#!/usr/bin/env python3
"""Núcleo compartido entre launcher.py y agentportable.py.

Una sola implementación de: detección de plataforma, catálogo de modelos
(descubrimiento + selección), localización del binario llama-server y
configuración persistente.

Cada entrypoint conserva su propio catálogo (MODEL_CATALOG), sus helpers
de color (err/warn/ok) y sus mensajes; aquí solo vive la lógica pura.
"""

import json
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# Contexto por defecto para .gguf fuera del catálogo (descubrimiento libre)
DEFAULT_MODEL_CTX = 16384

_LOOPBACK = frozenset({"127.0.0.1", "localhost"})


def host_policy_error(host: str) -> str | None:
    """Return a user-facing error when the configured bind is unsupported.

    ``ThreadingHTTPServer`` is IPv4-only (``AF_INET``), so accepting ``::1``
    would pass Host/Origin validation but fail at bind time. Reject it early
    with an actionable message instead of exposing a half-supported path.
    """
    if ":" in str(host or ""):
        return "IPv6 no soportada en rc.5. Usa 127.0.0.1 o localhost."
    return None

# Releases oficiales del fork PrismML (verificado: assets por plataforma)
BIN_RELEASE_REPO = "PrismML-Eng/llama.cpp"
BIN_DOCS_URL = "https://docs.prismml.com/run/llamacpp"

# Sufijo del asset de release por plataforma (CPU build)
BIN_ASSET_SUFFIX = {
    ("linux", "x86_64"): "ubuntu-x64",
    ("linux", "arm64"): "ubuntu-arm64",
    ("macos", "x86_64"): "macos-x64",
    ("macos", "arm64"): "macos-arm64",
    ("windows", "x86_64"): "win-cpu-x64",
    ("windows", "arm64"): "win-cpu-arm64",
}


def server_exe_name(os_key: str) -> str:
    return "llama-server.exe" if os_key == "windows" else "llama-server"


# ─── PLATAFORMA ───────────────────────────────────────────────────────────────


def detect_platform() -> tuple[str, str]:
    """Retorna (os_key, arch_key)"""
    sys_name = platform.system().lower()
    machine = platform.machine().lower()

    os_key = "linux"
    if sys_name == "windows":
        os_key = "windows"
    elif sys_name == "darwin":
        os_key = "macos"

    arch_key = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    return os_key, arch_key


def find_server_binary(
    bin_dir: Path, os_key: str, arch_key: str
) -> tuple[Path, bool]:
    """Localiza llama-server.

    Retorna (ruta, es_fallback) donde es_fallback=True significa que se
    usó el binario del sistema vía PATH en vez del empaquetado.
    Lanza LookupError si no hay nada — el mensaje lo decide el caller via `error`.
    """
    exe = server_exe_name(os_key)
    for candidate in (
        bin_dir / os_key / arch_key / exe,
        bin_dir / os_key / exe,
    ):
        if candidate.exists():
            return candidate, False

    sys_bin = shutil.which("llama-server")
    if sys_bin:
        return Path(sys_bin), True
    raise LookupError(f"llama-server no disponible para {os_key}/{arch_key}")


# ─── DESCARGA DEL BINARIO (fork PrismML, releases por plataforma) ────────────


def _asset_suffix(os_key: str, arch_key: str) -> str | None:
    return BIN_ASSET_SUFFIX.get((os_key, arch_key))


def _http_get(url: str, *, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Bonsai-Portable"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _download_stream(url: str, dest: Path, progress_cb=None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Bonsai-Portable"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress_cb:
                progress_cb(done, total)


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file incrementally without loading it into memory."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_sha256(path: Path, expected: str) -> str:
    """Fail closed unless ``path`` matches the expected SHA-256 digest."""
    normalized = (expected or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[len("sha256:"):]
    if len(normalized) != 64 or any(
        c not in "0123456789abcdef" for c in normalized
    ):
        raise RuntimeError(f"SHA-256 esperado inválido para {Path(path).name}")
    actual = file_sha256(path)
    if actual != normalized:
        raise RuntimeError(
            f"SHA-256 no coincide para {Path(path).name}: "
            f"esperado {normalized}, obtenido {actual}"
        )
    return actual


_ARCHIVE_MAX_MEMBERS = 1000
_ARCHIVE_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def _validate_member_name(name: str) -> tuple[str, ...]:
    """Reject names that can escape the staging directory or alias on Windows."""
    from pathlib import PurePosixPath

    if not name or "\x00" in name:
        raise RuntimeError("Archive contains an empty or NUL member name")
    if "\\" in name:
        raise RuntimeError(f"Unsafe archive member path: {name!r}")
    posix = PurePosixPath(name)
    if posix.is_absolute() or ".." in posix.parts:
        raise RuntimeError(f"Unsafe archive member path: {name!r}")
    for part in posix.parts:
        if not part or part == ".":
            raise RuntimeError(f"Unsafe archive member path: {name!r}")
        if ":" in part:
            raise RuntimeError(f"Unsafe archive member path: {name!r}")
        # Windows strips trailing dots/spaces and treats CON, NUL, COM1… as
        # devices. Rejecting them keeps extraction deterministic on every OS.
        if part != part.rstrip(" ."):
            raise RuntimeError(f"Unsafe archive member path: {name!r}")
        stem = part.split(".", 1)[0].lower()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise RuntimeError(f"Reserved archive member name: {name!r}")
    return tuple(posix.parts)


def _validate_zip_members(archive: zipfile.ZipFile) -> None:
    """Reject unsafe ZIP member paths, symlinks, duplicates, and bombs."""
    infos = archive.infolist()
    if len(infos) > _ARCHIVE_MAX_MEMBERS:
        raise RuntimeError("ZIP archive contains too many members")
    seen: set[str] = set()
    total_size = 0
    for info in infos:
        name = info.filename
        _validate_member_name(name)
        folded = name.casefold()
        if folded in seen:
            raise RuntimeError(f"Duplicate ZIP member path: {name!r}")
        seen.add(folded)
        total_size += max(0, int(info.file_size or 0))
        if total_size > _ARCHIVE_MAX_UNCOMPRESSED_BYTES:
            raise RuntimeError("ZIP archive exceeds the uncompressed size limit")
        if info.flag_bits & 0x1:
            raise RuntimeError(f"Encrypted ZIP members are not allowed: {name!r}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode in (0o120777, 0o120000):
            raise RuntimeError(f"Symlinks are not allowed in ZIP archives: {name!r}")
        if mode not in (0, 0o040000, 0o100000):
            raise RuntimeError(f"Special files are not allowed in ZIP archives: {name!r}")


def _validate_tar_members(archive: tarfile.TarFile) -> None:
    """Reject unsafe TAR members before any bytes are written to disk."""
    members = archive.getmembers()
    if len(members) > _ARCHIVE_MAX_MEMBERS:
        raise RuntimeError("TAR archive contains too many members")
    seen: set[str] = set()
    total_size = 0
    for member in members:
        name = member.name
        _validate_member_name(name)
        folded = name.casefold()
        if folded in seen:
            raise RuntimeError(f"Duplicate TAR member path: {name!r}")
        seen.add(folded)
        if not (member.isdir() or member.isreg()):
            kind = "symlink" if member.issym() else "hardlink" if member.islnk() else "special file"
            raise RuntimeError(f"{kind.capitalize()} entries are not allowed in TAR archives: {name!r}")
        total_size += max(0, int(member.size or 0))
        if total_size > _ARCHIVE_MAX_UNCOMPRESSED_BYTES:
            raise RuntimeError("TAR archive exceeds the uncompressed size limit")


def _extract_zip(archive: Path, stage: Path) -> None:
    """Extract a ZIP member-by-member without trusting ``extractall``."""
    with zipfile.ZipFile(archive) as zf:
        _validate_zip_members(zf)
        root = stage.resolve()
        for info in zf.infolist():
            target = (stage / info.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"ZIP member escapes staging: {info.filename!r}")
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, open(target, "xb") as destination:
                shutil.copyfileobj(source, destination, length=256 * 1024)


def _extract_tar(archive: Path, stage: Path) -> None:
    """Extract a TAR member-by-member, rejecting links and special files."""
    with tarfile.open(archive, "r:*") as tf:
        _validate_tar_members(tf)
        root = stage.resolve()
        for member in tf.getmembers():
            target = (stage / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"TAR member escapes staging: {member.name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:
                raise RuntimeError(f"TAR member has no data: {member.name!r}")
            with source, open(target, "xb") as destination:
                shutil.copyfileobj(source, destination, length=256 * 1024)
            try:
                os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
            except OSError:
                pass


def _extract_archive(archive: Path, exe_name: str, dest_dir: Path) -> None:
    """Extrae el release completo y deja el binario directamente en dest_dir.

    Los assets PrismML desempacan en una carpeta versionada (`llama-prism-*/`)
    que incluye el binario y sus librerías compartidas (resueltas vía $ORIGIN).
    Se extrae todo y se "iza" el contenido de esa carpeta a dest_dir para que
    la ruta esperada (`dest_dir/<exe>`) funcione con todos los .so hermanos.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".bonsai-stage-", dir=str(dest_dir.parent)))
    try:
        if archive.suffix == ".zip":
            _extract_zip(archive, stage)
        else:
            _extract_tar(archive, stage)

        exe = next(
            (
                candidate
                for candidate in stage.rglob(exe_name)
                if candidate.is_file() and not candidate.is_symlink()
            ),
            None,
        )
        if not exe:
            raise RuntimeError(f"{exe_name} no encontrado dentro de {archive.name}")

        src_dir = exe.parent
        for item in sorted(src_dir.iterdir()):
            target = dest_dir / item.name
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            shutil.move(str(item), str(target))
        exe_dest = dest_dir / exe_name
        if os.name != "nt":
            exe_dest.chmod(0o755)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def pick_release_asset(
    releases: list[dict], os_key: str, arch_key: str
) -> dict | None:
    """Primer asset compatible (truncate más nuevo release primero)."""
    suffix = _asset_suffix(os_key, arch_key)
    if not suffix:
        return None
    for rel in releases:
        for a in rel.get("assets", []):
            name = a.get("name", "")
            ok_name = name.startswith("llama-") and (
                name.endswith(f"-bin-{suffix}.tar.gz")
                or name.endswith(f"-bin-{suffix}.zip")
            )
            if ok_name:
                return a
    return None


def download_server_binary(
    bin_dir: Path,
    os_key: str,
    arch_key: str,
    progress_cb=None,
) -> Path:
    """Descarga e instala llama-server desde el release oficial del fork PrismML.

    Verificación obligatoria: se usa el ``digest`` SHA-256 publicado por el
    release de GitHub. ``BONSAI_BIN_SHA256`` puede fijar otro hash explícito.
    Si no hay ninguno, la descarga falla cerrada.
    """
    suffix = _asset_suffix(os_key, arch_key)
    if not suffix:
        raise RuntimeError(
            f"Plataforma {os_key}/{arch_key} sin binario publicado "
            f"(ver {BIN_DOCS_URL})"
        )

    # 1) último release con asset para esta plataforma
    api = f"https://api.github.com/repos/{BIN_RELEASE_REPO}/releases?per_page=10"
    releases = json.loads(_http_get(api).decode("utf-8"))
    asset = pick_release_asset(releases, os_key, arch_key)
    if not asset:
        raise RuntimeError(
            f"Ningún release publicado contiene binarios para {os_key}/{arch_key} "
            f"({BIN_DOCS_URL})"
        )

    expected = os.environ.get("BONSAI_BIN_SHA256", "").strip().lower() or (
        str(asset.get("digest") or "").strip().lower()
    )

    # 2) descarga a temp + verificación + extracción
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / asset["name"]
        _download_stream(asset["browser_download_url"], tmp, progress_cb)
        if not expected:
            raise RuntimeError(
                f"El release no publica SHA-256 para {asset['name']}; "
                "define BONSAI_BIN_SHA256 para verificar la descarga"
            )
        verify_file_sha256(tmp, expected)
        exe = server_exe_name(os_key)
        dest_dir = bin_dir / os_key / arch_key
        _extract_archive(tmp, exe, dest_dir)
        dest = dest_dir / exe
    return dest


# ─── MODELOS: descubrimiento y selección ─────────────────────────────────────


def _custom_meta(path: Path) -> dict:
    return {
        "file": path.name,
        "name": path.name,
        "profile": "general",
        "ctx": DEFAULT_MODEL_CTX,
    }


def find_available_models(
    models_dir: Path, catalog: dict
) -> list[tuple[str, dict]]:
    """Catálogo oficial presente en models_dir + cualquier otro .gguf ahí."""
    found: list[tuple[str, dict]] = []
    seen: set[Path] = set()
    for key, meta in catalog.items():
        path = models_dir / meta["file"]
        if path.exists():
            found.append((key, meta))
            seen.add(path.resolve())

    if models_dir.exists():
        catalog_files = {meta["file"] for _, meta in found}
        for path in sorted(models_dir.glob("*.gguf")):
            if not path.is_file():
                continue
            if path.name in catalog_files or path.resolve() in seen:
                continue
            found.append((f"custom:{path.stem}", _custom_meta(path)))
    return found


def pick_model(
    requested: str | None,
    *,
    models_dir: Path,
    catalog: dict,
    error,  # callable(msg) -> NoReturn (p.ej. err de cada entrypoint)
    warn,  # callable(msg) -> None
    no_models_msg: str,
) -> tuple[str, dict, Path]:
    """Resuelve el modelo a usar.

    Orden de resolución:
    1. Ruta explícita a un .gguf existente (aunque models_dir esté vacío)
    2. key del catálogo (case-insensitive) o nombre de archivo en models_dir
    3. Más capaz disponible (orden del catálogo)
    4. Primero disponible + warn
    """
    # 1) Ruta explícita a un .gguf existente: se acepta aunque /models esté vacío
    if requested:
        cand = Path(requested).expanduser()
        if cand.suffix.lower() == ".gguf" and cand.is_file():
            return f"custom:{cand.stem}", _custom_meta(cand), cand

    available = find_available_models(models_dir, catalog)
    if not available:
        error(no_models_msg)

    if requested:
        requested_l = requested.lower()
        # 2) key del catálogo o nombre de archivo en /models
        for key, meta in available:
            if requested_l in (key.lower(), meta["file"].lower()):
                return key, meta, models_dir / meta["file"]
        warn(f"Modelo '{requested}' no encontrado. Usando el primero disponible.")

    # 3) Preferir el más capaz (orden de declaración del catálogo)
    for preferred in catalog:
        for key, meta in available:
            if key == preferred:
                return key, meta, models_dir / meta["file"]

    key, meta = available[0]
    return key, meta, models_dir / meta["file"]


# ─── CONFIGURACIÓN PERSISTENTE ───────────────────────────────────────────────


def config_file(default: Path) -> Path:
    """config.json del proyecto, con override opcional vía BONSAI_CONFIG."""
    override = os.environ.get("BONSAI_CONFIG")
    return Path(override).expanduser() if override else default


def load_config(cfg_file: Path) -> dict:
    defaults = {
        "port": 8080,
        "host": "127.0.0.1",
        "ngl": -1,
        "threads": 0,
        "open_browser": True,
        "last_model": None,
        "rag_enabled": False,
        "rag_max_chars": 6000,
        "rag_top_k": 3,
        "experience_persistence": False,
        "agents_md_enabled": True,
        "skills_enabled": True,
        "mcp_enabled": False,
        "mcp_servers": [],
        "hooks": {},
        "tool_permissions": {},
        "plugins_enabled": False,
        "plugin_paths": [],
        "plugins_enable_mcp": False,
        "subagents_enabled": False,
        "yubikey_auto": False,
        "llm_port_offset": 100,
        "max_iterations": 5,
        "max_tool_calls": 16,
        "max_runtime_seconds": 300,
    }
    if cfg_file.exists():
        try:
            saved = json.loads(cfg_file.read_text())
            defaults.update(saved)
        except Exception:
            pass
    # Bind policy: non-loopback requires BONSAI_ALLOW_LAN=1
    host = str(defaults.get("host") or "127.0.0.1")
    if ":" in host:
        # IPv6 is not supported by the stdlib server used in rc.5.
        defaults["_ipv6_requested"] = host
    allow_lan = os.environ.get("BONSAI_ALLOW_LAN", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if host not in _LOOPBACK and not allow_lan:
        # Fail closed to loopback
        defaults["host"] = "127.0.0.1"
        defaults["_host_clamped"] = True
        defaults["_requested_host"] = host
    return defaults


def save_config(cfg_file: Path, cfg: dict):
    cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
