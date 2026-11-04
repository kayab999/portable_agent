#!/usr/bin/env python3
"""
Bonsai Portable — Launcher Universal
Detecta OS/arch, selecciona el binario correcto, inicia llama-server
y abre la interfaz web. Funciona en Linux, Windows y macOS.
"""

import os
import sys
import json
import time
import signal
import subprocess
import webbrowser
import argparse
from pathlib import Path

import runtime_core

# ─── COLORES ANSI (funciona en todos los terminales modernos) ─────────────────
R = "\033[0;31m"
G = "\033[0;32m"
Y = "\033[1;33m"
C = "\033[0;36m"
B = "\033[1;34m"
M = "\033[0;35m"
W = "\033[1;37m"
NC = "\033[0m"
BOLD = "\033[1m"

# ACP owns stdout for JSON-RPC frames; all human diagnostics go to stderr.
_ACP_MODE = "--acp" in sys.argv[1:]
_LOG_FILE = sys.stderr if _ACP_MODE else sys.stdout


def p(msg, color=W):
    print(f"{color}{msg}{NC}", file=_LOG_FILE)


def ok(msg):
    print(f"{G}  ✓  {NC}{msg}", file=_LOG_FILE)


def err(msg):
    print(f"{R}  ✗  {NC}{msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"{Y}  !  {NC}{msg}", file=_LOG_FILE)


def info(msg):
    print(f"{C}  ·  {NC}{msg}", file=_LOG_FILE)


BANNER = f"""
{B}{BOLD}
  ╔══════════════════════════════════════╗
  ║   🌿 Bonsai Portable AI Runtime     ║
  ║   PrismML 1-bit · Sin instalación   ║
  ╚══════════════════════════════════════╝
{NC}"""

# ─── PATHS ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
BIN_DIR = ROOT / "bin"
MODELS = ROOT / "models"
WEB_DIR = ROOT / "web"
CFG_FILE = runtime_core.config_file(ROOT / "config.json")

# ─── MODO DESARROLLO ─────────────────────────────────────────────────────────
# RC default: secure. Dev override: BONSAI_DEBUG=1
import os as _os_for_debug

DEBUG_MODE = _os_for_debug.environ.get("BONSAI_DEBUG", "0") in ("1", "true", "True", "yes")
INSECURE_MODE = _os_for_debug.environ.get("BONSAI_INSECURE", "0") in (
    "1",
    "true",
    "True",
    "yes",
)
# Mientras DEBUG_MODE=True:
#   • Servidor arranca unlocked sin YubiKey ni password
#   • RAG funciona sin encriptación
#   • UI accesible sin restricciones
# Producción RC: fail-closed. Requiere vault creado con --set-password.
# Bypass explícito: BONSAI_INSECURE=1 (solo desarrollo, se anuncia en consola).

# ─── RATE LIMITING (import if available) ──────────────────────────────────────
try:
    from rate_limit import download_with_resume, get_limiter

    _HAS_RATE_LIMIT = True
except ImportError:
    _HAS_RATE_LIMIT = False

# ─── TOOLBOX (herramientas reales del agente) ────────────────────────────────
try:
    from tools import get_toolbox

    _HAS_TOOLS = True
except ImportError:
    _HAS_TOOLS = False

# ─── YUBIKEY HARDWARE TOKEN (challenge-response) ──────────────────────────────
YUBIKEY_CHALLENGE = "Kayab-Node-Unlock"

# Unlock state kept ONLY in RAM — never written to disk.
# "locked" | "yubikey" | "insecure"
_unlock_source = "locked"


def get_vault():
    """Return the owner vault for this installation."""
    from session_auth import Vault

    root = Path(os.environ.get("BONSAI_VAULT_ROOT", str(ROOT))).expanduser()
    return Vault(root)


def try_yubikey_unlock() -> str | None:
    """Attempt YubiKey challenge-response to get auto-unlock password.

    Calls: ykchalresp -2 'Kayab-Node-Unlock'
    Returns the response string if successful, None if failed.
    """
    try:
        result = subprocess.run(
            ["ykchalresp", "-2", YUBIKEY_CHALLENGE],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            response = result.stdout.strip()
            info("YubiKey: unlocked via hardware token")
            return response
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return None


def unlock_with_yubikey(password: str) -> bool:
    """Unlock only when the YubiKey response verifies against the owner vault."""
    global _unlock_source
    if not password:
        return False
    if not get_vault().verify(password):
        return False
    _unlock_source = "yubikey"
    return True


def set_insecure_mode() -> None:
    """Explicit development/insecure mode. Never used silently in production."""
    global _unlock_source
    _unlock_source = "insecure"


def is_server_unlocked() -> bool:
    """True only for a verified hardware unlock or an explicit insecure mode."""
    return _unlock_source in ("yubikey", "insecure")


def unlock_source() -> str:
    return _unlock_source




# ─── MODELOS DISPONIBLES ──────────────────────────────────────────────────────
MODEL_CATALOG = {
    "bonsai-8b": {
        "file": "Bonsai-8B.gguf",
        "name": "Bonsai 8B (1.15 GB)",
        "profile": "thesis",
        "ctx": 65536,
        "hf_url": "https://huggingface.co/prism-ml/Bonsai-8B-gguf/resolve/main/Bonsai-8B.gguf",
    },
    "bonsai-4b": {
        "file": "Bonsai-4B.gguf",
        "name": "Bonsai 4B (0.5 GB)",
        "profile": "general",
        "ctx": 32768,
        "hf_url": "https://huggingface.co/prism-ml/Bonsai-4B-gguf/resolve/main/Bonsai-4B.gguf",
    },
    "bonsai-1.7b": {
        "file": "Bonsai-1.7B.gguf",
        "name": "Bonsai 1.7B (0.24 GB)",
        "profile": "quick",
        "ctx": 16384,
        "hf_url": "https://huggingface.co/prism-ml/Bonsai-1.7B-gguf/resolve/main/Bonsai-1.7B.gguf",
    },
}

# Contexto por defecto para .gguf fuera del catálogo (descubrimiento libre)
DEFAULT_MODEL_CTX = runtime_core.DEFAULT_MODEL_CTX

# ─── DETECCIÓN DE PLATAFORMA ──────────────────────────────────────────────────


def detect_platform() -> tuple[str, str]:
    """Retorna (os_key, arch_key) — implementación compartida en runtime_core."""
    return runtime_core.detect_platform()


def get_server_binary(os_key: str, arch_key: str) -> Path:
    """Encuentra el binario llama-server para esta plataforma."""
    try:
        found, is_fallback = runtime_core.find_server_binary(BIN_DIR, os_key, arch_key)
    except LookupError:
        err(
            f"No se encontró llama-server para {os_key}/{arch_key}.\n"
            f"  Ejecuta: python launcher.py --setup\n"
            f"  O descarga el asset llama-*-bin-*.tar.gz desde: "
            f"https://github.com/{runtime_core.BIN_RELEASE_REPO}/releases"
        )
    if is_fallback:
        warn(f"Binario local no encontrado. Usando sistema: {found}")
    return found


# ─── SELECCIÓN DE MODELO ──────────────────────────────────────────────────────


def find_available_models() -> list[tuple[str, dict]]:
    """Catálogo oficial + cualquier .gguf en /models (runtime_core)."""
    return runtime_core.find_available_models(MODELS, MODEL_CATALOG)


def pick_model(requested: str | None) -> tuple[str, dict, Path]:
    """Resuelve el modelo a usar (runtime_core, mensajes propios)."""
    return runtime_core.pick_model(
        requested,
        models_dir=MODELS,
        catalog=MODEL_CATALOG,
        error=err,
        warn=warn,
        no_models_msg=(
            "No hay modelos descargados.\n"
            "  Ejecuta: python launcher.py --setup\n"
            "  O copia un .gguf a la carpeta /models/"
        ),
    )


# ─── CARGA DE CONFIGURACIÓN ───────────────────────────────────────────────────


def security_active() -> bool:
    """True only when the owner vault exists and no explicit bypass is active."""
    return (
        not DEBUG_MODE
        and _unlock_source != "insecure"
        and get_vault().exists()
    )


def load_config() -> dict:
    """Config persistente + política de bind (runtime_core)."""
    return runtime_core.load_config(CFG_FILE)


def save_config(cfg: dict):
    runtime_core.save_config(CFG_FILE, cfg)


# ─── SERVIDOR ─────────────────────────────────────────────────────────────────


def wait_for_server(host: str, port: int, timeout: int = 30) -> bool:
    import urllib.request

    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def build_server_cmd(
    binary: Path, model_path: Path, meta: dict, cfg: dict
) -> list[str]:
    cmd = [
        str(binary),
        "-m",
        str(model_path),
        "--host",
        cfg["host"],
        "--port",
        str(cfg["port"]),
        "--ctx-size",
        str(meta["ctx"]),
        "--log-disable",  # menos ruido en terminal
    ]
    if cfg.get("ngl", 0) != 0:
        cmd += ["-ngl", str(cfg["ngl"])]
    if cfg.get("threads", 0) > 0:
        cmd += ["-t", str(cfg["threads"])]
    return cmd


# ─── CLI INTERACTIVA (modo sin web) ──────────────────────────────────────────


def run_cli_mode(host: str, port: int, meta: dict, rag: bool = False):
    """Cliente CLI minimalista: usa /api/chat del gateway (auth + persona + sandbox)."""
    import urllib.request
    import urllib.error

    base_url = f"http://{host}:{port}"
    history: list[dict] = []
    session_token: str | None = None

    def _unlock() -> str | None:
        nonlocal session_token

        def _post(payload: dict) -> dict:
            req = urllib.request.Request(
                f"{base_url}/api/session/unlock",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

        try:
            data = _post({})
            session_token = data.get("token")
            return session_token
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            try:
                err = json.loads(body)
            except json.JSONDecodeError:
                err = {}
            if e.code == 429:
                warn("Rate limit en unlock — espera unos segundos.")
                return None
            if e.code != 403:
                warn(f"Session unlock: HTTP {e.code} {body}")
                return None
            if err.get("error") == "not_provisioned":
                warn(
                    "Servidor bloqueado sin vault. Ejecuta: "
                    "python launcher.py --set-password"
                )
                return None
            if not sys.stdin.isatty():
                warn(
                    "Servidor bloqueado. Desbloquea desde la UI o usa "
                    "BONSAI_INSECURE=1 (solo desarrollo)."
                )
                return None
            import getpass

            try:
                password = getpass.getpass("  Contraseña del vault: ")
            except (EOFError, KeyboardInterrupt):
                return None
            if not password:
                return None
            try:
                data = _post({"password": password})
            except urllib.error.HTTPError as e2:
                if e2.code == 429:
                    warn("Rate limit en unlock — espera unos segundos.")
                elif e2.code == 403:
                    warn("Contraseña incorrecta.")
                else:
                    warn(f"Session unlock: HTTP {e2.code}")
                return None
            session_token = data.get("token")
            if session_token:
                ok("Sesión desbloqueada.")
            return session_token
        except Exception as e:
            warn(f"Session unlock: {e}")
            return None

    _unlock()

    p("\n  Bonsai CLI · escribe 'salir' para terminar, 'reset' para nueva sesión\n", C)
    p("  Canal: /api/chat (auth + persona allowlist + sandbox)\n", G)

    def _auth_headers() -> dict:
        h = {"Content-Type": "application/json"}
        if session_token:
            h["Authorization"] = f"Bearer {session_token}"
            h["X-Bonsai-Token"] = session_token
        return h

    def _send_chat(user_text: str) -> str | None:
        nonlocal session_token
        payload = json.dumps(
            {
                "message": user_text,
                "messages": history,
                "rag": bool(rag),
            }
        ).encode()
        try:
            req = urllib.request.Request(
                f"{base_url}/api/chat",
                data=payload,
                headers=_auth_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            if data.get("messages"):
                # Keep last turns only
                return data.get("reply") or ""
            return data.get("reply") or data.get("error")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                _unlock()
                warn("Reautenticando…")
                return None
            if e.code == 429:
                warn("Rate limit — espera unos segundos…")
                time.sleep(3)
                return None
            body = e.read().decode("utf-8", errors="replace")[:300]
            warn(f"Error HTTP {e.code}: {body}")
            return None
        except Exception as e:
            warn(f"Error: {e}")
            return None

    while True:
        try:
            user_input = input(f"{C}  Tú ▶ {NC}").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            break
        if user_input.lower() == "reset":
            history = []
            ok("Sesión reiniciada.")
            continue
        if user_input.lower() == "tools":
            if _HAS_TOOLS:
                tb = get_toolbox()
                d = tb.diagnose()
                print(f"  Workspace: {d['workspace']} ({d['workspace_size']})")
                print(f"  Herramientas: {', '.join(d['tools_available'])}")
                print(f"  Power tools: {d.get('power_tools')}")
            else:
                warn("Herramientas no disponibles.")
            continue

        reply = _send_chat(user_input)
        if reply is None:
            continue
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": reply})
        # Keep history bounded
        if len(history) > 40:
            history = history[-40:]
        print(f"\n{B}  φ ▶{NC} {reply}\n")


# ─── SETUP (descarga binarios y modelos) ──────────────────────────────────────


def run_setup(os_key: str, arch_key: str):
    p("\n  Bonsai Portable — Setup\n", B)

    # Mostrar modelos disponibles
    info("Modelos disponibles para descarga:")
    for i, meta in enumerate(MODEL_CATALOG.values(), 1):
        status = "✓" if (MODELS / meta["file"]).exists() else " "
        print(f"    [{status}] {i}) {meta['name']}")

    print("\n  Selecciona modelos a descargar (ej: 1 2 3 o 'todos'):")
    choice = input("  > ").strip().lower()

    keys = list(MODEL_CATALOG.keys())
    if choice == "todos":
        to_download = list(MODEL_CATALOG.items())
    else:
        indices = [int(x) - 1 for x in choice.split() if x.isdigit()]
        to_download = [
            (keys[i], MODEL_CATALOG[keys[i]]) for i in indices if 0 <= i < len(keys)
        ]

    for _key, meta in to_download:
        dest = MODELS / meta["file"]
        if dest.exists():
            ok(f"{meta['name']} — ya descargado.")
            continue
        info(f"Descargando {meta['name']}...")
        _download_with_rate_limit(meta["hf_url"], dest)
        if len(to_download) > 1:
            time.sleep(3)

    # Binario llama-server
    bin_dest = (
        BIN_DIR
        / os_key
        / arch_key
        / runtime_core.server_exe_name(os_key)
    )
    if not bin_dest.exists():
        info(
            f"Descargando binario llama-server (PrismML) para {os_key}/{arch_key}..."
        )
        try:
            runtime_core.download_server_binary(BIN_DIR, os_key, arch_key)
            ok(f"Binario llama-server: {bin_dest}")
        except Exception as e:
            warn(f"Descarga automática falló: {e}")
            p("\n  Descarga el binario manualmente (fork PrismML):", Y)
            print(
                f"\n    https://github.com/{runtime_core.BIN_RELEASE_REPO}/releases\n"
            )
            print(f"  Asset llama-*-bin-{runtime_core._asset_suffix(os_key, arch_key)}.tar.gz")
            print(f"  y colócalo en: {bin_dest.parent}/\n")
            print(f"  Referencia: {runtime_core.BIN_DOCS_URL}")
    else:
        ok(f"Binario llama-server: {bin_dest}")

    ok("Setup completado.")


def _download_with_rate_limit(url: str, dest: Path):
    if _HAS_RATE_LIMIT:
        limiter = get_limiter()

        def progress(downloaded, total):
            pct = min(100, int(downloaded * 100 / total)) if total else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r    [{bar}] {pct}%", end="", flush=True)

        limiter.wait_and_allow()
        success = download_with_resume(url, dest, progress_cb=progress)
        print()
        if success:
            ok(f"Guardado: {dest.name}")
        else:
            warn(f"Falló descarga: {dest.name}")
    else:
        _download(url, dest)


def _download(url: str, dest: Path):
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:

        def progress(block, block_size, total):
            done = block * block_size
            pct = min(100, int(done * 100 / total)) if total > 0 else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r    [{bar}] {pct}%", end="", flush=True)

        urllib.request.urlretrieve(url, dest, reporthook=progress)
        print()
        ok(f"Guardado: {dest.name}")
    except Exception as e:
        err(f"Error descargando {url}: {e}")


# ─── OWNER VAULT PROVISIONING ─────────────────────────────────────────────────


def provision_vault(force: bool = False) -> None:
    """Create or rotate the owner password vault interactively."""
    import getpass

    from session_auth import VaultError

    vault = get_vault()
    if vault.exists() and not force:
        current = getpass.getpass("  Current password: ")
        if not vault.verify(current):
            err("Contraseña actual incorrecta. No se modificó el vault.")
        # Verified in place: allow the provision call to replace the file.
        force = True

    password = getpass.getpass("  New password (min 12 chars): ")
    confirm = getpass.getpass("  Confirm new password: ")
    if password != confirm:
        err("Las contraseñas no coinciden.")
    try:
        vault.provision(password, force=force)
    except VaultError as e:
        err(str(e))

    ok(f"Vault creado: {vault.owner_lock} (single file, 0600)")
    info(f"KDF:    PBKDF2-HMAC-SHA256 x {vault.metadata().get('iterations', '?')} iteraciones")
    info("Inicia sesión desde la UI o con POST /api/session/unlock.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    print(BANNER, file=_LOG_FILE)
    os_key, arch_key = detect_platform()
    cfg = load_config()

    # Fail fast on bind addresses the stdlib server cannot serve. This runs
    # before llama-server so the user gets one clear message, not a traceback.
    policy_error = runtime_core.host_policy_error(
        cfg.get("_ipv6_requested") or cfg["host"]
    )
    if policy_error:
        err(policy_error)

    parser = argparse.ArgumentParser(description="Bonsai Portable Launcher")
    parser.add_argument(
        "--model",
        "-m",
        help=(
            "Modelo a usar: key del catálogo (bonsai-8b, bonsai-4b, bonsai-1.7b), "
            "custom:<nombre>, nombre de archivo en /models, o ruta a un .gguf"
        ),
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=cfg["port"],
        help=f"Puerto HTTP (default: {cfg['port']})",
    )
    parser.add_argument(
        "--cli", action="store_true", help="Solo CLI, sin abrir navegador"
    )
    parser.add_argument(
        "--acp",
        action="store_true",
        help="Servir Agent Client Protocol (JSON-RPC) por stdin/stdout",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Iniciar servidor sin abrir navegador"
    )
    parser.add_argument(
        "--setup", action="store_true", help="Descargar modelos y binarios"
    )
    parser.add_argument(
        "--list-models", action="store_true", help="Listar modelos disponibles"
    )
    parser.add_argument(
        "--ngl", type=int, default=cfg.get("ngl", -1), help="Capas GPU (-1=auto, 0=CPU)"
    )
    parser.add_argument(
        "--yubikey",
        action="store_true",
        help="Intentar desbloqueo con YubiKey (requiere vault configurado)",
    )
    parser.add_argument(
        "--set-password",
        action="store_true",
        help="Crear o rotar el vault del propietario (owner.lock + salt + canary)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Permitir rotar el vault sin la contraseña actual (recuperación)",
    )
    args = parser.parse_args()

    if args.set_password:
        provision_vault(force=args.force)
        return

    # Fail closed: production starts locked unless a verified credential or an
    # explicit bypass is present. Never mark the server unlocked without one.
    if DEBUG_MODE:
        if get_vault().exists():
            warn("owner.lock encontrado pero BONSAI_DEBUG=1 — seguridad deshabilitada.")
        set_insecure_mode()
    elif INSECURE_MODE:
        warn("BONSAI_INSECURE=1 — autenticación deshabilitada (solo desarrollo).")
        set_insecure_mode()
    elif args.yubikey or cfg.get("yubikey_auto", False):
        vault = get_vault()
        if not vault.exists():
            warn("YubiKey solicitado pero no hay vault. Ejecuta: python launcher.py --set-password")
        else:
            yubikey_password = try_yubikey_unlock()
            if not yubikey_password or not unlock_with_yubikey(yubikey_password):
                warn("YubiKey no verificó contra el vault — servidor bloqueado.")

    if args.setup:
        run_setup(os_key, arch_key)
        return

    if args.list_models:
        available = find_available_models()
        p("  Modelos instalados:", C)
        for key, meta in available:
            size = (MODELS / meta["file"]).stat().st_size / (1024**3)
            print(f"    {key:15s} — {meta['name']:25s} ({size:.2f} GB)")
        if not available:
            warn("Ninguno. Ejecuta: python launcher.py --setup")
        return

    # Seleccionar modelo y binario
    model_key, model_meta, model_path = pick_model(args.model)
    binary = get_server_binary(os_key, arch_key)

    # Actualizar config
    cfg["port"] = args.port
    cfg["ngl"] = args.ngl
    cfg["last_model"] = model_key
    save_config(cfg)

    gateway_port = int(cfg["port"])
    llm_port = gateway_port + int(cfg.get("llm_port_offset", 100))
    llm_cfg = dict(cfg)
    llm_cfg["port"] = llm_port
    # llama-server always on localhost; gateway binds to cfg host
    llm_cfg["host"] = "127.0.0.1"

    ok(f"Plataforma: {os_key}/{arch_key}")
    ok(f"Modelo:     {model_meta['name']}")
    ok(f"Binario:    {binary.name}")
    ok(f"Gateway:    http://{cfg['host']}:{gateway_port}")
    ok(f"LLM (int):  http://127.0.0.1:{llm_port}")
    if DEBUG_MODE:
        p("  ⚠ MODO DESARROLLO — seguridad deshabilitada (BONSAI_DEBUG=1)", Y)
    elif _unlock_source == "insecure":
        p("  ⚠ MODO INSEGURO — autenticación deshabilitada (BONSAI_INSECURE=1)", Y)
    elif is_server_unlocked():
        ok("Desbloqueado: YubiKey")
    elif get_vault().exists():
        info("Modo: bloqueado (la web UI pide contraseña)")
    else:
        warn("Modo: bloqueado — no hay vault. Ejecuta: python launcher.py --set-password")
    print(file=_LOG_FILE)

    # Iniciar llama-server on internal port
    cmd = build_server_cmd(binary, model_path, model_meta, llm_cfg)
    info("Iniciando llama-server...")

    srv_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    gateway_server = None
    tool_registry = None

    def shutdown(sig=None, frame=None):
        info("\nApagando servidores...")
        if tool_registry is not None:
            try:
                tool_registry.close()
            except Exception:
                pass
        if gateway_server is not None:
            try:
                gateway_server.shutdown()
            except Exception:
                pass
        try:
            srv_proc.terminate()
            try:
                srv_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv_proc.kill()
                try:
                    srv_proc.wait(timeout=3)  # reapear (evita zombi)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        # Salida determinista: no depender de hilos que puedan quedar colgados
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    # Esperar a que el LLM esté listo
    if not wait_for_server("127.0.0.1", llm_port, timeout=45):
        # Stop the child before draining stderr; reading a live pipe can block
        # forever if llama-server is hung instead of exiting.
        try:
            srv_proc.terminate()
            srv_proc.wait(timeout=3)
        except Exception:
            srv_proc.kill()
        err_output = srv_proc.stderr.read() if srv_proc.stderr else ""
        err(f"llama-server no respondió en 45s.\n{err_output[:500]}")

    ok("llama-server listo.")

    # Gateway: single origin for UI + /api + /v1 proxy
    from gateway import GatewayState, start_gateway
    from session_auth import SessionManager

    if cfg.get("_host_clamped"):
        warn(
            f"host {cfg.get('_requested_host')!r} no permitido sin BONSAI_ALLOW_LAN=1 "
            f"— bind en {cfg['host']}"
        )
    elif cfg["host"] not in ("127.0.0.1", "localhost"):
        warn(
            "LAN no soportada en rc.5: la validación de Host/Origin rechazará "
            "clientes que no sean loopback."
        )

    sessions = SessionManager()
    vault = get_vault()

    mcp_configs = list(cfg.get("mcp_servers") or [])
    plugin_agent_roots: list[Path] = []
    if cfg.get("plugins_enabled"):
        try:
            from plugin_adapters import load_plugins, merge_mcp_servers, skill_roots
            from skills import configure_skill_roots

            plugins, plugin_errors = load_plugins(cfg.get("plugin_paths") or [])
            for item in plugin_errors:
                warn(f"Plugin {item.get('path')}: {item.get('error')}")
            configure_skill_roots(skill_roots(plugins))
            plugin_agent_roots = [
                agent for plugin in plugins for agent in plugin.agents
            ]
            mcp_configs.extend(
                merge_mcp_servers(
                    plugins, enable=bool(cfg.get("plugins_enable_mcp", False))
                )
            )
            if plugins:
                info(
                    "Plugins: "
                    + ", ".join(
                        f"{plugin.name} ({plugin.format}, "
                        f"{len(plugin.skills)} skills, "
                        f"{len(plugin.mcp_servers)} MCP)"
                        for plugin in plugins
                    )
                )
        except Exception as e:
            warn(f"Plugins deshabilitados por error: {e}")

    subagent_index = None
    if cfg.get("subagents_enabled", False):
        try:
            from subagents import SubagentIndex

            subagent_index = SubagentIndex.load(
                ROOT, extra_roots=plugin_agent_roots
            )
            for item in subagent_index.errors:
                warn(f"Subagente {item.get('path')}: {item.get('error')}")
            if subagent_index.agents:
                info(f"Subagentes: {', '.join(sorted(subagent_index.agents))}")
        except Exception as e:
            warn(f"Subagentes deshabilitados por error: {e}")

    tool_registry = None
    if cfg.get("mcp_enabled"):
        try:
            from mcp_client import MCPManager
            from tool_registry import ToolRegistry
            from tools import get_toolbox

            mcp_manager = MCPManager.from_config(mcp_configs).start_all()
            for item in mcp_manager.errors:
                warn(f"MCP {item.get('name')}: {item.get('error')}")
            if mcp_manager.servers:
                ok(
                    "MCP: "
                    + ", ".join(
                        f"{name} ({len(server.tools)} tools)"
                        for name, server in mcp_manager.servers.items()
                    )
                )
            tool_registry = ToolRegistry(get_toolbox(), mcp_manager)
        except Exception as e:
            warn(f"MCP deshabilitado por error: {e}")

    try:
        from hooks import HookManager
        from permissions import PermissionPolicy

        hooks = HookManager.from_config(cfg.get("hooks"), cwd=ROOT)
        permissions = PermissionPolicy.from_config(cfg.get("tool_permissions"))
        if hooks.enabled:
            active = sum(len(items) for items in hooks.specs.values())
            info(f"Hooks: {active} declarados")
    except Exception as e:
        warn(f"Hooks deshabilitados por error: {e}")
        hooks = None
        permissions = None

    if args.acp:
        from acp_server import AcpServer, _read_version, make_turn_runner
        from gateway import _HttpModelClient
        from tools import get_toolbox

        model_name = model_meta.get("name", model_key)
        runner = make_turn_runner(
            root=ROOT,
            config=cfg,
            model_client=_HttpModelClient(
                f"http://127.0.0.1:{llm_port}", model=model_name
            ),
            model=model_name,
            toolbox=get_toolbox(),
            registry=tool_registry,
            hooks=hooks,
            permissions=permissions,
            max_iterations=min(int(cfg.get("max_iterations", 5)), 8),
        )
        acp = AcpServer(
            runner,
            hooks=hooks,
            permissions=permissions,
            agent_version=_read_version(ROOT),
        )
        info("ACP: JSON-RPC por stdin/stdout (Ctrl+D para salir).")
        try:
            acp.serve(sys.stdin, sys.stdout)
        finally:
            shutdown()
        return

    gstate = GatewayState(
        llm_base=f"http://127.0.0.1:{llm_port}",
        debug_mode=DEBUG_MODE,
        is_unlocked=is_server_unlocked,
        rag_enabled=bool(cfg.get("rag_enabled", False)),
        persistence_enabled=bool(cfg.get("experience_persistence", False)),
        vault=vault,
        insecure_mode=(_unlock_source == "insecure"),
        model_name=model_meta.get("name", model_key),
        host=cfg["host"],
        port=gateway_port,
        sessions=sessions,
        auth_required=not DEBUG_MODE,
        allow_direct_tools=DEBUG_MODE,
        agents_md_enabled=bool(cfg.get("agents_md_enabled", True)),
        skills_enabled=bool(cfg.get("skills_enabled", True)),
        mcp_enabled=bool(cfg.get("mcp_enabled", False)),
        tool_registry=tool_registry,
        hooks=hooks,
        permissions=permissions,
        subagents_enabled=bool(cfg.get("subagents_enabled", False)),
        subagent_index=subagent_index,
    )
    try:
        gateway_server = start_gateway(gstate, background=True)
        ok(f"Gateway listo en http://{cfg['host']}:{gateway_port}")
        if not DEBUG_MODE and gstate.auth_required:
            if vault.exists():
                info("Auth: requerida para /api/* y /v1/* (unlock con contraseña).")
            else:
                info("Auth: bloqueada; crea el vault con --set-password.")
        if not gstate.rag_enabled:
            info("RAG: deshabilitado (experimental en esta versión).")
        if not gstate.persistence_enabled:
            info("Memoria de experiencias: deshabilitada (experimental).")
        print()
    except OSError as e:
        err(f"No se pudo iniciar gateway en {cfg['host']}:{gateway_port}: {e}")

    public = f"http://{cfg['host']}:{gateway_port}"

    if args.cli:
        run_cli_mode(cfg["host"], gateway_port, model_meta, rag=bool(cfg.get("rag_enabled", False)))
        shutdown()
        return

    if not args.no_browser:
        info(f"Abriendo: {public}")
        time.sleep(0.5)
        webbrowser.open(public)

    p(f"\n  Gateway (UI+API)  {public}", G)
    p(f"  OpenAI-compatible {public}/v1", C)
    p(f"  Health            {public}/api/health", C)
    p("\n  Ctrl+C para detener.\n", Y)

    try:
        srv_proc.wait()
    except KeyboardInterrupt:
        shutdown()



if __name__ == "__main__":
    main()
