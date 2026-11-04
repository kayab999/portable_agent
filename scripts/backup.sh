#!/usr/bin/env bash
# Create a portable, permission-restricted backup of Bonsai state.
# Usage: scripts/backup.sh [output.tar.gz]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="${1:-backup/bonsai-backup-$(date +%Y%m%d-%H%M%S).tar.gz}"
mkdir -p "$(dirname "$OUT")"
umask 077
FILES=()
for item in config.json identity.json owner.lock data; do
  [[ -e "$item" ]] && FILES+=("$item")
done
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "Nada que respaldar (config.json/identity.json/owner.lock/data)." >&2
  exit 1
fi
tar -czf "$OUT" \
  --exclude='data/vector_db/*.db-wal' \
  --exclude='data/vector_db/*.db-shm' \
  "${FILES[@]}"
chmod 600 "$OUT"
echo "Backup creado: $OUT"
echo "Contiene credenciales del vault: guárdalo cifrado y nunca lo publiques."
