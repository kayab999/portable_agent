#!/usr/bin/env python3
"""Enforce per-module coverage floors for security-critical code.

``coverage.py`` has a single global ``fail_under``; this script adds the
required per-file discipline so a well-covered utility cannot mask a weak
security module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FLOORS = {
    "local_memory.py": 80,
    "experience_store.py": 80,
    "permissions.py": 90,
    "hooks.py": 80,
    "context_builder.py": 85,
    "session_auth.py": 75,
    "crypto.py": 75,
    "mcp_client.py": 75,
    "acp_server.py": 65,
    "tools.py": 60,
    "gateway.py": 58,
    "subagents.py": 75,
    "config_schema.py": 70,
    "bonsai_logging.py": 80,
    "maintenance.py": 55,
    "runtime.py": 70,
    "model_client.py": 90,
    "audit_worker.py": 55,
    "doctor.py": 55,
}

OVERALL_FLOOR = 64


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "coverage.json"],
        cwd=ROOT,
        check=True,
    )
    data = json.loads((ROOT / "coverage.json").read_text(encoding="utf-8"))
    files = data["files"]
    failures: list[str] = []
    for name, floor in FLOORS.items():
        entry = files.get(name)
        if entry is None:
            failures.append(f"{name}: missing from coverage report")
            continue
        percent = float(entry["summary"]["percent_covered"])
        if percent + 1e-9 < floor:
            failures.append(f"{name}: {percent:.1f}% < {floor}%")
    total = float(data["totals"]["percent_covered"])
    if total + 1e-9 < OVERALL_FLOOR:
        failures.append(f"overall: {total:.1f}% < {OVERALL_FLOOR}%")
    if failures:
        print("Coverage floors failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"Coverage OK: overall {total:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
