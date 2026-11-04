#!/usr/bin/env python3
"""doctor.py — local diagnostics for operators and CI.

``python doctor.py`` prints a human report and exits non-zero when a hard
prerequisite is missing. ``--json`` is intended for support bundles and tests.
No secrets are printed: only presence, size and permission metadata.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATABASE = ROOT / "data" / "vector_db" / "memory.db"


def _check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "required": required}


def run_checks(root: Path = ROOT, *, port: int | None = None) -> dict:
    checks: list[dict] = []
    version = sys.version_info
    checks.append(
        _check(
            "python",
            version >= (3, 11),
            f"{platform.python_version()} ({platform.system()} {platform.machine()})",
        )
    )

    for relative in ("launcher.py", "gateway.py", "web/index.html", "web/app.js"):
        path = root / relative
        checks.append(_check(f"file:{relative}", path.is_file(), str(path)))

    models = root / "models"
    ggufs = list(models.glob("*.gguf")) if models.is_dir() else []
    checks.append(
        _check(
            "model",
            bool(ggufs),
            f"{len(ggufs)} .gguf en models/" if ggufs else "sin modelos; ejecuta --setup",
            required=False,
        )
    )

    binary = shutil.which("llama-server")
    if not binary:
        candidates = list((root / "bin").rglob("llama-server*"))
        binary = str(candidates[0]) if candidates else None
    checks.append(
        _check(
            "llama-server",
            bool(binary),
            binary or "no encontrado; ejecuta --setup",
            required=False,
        )
    )

    vault = root / "owner.lock"
    if vault.exists():
        mode = vault.stat().st_mode & 0o777
        checks.append(
            _check("vault", mode == 0o600, f"owner.lock mode {mode:o}", required=False)
        )
    else:
        checks.append(
            _check(
                "vault",
                True,
                "no provisionado (--set-password recomendado)",
                required=False,
            )
        )

    data = root / "data"
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        checks.append(_check("data-writable", True, str(data)))
    except OSError as exc:
        checks.append(_check("data-writable", False, str(exc)))

    if DATABASE.exists():
        try:
            conn = sqlite3.connect(str(DATABASE), timeout=5)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                version_row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key='version'"
                ).fetchone()
                conn.execute(
                    "SELECT COUNT(*) FROM documents"
                ).fetchone()
            finally:
                conn.close()
            checks.append(
                _check(
                    "rag-db",
                    integrity == "ok",
                    f"schema={version_row[0] if version_row else '?'} integrity={integrity}",
                )
            )
        except sqlite3.Error as exc:
            checks.append(_check("rag-db", False, str(exc)))
    else:
        checks.append(_check("rag-db", True, "no inicializada", required=False))

    if port is not None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", int(port)))
            free = True
            detail = f"127.0.0.1:{port} libre"
        except OSError as exc:
            free = False
            detail = f"127.0.0.1:{port} no disponible ({exc})"
        finally:
            sock.close()
        checks.append(_check("port", free, detail, required=False))

    ok = all(item["ok"] for item in checks if item["required"])
    return {"ok": ok, "checks": checks}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bonsai Portable diagnostics")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--port", type=int, default=None, help="Check port availability")
    args = parser.parse_args(argv)
    report = run_checks(port=args.port)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for item in report["checks"]:
            mark = "ok" if item["ok"] else ("FAIL" if item["required"] else "warn")
            print(f"[{mark:4s}] {item['name']}: {item['detail']}")
        print("\n" + ("Doctor: OK" if report["ok"] else "Doctor: FAIL"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
