#!/usr/bin/env bash
# Restore a Bonsai backup created by scripts/backup.sh.
# Usage: scripts/restore.sh backup.tar.gz [--force]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE="${1:-}"
FORCE="${2:-}"
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "Uso: scripts/restore.sh backup.tar.gz [--force]" >&2
  exit 2
fi
if [[ -e "$ROOT/identity.json" && "$FORCE" != "--force" ]]; then
  echo "Ya existe identity.json. Detén el servidor y usa --force para sobrescribir." >&2
  exit 1
fi
# Refuse absolute paths and parent traversal inside the archive.
if tar -tzf "$ARCHIVE" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "Archivo rechazado: contiene rutas inseguras." >&2
  exit 3
fi
cd "$ROOT"
tar -xzf "$ARCHIVE"
chmod 600 owner.lock 2>/dev/null || true
echo "Restaurado en $ROOT. Ejecuta: python launcher.py --doctor"
