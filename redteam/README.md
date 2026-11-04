# Red Team Suite — Bonsai Portable

## What is actually tested

| Area | Coverage |
|------|----------|
| Path jail | Traversal, absolute paths, symlink resolve |
| Command sandbox | Whitelist without interpreters; path-jailed args |
| python_exec | AST import/name allowlist; no `os`/`open` |
| SSRF | Private/link-local/metadata + redirect recheck |
| Gateway auth | Bearer required; direct `/api/tools` disabled |
| Crypto | EtM v1; legacy v0 rejected by default |
| Persona tools | Allowlists without silent elevation |

## Run

```bash
python3 redteam/run_all.py
# or full CI
./scripts/ci.sh
```

## Explicitly NOT implemented (do not claim)

- Seccomp / syscall filtering
- Skill package signing
- Cognitive Drift Engine (CDE) productization
- Container / gVisor / nsjail isolation
- Multi-tenant auth (OAuth, mTLS)

Power tools (`python_exec`, `execute_command`, `fetch_url`) require
`BONSAI_POWER_TOOLS=1` or `BONSAI_DEBUG=1`.
