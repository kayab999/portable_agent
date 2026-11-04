#!/usr/bin/env bash
# Bonsai Portable — root start script (Linux/macOS)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 &>/dev/null; then
    exec python3 launcher.py "$@"
fi

echo "ERROR: python3 required. Install Python 3.11+ and re-run."
exit 1
