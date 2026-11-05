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
- **HTTP connections are hardened.** The gateway proxy and model client disable
  redirects and implicit proxies when talking to the internal LLM endpoint.
- **Token minting needs proof.** `/api/session/unlock` issues a token only for a
  valid bearer, a verified vault password, or the explicit debug/insecure
  bypass. A process that is merely unlocked (for example by YubiKey) cannot
  mint tokens for local clients.
- **Unlock rate limits are per client.** The 5-attempt/minute unlock limit is
  tracked separately per client address.
- **Loopback by default.** `host` is clamped to `127.0.0.1` unless
  `BONSAI_ALLOW_LAN=1`; IPv6 bind is rejected.
- **Optional surfaces are off.** MCP, plugins, hooks, subagents, direct
  `/api/tools`, and the knowledge base are disabled unless configured.
- **Permissions fail closed.** Invalid permission configuration loads a
  deny-all policy, and MCP tools require an explicit allow rule.
- **MCP protocol is strict.** The client uses per-request `_meta` and
  `resultType` for modern `2026-07-28` servers, falls back to the legacy
  `initialize` handshake only for non-modern servers, and treats an explicit
  empty `tools_allow` as "no tools".
- **No foreign code execution.** Plugin adapters read declarative manifests
  only. Skills, agents and AGENTS.md are untrusted prompt content.
- **Command paths are resolved.** `execute_command` uses a command/flag
  allowlist, rejects unknown flags, and resolves every path argument—including
  bare relative names and symlinks—under the read roots before execution.
- **Power tools require an explicit gate.** `execute_command`, `python_exec`
  and `fetch_url` require `BONSAI_POWER_TOOLS=1` (or explicit debug mode).
  `python_exec` additionally requires `BONSAI_UNSAFE_PYTHON_EXEC=1`; it runs
  Python without OS isolation and its AST filter is only a pre-filter.
- **Resource limits.** `execute_command` and `python_exec` run under CPU,
  address-space, file-size, process and descriptor limits where the platform
  supports them; output remains capped.
- **Downloads are integrity-checked.** `--setup` pins model SHA-256 hashes in
  `MODEL_CATALOG` and requires the GitHub release digest (or explicit
  `BONSAI_BIN_SHA256`) for `llama-server`. Mismatches delete the partial
  file and fail closed; hashing is streaming. ZIP members are validated
  before extraction.
- **External content is delimited.** AGENTS.md, skills, MCP metadata, RAG
  results and user sources are wrapped in `<<<BONSAI_*>>>` blocks and can never
  grant permissions or authorize tool use.
- **Secrets stay local.** The vault uses PBKDF2-SHA256 with 600,000 iterations
  and an atomic `0600` file. Tokens are session-scoped and stored in memory.
- **Crypto is custom but reviewed.** The stdlib-only vault cipher is an
  HMAC-SHA256 CTR stream with Encrypt-then-MAC. It is not FIPS-validated; the
  E9 review item remains open for external cryptographic review.
- **At-rest data is not encrypted.** `data/`, `workspace/` and `docs/` are
  ordinary files. Use full-disk encryption if the device may be stolen.

## SSRF protections (`fetch_url`)

- Only `http` and `https` URLs are accepted. Loopback, private, link-local,
  multicast, reserved, unspecified, CGNAT (`100.64.0.0/10`) and other
  non-global destinations are rejected, including IPv4-mapped IPv6 addresses
  and mixed public/private DNS answers.
- DNS is resolved once per request hop. The connection is pinned to a validated
  address; it does not re-resolve the hostname through the HTTP client.
- HTTPS keeps the original hostname for SNI and certificate verification.
- Redirects are not followed automatically. Each `Location` is normalized and
  validated before connecting. HTTPS-to-HTTP downgrades, loops and chains over
  five redirects are rejected.
- `fetch_url` uses direct `http.client` connections and does not honor
  `http_proxy`/`https_proxy` or other implicit proxy environment settings.
- Redirect hops share a single timeout budget. Response bodies are capped at
  1 MiB; oversized responses are truncated and flagged in the tool result.

## Out of scope

- Multi-user isolation, remote exposure, TLS termination, SSO.
- OS-level sandboxing (containers, seccomp, jails). `python_exec` is disabled
  by default and remains an opt-in, non-isolated convenience runner; its AST
  pre-filter is not a security boundary against arbitrary Python.
- A2A/remote agent transport.
- Remote MCP HTTP/SSE/OAuth; only stdio MCP is supported.
- Protection against a fully compromised local OS account.

See `docs/THREAT_MODEL.md` for assets, trust boundaries and test mapping.
