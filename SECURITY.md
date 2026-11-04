# Security policy

Bonsai Portable is a **single-user, loopback-only, local-first** agent runtime.
It is not a multi-tenant service and must not be exposed to a network.

## Supported versions

| Version | Supported |
|---|---|
| `1.0.0-rc.5` and current `main` | Yes |
| Older release candidates | No |

## Reporting a vulnerability

Report privately to the project maintainer before opening a public issue.
Include:

- affected version/commit;
- a minimal reproduction;
- whether the issue requires local access, a malicious repository, or a
  malicious model/MCP/plugin/ACP peer;
- any suggested fix.

Do not include real vault passwords, API tokens, or private documents.

## Security model in one page

- **Fail closed.** Authentication is required for `/api/*` and `/v1/*` unless
  `BONSAI_DEBUG=1` or `BONSAI_INSECURE=1` is explicitly set.
- **Loopback by default.** `host` is clamped to `127.0.0.1` unless
  `BONSAI_ALLOW_LAN=1`; IPv6 bind is rejected.
- **Optional surfaces are off.** MCP, plugins, hooks, subagents and direct
  `/api/tools` are disabled unless configured.
- **No foreign code execution.** Plugin adapters read declarative manifests
  only. Skills, agents and AGENTS.md are untrusted prompt content.
- **Power tools require an explicit gate.** `execute_command`, `python_exec`
  and `fetch_url` require `BONSAI_POWER_TOOLS=1` (or explicit debug mode).
- **External content is delimited.** AGENTS.md, skills, MCP metadata, RAG
  results and user sources are wrapped in `<<<BONSAI_*>>>` blocks and can never
  grant permissions or authorize tool use.
- **Secrets stay local.** The vault uses PBKDF2-SHA256 with 600,000 iterations
  and an atomic `0600` file. Tokens are session-scoped and stored in memory.
- **At-rest data is not encrypted.** `data/`, `workspace/` and `docs/` are
  ordinary files. Use full-disk encryption if the device may be stolen.

## Out of scope

- Multi-user isolation, remote exposure, TLS termination, SSO.
- OS-level sandboxing (containers, seccomp, jails). The AST sandbox is a
  hardening layer, not a security boundary against arbitrary Python.
- A2A/remote agent transport.
- Remote MCP HTTP/SSE/OAuth; only stdio MCP is supported.
- Protection against a fully compromised local OS account.

See `docs/THREAT_MODEL.md` for assets, trust boundaries and test mapping.
