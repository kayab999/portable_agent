# Stable RC — Definition of Done (rc.5)

Product: **Bonsai Portable** (internal: CRS)
Version: `1.0.0-rc.5` (see `VERSION`)

Every checked item maps to an automated test that fails on rc.3 behavior.
Unchecked items are explicitly out of scope for this release.

## Functional

- [x] Single command start (`./start.sh`, `python3 launcher.py`, `start.ps1`)
- [x] Gateway health + same-origin APIs
- [x] OOBE identity save/load/reset
- [x] Core tools: read/write/list/stats/diagnose
- [x] Server-side agent loop (`/api/chat`) with canonical tool protocol + timeline
- [x] Strict `<tool_call>` parsing; fenced/standalone JSON is not executed
  (`tests/test_tool_parser_strict.py`)
- [x] RAG index/retrieval with SQLite FTS5/BM25, schema v2, citations
  (`tests/test_rag_store.py`, `tests/test_gateway_rag.py`)
- [x] RAG v1→v2 migration preserves content and searchability
  (`tests/test_rag_store.py::TestRagMigration`)
- [x] RAG ingestion is idempotent, confined, and soft-deletes missing sources
  (`tests/test_rag_store.py`, `tests/test_gateway_auth.py`)
- [x] Experience persistence with idempotency, leases, retry and dead-letter
  (`tests/test_experience_store.py`, `tests/test_gateway_rag.py`)
- [x] RAG and persistence remain opt-in and report `503 feature_unavailable`
  when disabled (`tests/test_feature_containment.py`)
- [x] Maintenance scoring is non-self-referential, dry-runnable, and purges
  documents only with explicit opt-in (`audit_suite/data_quality/test_memory_integrity.py`)

## Quality

- [x] Unit + adversarial + audit + production E2E green (`./scripts/ci.sh`)
- [x] 295 tests: 294 passing + 1 skipped (`pwsh` absent)
- [x] Lint gate (`ruff` curated rules) at 0 findings
- [x] Branch coverage with per-module floors (`scripts/check_coverage.py`)
- [x] Import contracts: storage never imports transports; ACP never imports the
  HTTP gateway (`pyproject.toml`, optional `lint-imports`)
- [x] Shared composition root and model transport are covered, including
  fail-closed plugin/MCP wiring (`tests/test_runtime.py`)
- [x] CI matrix: Linux Python 3.11.4–3.13, Windows PowerShell launcher job and an
  import-contract/audit job (`.github/workflows/ci.yml`)
- [x] E2E: debug boot → health → `/v1` → clean shutdown
- [x] E2E: production boot without vault stays locked; `/v1` is 401
  (`tests/test_e2e_boot.py`)
- [x] E2E: production boot with vault unlocks and `/v1` works
  (`tests/test_e2e_boot.py`)
- [x] Path confinement + allowlist red-team tests
- [x] Adversarial sandbox-escape tests green
- [x] 64 concurrent searches + concurrent read/write, SQLite integrity `ok`
  (`tests/test_sqlite_concurrency.py`)
- [x] Concurrent queue drains never duplicate events
  (`tests/test_experience_store.py`)

## Setup & Portability

- [x] `--setup` for llama-server (PrismML fork) + models
- [x] OS/arch detection shared in `runtime_core`
- [x] `start.sh` (POSIX) + Python-first `start.ps1` (Windows)
- [x] `start.ps1` parses under PowerShell when `pwsh` is available
  (`tests/test_start_ps1.py`)
- [x] Any `.gguf` via `models/` or `--model /path`
- [x] `--doctor` / `doctor.py --json` diagnostics
  (`tests/test_doctor.py`)
- [x] Config schema validation fails fast with actionable messages
  (`tests/test_config_schema.py`)
- [x] Backup/restore scripts refuse unsafe archive paths
- [x] Shared composition root for launcher and ACP (`runtime.py`)

## Compatibility (rc.5)

- [x] AGENTS.md discovery root→cwd with a 32 KB cap
  (`tests/test_agents_md.py`)
- [x] Agent Skills frontmatter parsing, validation, discovery and deduplication
  (`tests/test_skills.py`)
- [x] `load_skill` progressive disclosure; scripts are never executed
  (`tests/test_skills.py`)
- [x] Fixed system-prompt order with untrusted-content delimiters, including
  RAG context (`tests/test_context_builder.py`)
- [x] MCP stdio `2026-07-28` protocol (`server/discover`, per-request `_meta`,
  `resultType`), legacy `initialize` fallback, namespaced tools, strict
  allowlists and fail-closed dispatch (`tests/test_mcp_client.py`)
- [x] MCP results are labeled untrusted and size-capped
  (`tests/test_mcp_client.py`)
- [x] Lifecycle hooks run without a shell, with a sanitized environment, and
  pre-events fail closed (`tests/test_hooks.py`)
- [x] Per-tool `allow`/`ask`/`deny` policy; `ask` fails closed headless
  (`tests/test_hooks.py`)
- [x] Declarative plugin adapters for Claude/Gemini/Hermes/Antigravity never
  execute plugin code (`tests/test_plugins_subagents.py`)
- [x] Markdown subagents restrict tools to the global allowlist intersection
  **and** the active persona allowlist (`tests/test_plugins_subagents.py`)
- [x] ACP v1 stdio server: initialize, session/new, prompt, cancel and updates;
  no fs/terminal/client MCP bridging (`tests/test_acp.py`)
- [x] A2A is an explicit non-goal for local-only deployments (documented)

## Security (measurable)

- [x] Fail-closed auth: no vault → unlock returns 403, no token
  (`tests/test_auth_fail_closed.py`)
- [x] Download supply chain: pinned model SHA-256 and binary release digest
  (`tests/test_setup_download.py`, `tests/test_runtime_core.py`)
- [x] PBKDF2-HMAC-SHA256 at 600k iterations; rc.3 100k vaults verify and migrate
  (`tests/test_auth_fail_closed.py`)
- [x] IPv6 (`::1`) is rejected at startup instead of failing at bind
  (`tests/test_runtime_core.py`, `tests/test_origin_host.py`)
- [x] Wrong password rejected; unlock rate-limited to 5/min per client
  (`tests/test_auth_fail_closed.py`)
- [x] `BONSAI_INSECURE=1` is the only auth bypass and is explicit
  (`tests/test_auth_fail_closed.py`)
- [x] Token minting requires a valid bearer or vault password; process unlock
  state alone cannot mint tokens (`tests/test_auth_fail_closed.py`)
- [x] Gateway/model HTTP proxy disables redirects and implicit proxies
- [x] RAG/persistence default off in every entrypoint, including
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
- [x] Resource limits for `execute_command` and `python_exec` where supported
- [x] `python_exec` disabled by default; opt-in via `BONSAI_UNSAFE_PYTHON_EXEC=1`
  (AST pre-filter, no OS isolation)
- [x] POWER_TOOLS require `BONSAI_POWER_TOOLS=1` or DEBUG
- [x] SSRF guards (non-global/CGNAT, pinned DNS, redirect recheck, 1 MiB body cap)
- [x] Crypto Encrypt-then-MAC v1; legacy v0 rejected by default
- [x] Persona tool allowlists enforced (no silent write/power elevation)
- [x] `POST /api/tools` disabled outside DEBUG
- [x] Non-loopback bind requires `BONSAI_ALLOW_LAN=1`
- [x] Body size limit (2 MiB) + rate limits
- [x] Security response headers: CSP `script-src 'self'`, `object-src 'none'`,
  `base-uri 'self'`, `form-action 'self'`, COOP/CORP, nosniff, frame-deny
  (`tests/test_web_static.py`)
- [x] UI has no inline event handlers or inline `<script>`/`<style>`
  (`tests/test_web_static.py`)
- [x] FTS queries cannot inject operators; inactive/deleted chunks never return
  (`tests/test_rag_store.py`)
- [x] Experience retries are idempotent and dead-lettered, never silently lost
  (`tests/test_experience_store.py`)
- [x] Structured logs redact bearer tokens, API keys and `sk-*` secrets
  (`tests/test_logging.py`)
- [x] Threat model maps each asset/threat to tests (`THREAT_MODEL.md`)
- [ ] OS-level isolation (seccomp/nsjail) — out of scope
- [ ] Multi-user identity provider — out of scope
- [ ] At-rest encryption of RAG/persistence data — out of scope (use FDE)

## UX

- [x] Same-origin `API_BASE` + session unlock
- [x] Tool activity cards in chat
- [x] Markdown subset rendering
- [x] Cancel (Esc) + toasts on errors
- [x] Sidebar/sources persisted in localStorage
- [x] RAG status/sources visible; index and query wired to the real endpoints
- [x] Chat renders RAG citations with path, chunk and score
- [x] RAG errors surface as toasts instead of false success

## Docs

- [x] Product name: Bonsai Portable
- [x] `../README.md` + `ARCHITECTURE.md` + `MANUAL.md` aligned with rc.5
- [x] `../SECURITY.md` and `THREAT_MODEL.md`
- [x] `../CONTRIBUTING.md` with quality gates and non-negotiables
- [x] `docs/RAG.md`, `docs/PERSISTENCE.md`, `docs/OPERATIONS.md`,
  `docs/ACP.md`, `docs/PLUGINS.md`, `docs/API.md`, `docs/TESTING.md`
- [x] ADRs 001–008 recorded in `docs/ADRs/`
- [x] `Known limitations` documents opt-in RAG/persistence, at-rest encryption,
  Windows verification and dead modules
- [x] `MASK_GUIDE.md` says hash, not cryptographic signature
- [x] `INTEGRATION_GUIDE.md` says the linter is not mounted
- [x] Honest redteam/README (no false seccomp claims)
- [x] LICENSE: MIT (`LICENSE`)
