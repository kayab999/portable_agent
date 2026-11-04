# Stable RC — Definition of Done (rc.5)

Product: **Bonsai Portable** (internal: CRS)
Version: `1.0.0-rc.5` (see `VERSION`)

Every checked item must map to an automated test that fails on rc.3 behavior.
Unchecked items are explicitly out of scope for this release.

## Functional

- [x] Single command start (`./start.sh`, `python3 launcher.py`, `start.ps1`)
- [x] Gateway health + same-origin APIs
- [x] OOBE identity save/load/reset
- [x] Core tools: read/write/list/stats/diagnose
- [x] Server-side agent loop (`/api/chat`) with canonical tool protocol + timeline
- [x] Strict `<tool_call>` parsing; fenced/standalone JSON is not executed
  (`tests/test_tool_parser_strict.py`)
- [ ] RAG index/retrieval — **experimental, disabled by default**
  (`tests/test_feature_containment.py`)
- [ ] Experience persistence — **experimental, disabled by default**
  (`tests/test_feature_containment.py`)

## Quality

- [x] Unit + adversarial + audit + production E2E green (`./scripts/ci.sh`)
- [x] 220 tests: 219 passing + 1 skipped (`pwsh` absent)
- [x] Lint gate (`ruff` curated rules) at 0 findings
- [x] E2E: debug boot → health → `/v1` → clean shutdown
- [x] E2E: production boot without vault stays locked; `/v1` is 401
  (`tests/test_e2e_boot.py`)
- [x] E2E: production boot with vault unlocks and `/v1` works
  (`tests/test_e2e_boot.py`)
- [x] Path confinement + allowlist red-team tests
- [x] Adversarial sandbox-escape tests green
- [x] 64 concurrent searches + concurrent read/write, SQLite integrity `ok`
  (`tests/test_sqlite_concurrency.py`)

## Setup & Portability

- [x] `--setup` for llama-server (PrismML fork) + models
- [x] OS/arch detection shared in `runtime_core`
- [x] `start.sh` (POSIX) + Python-first `start.ps1` (Windows)
- [x] `start.ps1` parses under PowerShell when `pwsh` is available
  (`tests/test_start_ps1.py`)
- [x] Any `.gguf` via `models/` or `--model /path`

## Compatibility (rc.5)

- [x] AGENTS.md discovery root→cwd with a 32 KB cap
  (`tests/test_agents_md.py`)
- [x] Agent Skills frontmatter parsing, validation, discovery and deduplication
  (`tests/test_skills.py`)
- [x] `load_skill` progressive disclosure; scripts are never executed
  (`tests/test_skills.py`)
- [x] Fixed system-prompt order with untrusted-content delimiters
  (`tests/test_context_builder.py`)
- [x] MCP stdio handshake, namespaced tools, allowlists and fail-closed dispatch
  (`tests/test_mcp_client.py`)
- [x] MCP results are labeled untrusted and size-capped
  (`tests/test_mcp_client.py`)
- [x] Lifecycle hooks run without a shell, with a sanitized environment, and
  pre-events fail closed (`tests/test_hooks.py`)
- [x] Per-tool `allow`/`ask`/`deny` policy; `ask` fails closed headless
  (`tests/test_hooks.py`)
- [x] Declarative plugin adapters for Claude/Gemini/Hermes/Antigravity never
  execute plugin code (`tests/test_plugins_subagents.py`)
- [x] Markdown subagents restrict tools to the global allowlist intersection
  (`tests/test_plugins_subagents.py`)
- [x] ACP v1 stdio server: initialize, session/new, prompt, cancel and updates;
  no fs/terminal/client MCP bridging (`tests/test_acp.py`)
- [x] A2A is an explicit non-goal for local-only deployments (documented)

## Security (measurable)

- [x] Fail-closed auth: no vault → unlock returns 403, no token
  (`tests/test_auth_fail_closed.py`)
- [x] `--set-password` creates a single `owner.lock` (KDF+salt+canary) with
  mode 0600 and atomic rotation (`tests/test_auth_fail_closed.py`)
- [x] PBKDF2-HMAC-SHA256 at 600k iterations; rc.3 100k vaults verify and migrate
  (`tests/test_auth_fail_closed.py`)
- [x] IPv6 (`::1`) is rejected at startup instead of failing at bind
  (`tests/test_runtime_core.py`, `tests/test_origin_host.py`)
- [x] Wrong password rejected; unlock rate-limited to 5/min
  (`tests/test_auth_fail_closed.py`)
- [x] `BONSAI_INSECURE=1` is the only auth bypass and is explicit
  (`tests/test_auth_fail_closed.py`)
- [x] A verified unlock can mint new tokens; wrong passwords are rejected before
  unlock (`tests/test_auth_fail_closed.py`)
- [x] `--cli` prompts for the vault password when production is locked
- [x] Experimental persistence is off in every entrypoint, including
  `execution_loop` (`tests/test_feature_containment.py`)
- [x] No `Access-Control-Allow-Origin: *`; exact loopback origin only
  (`tests/test_origin_host.py`)
- [x] Foreign Origin and invalid Host are rejected
  (`tests/test_origin_host.py`)
- [x] `/v1/*` requires a bearer in production; health stays public
  (`tests/test_v1_auth.py`)
- [x] Thread-safe SQLite: connection per operation
  (`tests/test_sqlite_concurrency.py`)
- [x] `DEBUG` off by default (`BONSAI_DEBUG=1` for dev)
- [x] Workspace write; docs/datasets read-only
- [x] Subprocess whitelist without interpreters; path-jailed args
- [x] `python_exec` AST allowlist (no `os`/`open`/`subprocess`)
- [x] POWER_TOOLS require `BONSAI_POWER_TOOLS=1` or DEBUG
- [x] SSRF guards (private/link-local + redirect recheck)
- [x] Crypto Encrypt-then-MAC v1; legacy v0 rejected by default
- [x] Persona tool allowlists enforced (no silent write/power elevation)
- [x] `POST /api/tools` disabled outside DEBUG
- [x] Non-loopback bind requires `BONSAI_ALLOW_LAN=1`
- [x] Body size limit (2 MiB) + rate limits
- [x] Security response headers (CSP, nosniff, frame-deny)
- [ ] OS-level isolation (seccomp/nsjail) — out of scope
- [ ] Multi-user identity provider — out of scope

## UX

- [x] Same-origin `API_BASE` + session unlock
- [x] Tool activity cards in chat
- [x] Markdown subset rendering
- [x] Cancel (Esc) + toasts on errors
- [x] Sidebar/sources persisted in localStorage
- [x] RAG status visible (disabled notice instead of false success)

## Docs

- [x] Product name: Bonsai Portable
- [x] `README.md` + `ARCHITECTURE.md` + `MANUAL.md` aligned with rc.5
- [x] `Known limitations` documents RAG, persistence, reflection, dead modules
- [x] `MASK_GUIDE.md` says hash, not cryptographic signature
- [x] `INTEGRATION_GUIDE.md` says the linter is not mounted
- [x] Honest redteam/README (no false seccomp claims)
- [x] LICENSE: MIT (`LICENSE`)
