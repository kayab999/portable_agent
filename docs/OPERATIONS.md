# Operations guide

This guide covers install, first run, diagnostics, backup/restore, upgrades and
recovery for a local Linux installation. Windows/macOS are best-effort.

## Requirements

- Python 3.11+
- Linux x86_64/arm64 for the first-class path
- `llama-server` (bundled by `--setup`, or on `PATH`)
- A local `.gguf` model in `models/`

No runtime Python packages are required.

## Install and first run

```bash
git clone <repo> bonsai-portable
cd bonsai-portable
python3 launcher.py --setup          # download model + llama.cpp binary
python3 launcher.py --set-password   # create owner.lock (0600)
python3 launcher.py                  # serve http://127.0.0.1:8080
```

`--doctor` checks Python, required files, model/binary presence, vault mode,
data write access, RAG schema and port availability:

```bash
python3 launcher.py --doctor
python3 doctor.py --json
```

## Configuration

`config.json` is local and ignored by git. Start from `config.example.json`.
Invalid values fail fast with an actionable message via `config_schema.py`.

Important flags:

| Key | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | loopback only unless `BONSAI_ALLOW_LAN=1` |
| `rag_enabled` | `false` | enable local retrieval |
| `experience_persistence` | `false` | enable the experience queue |
| `mcp_enabled` | `false` | start stdio MCP servers |
| `plugins_enabled` | `false` | read declarative plugin manifests |
| `subagents_enabled` | `false` | discover Markdown subagents |
| `tool_permissions` | `{}` | allow/ask/deny policy; `ask` denies headless |

Secrets belong in environment variables, not `config.json`.

## Logs

- Launcher/gateway diagnostics: the launcher log.
- Structured JSON logs: set `BONSAI_LOG_JSON=1`; writes
  `runtime/bonsai.log` with rotation (2 MiB × 3) and secret redaction.
- HTTP access logs: set `BONSAI_HTTP_LOG=1`.
- Maintenance metrics: `data/maintenance_log.jsonl`.

## Backup

```bash
scripts/backup.sh                       # -> backup/bonsai-backup-<timestamp>.tar.gz
scripts/backup.sh /secure/bonsai.tgz
```

The archive contains `config.json`, `identity.json`, `owner.lock` and `data/`.
It contains vault credentials, so keep it encrypted and never publish it.

## Restore

Stop the server first.

```bash
scripts/restore.sh backup/bonsai-backup-....tar.gz
python3 launcher.py --doctor
```

The restore script rejects absolute paths and `..` traversal inside the
archive. Use `--force` only after stopping the server and confirming the
backup is the one you want.

## Upgrade

1. Back up.
2. Replace the code (git pull / new release).
3. Keep `config.json`, `identity.json`, `owner.lock`, `data/`, `models/`.
4. Run `python3 launcher.py --doctor`.
5. Open the gateway; RAG schema migrates automatically to v2.
6. Check `/api/rag/status` and `/api/experience/status`.

Database migrations are additive and transactional. There is no automatic
downgrade; restore the backup to roll back.

## Recovery

| Symptom | Action |
|---|---|
| `owner.lock` lost | `python3 launcher.py --set-password --force` (destroys old vault) |
| RAG DB corrupt | stop server, move `data/vector_db/memory.db*` aside, re-index |
| Queue stuck | `/api/experience/status`; expired leases reclaim automatically |
| Dead letters | inspect `last_error`; purge after fixing the root cause |
| Port busy | `--port 8081` or `python3 launcher.py --doctor` |
| Model missing | `python3 launcher.py --setup` or place a `.gguf` in `models/` |

## Uninstall

Delete the project directory. There is no system service or registry entry.
If you used a custom config/data directory, remove it separately.
