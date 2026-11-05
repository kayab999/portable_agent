# Threat model

Scope: a local, single-user Bonsai Portable instance on a trusted workstation.
The model, the browser, project files, MCP servers, plugins and ACP clients are
all treated as **untrusted inputs**, even when they are local.

## Assets

| Asset | Location | Impact if disclosed |
|---|---|---|
| Vault password | user memory | Full owner impersonation |
| `owner.lock` + salt + canary | project root / `data/` | Offline password guessing |
| Session tokens | process memory | Authenticated gateway access until expiry |
| Identity and persona config | `identity.json` | Behavior/profile disclosure |
| Chat turns and tool output | process memory, optional `events` queue | Private content disclosure |
| Indexed documents | `data/vector_db/memory.db` | Private document disclosure |
| API keys / model credentials | environment/user config | External account abuse |
| Local filesystem | workspace, docs, home | Data loss or exfiltration |

## Trust boundaries

1. **Browser → gateway** (`127.0.0.1`): bearer token, Host/Origin checks,
   body caps, rate limits, strict CSP.
2. **Gateway → llama-server**: loopback proxy; model output is untrusted.
3. **Runtime → MCP server**: stdio JSON-RPC; tool names/descriptions/results
   are untrusted; servers are opt-in.
4. **Runtime → plugins/skills/agents**: declarative manifests and Markdown;
   never executed as code.
5. **Runtime → ACP client**: stdio JSON-RPC; no filesystem/terminal bridging.
6. **Model output → tool calls**: parsed strictly, then filtered by persona
   allowlist, global allowlist, power gate and permission policy.
7. **Retrieved documents → prompt**: untrusted delimited blocks; cannot change
   tool policy or permissions.

## Actors and threats

| # | Actor / threat | Mitigation | Tests |
|---|---|---|---|
| T1 | Unauthenticated local HTTP client | Fail-closed auth, loopback host clamp, Host/Origin validation, body caps, rate limits | `tests/test_gateway_auth.py`, `tests/test_origin_host.py`, `tests/test_auth_fail_closed.py`, `tests/test_v1_auth.py` |
| T2 | Cross-site browser request | No wildcard CORS, Origin/Host allowlist, CSP `script-src 'self'`, no inline handlers | `tests/test_origin_host.py`, `tests/test_web_static.py` |
| T3 | Stolen device | PBKDF2 vault, `0600` secret files, tokens in memory only, no plaintext password storage | `tests/test_crypto.py`, `tests/test_auth_fail_closed.py`, `tests/test_doctor.py` |
| T4 | Malicious project content (`AGENTS.md`, skills, agents) | Untrusted delimiters, no permission widening, parser caps | `tests/test_agents_md.py`, `tests/test_skills.py`, `tests/test_context_builder.py` |
| T5 | Malicious model output | Strict tool-call parser, persona ∩ global allowlist, power gate, permission policy, hooks | `tests/test_tool_parser_strict.py`, `tests/test_persona_tools.py`, `tests/test_hooks.py` |
| T6 | Sandbox escape via tools | Path confinement, command/flag whitelist with symlink-resolved path args, opt-in non-isolated `python_exec` (AST pre-filter only, separate unsafe gate), per-hop SSRF validation with pinned-IP connections, no shell | `tests/test_tools_sandbox.py`, `tests/test_sandbox_escape.py`, `tests/test_ssrf.py`, `audit_suite/red_team/test_tools_security.py` |
| T7 | Malicious MCP server | Opt-in stdio, modern/legacy negotiation, per-request `_meta`, `resultType` enforcement, JSON-RPC framing caps, tool namespacing, strict empty allowlist, no permission expansion, `post_tool` hooks | `tests/test_mcp_client.py` |
| T8 | Malicious plugin | Declarative manifests only, no code execution, explicit enable flags | `tests/test_plugins_subagents.py` |
| T9 | Subagent privilege escalation | Agent tools ∩ global allowlist ∩ power gate ∩ active persona allowlist | `tests/test_plugins_subagents.py` |
| T10 | Prompt injection via RAG documents | Results wrapped as untrusted, fenced delimiters, citations, no tool authorization | `tests/test_context_builder.py`, `tests/test_rag_store.py` |
| T11 | RAG indexing escapes allowed roots | Resolved path confinement, symlink rejection, size caps, UTF-8 policy | `tests/test_gateway_auth.py`, `tests/test_rag_store.py`, `tests/test_gateway_rag.py` |
| T12 | RAG data corruption / stale results | Versioned schema, transactions, FTS triggers, active filtering, idempotent ingest | `tests/test_rag_store.py`, `tests/test_sqlite_concurrency.py`, `audit_suite/data_quality/test_memory_integrity.py` |
| T13 | Persistence duplication/loss | Idempotency keys, claim/lease, stale-lease reclaim, retry/backoff, dead-letter | `tests/test_experience_store.py`, `tests/test_agent_loop.py`, `tests/test_gateway_rag.py` |
| T14 | ACP client abuse | Session caps, strict params, no fs/terminal/client-MCP, cancellation, stdio-only | `tests/test_acp.py` |
| T15 | Supply-chain compromise | Stdlib-only runtime; dev tools pinned; optional `pip-audit`/`bandit` in CI | `scripts/ci.sh`, `pyproject.toml` |

## Explicit non-goals

- A2A / remote agent federation.
- Remote MCP transports (HTTP/SSE/OAuth).
- Executing plugin JS/TS/Python.
- Multi-tenant authorization.
- OS-level confinement. `python_exec` is disabled by default and remains an
  opt-in, non-isolated runner; its AST pre-filter is not a real sandbox and a
  local attacker who can modify the runtime can bypass it.

## Residual risks

1. **RAG content is not encrypted at rest.** Anyone with filesystem access can
   read `memory.db`. Full-disk encryption is the recommended control.
2. **The model can still be socially manipulated.** Delimiters prevent content
   from changing *permissions*, not from making a misleading answer.
3. **Tool output can contain sensitive data.** Output masking is best-effort;
   it is not a data-loss-prevention boundary.
4. **Local processes can read `/proc` and the environment** of the runtime
   under the same user. The product does not defend against a same-user
   attacker.
5. **`ask` in headless mode means deny.** There is no interactive approval
   dialog; use `auto_approve` only for tools you would allow by default.

## Security invariants to preserve in review

- A new surface must be disabled by default and cannot widen another surface.
- External content can never add a tool, permission, root or hook.
- Every state-changing endpoint requires auth and rate limiting.
- Secret-bearing files are `0600`; executables are not group/world writable.
- Failures degrade to "disabled", never to "allow all".
