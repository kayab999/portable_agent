# Red Team Suite — Bonsai Portable

## What is actually tested

| Area | Coverage |
|------|----------|
| Path jail | Traversal, absolute paths, symlink resolve |
| Command sandbox | Whitelist without interpreters; allowlisted flags; symlink-resolved paths |
| python_exec | Disabled by default; AST pre-filter when explicitly opted in |
| SSRF | Non-global/CGNAT, pinned DNS, redirect recheck, 1 MiB body cap |
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
`BONSAI_POWER_TOOLS=1` or `BONSAI_DEBUG=1`. `python_exec` also requires the
explicit `BONSAI_UNSAFE_PYTHON_EXEC=1` opt-in and has no OS isolation.
