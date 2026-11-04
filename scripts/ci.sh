#!/usr/bin/env bash
# Minimal CI for Bonsai Portable RC
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

echo "== smoke =="
"$PYTHON" scripts/smoke_boot.py

echo "== unit tests (incl. rc.5: auth, vault, origin/host, /v1, sqlite, parser, containment, hooks, plugins, ACP) =="
"$PYTHON" -m unittest discover -s tests -v

echo "== adversarial sandbox escape =="
"$PYTHON" -m unittest tests.test_sandbox_escape tests.test_gateway_auth tests.test_persona_tools -v

echo "== audit suite =="
"$PYTHON" -m unittest \
  audit_suite.red_team.test_tools_security \
  audit_suite.chaos_monkey.test_execution_resilience \
  audit_suite.data_quality.test_memory_integrity \
  -v

echo "== e2e boot (llama-server + gateway + /v1 + shutdown) =="
"$PYTHON" -m unittest tests.test_e2e_boot -v 2>&1 | tail -4

echo "== lint (ruff: imports sin usar, shadowing, bare except, multi-statement) =="
if command -v ruff &>/dev/null; then
  ruff check .
else
  echo "  ruff no disponible — saltando (instalar: pip install ruff)"
fi

echo "CI PASS"
