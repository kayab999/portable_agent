# Changelog

## 1.0.0-rc.5

### RAG, persistence and operational completion
- **RAG schema v2.** `documents` is now authoritative with unique
  `(source_type, root, path)` and root-relative paths, so duplicate basenames no
  longer collide. `chunks` references documents; `chunks_fts` is a content-backed
  FTS5 table maintained by insert/update/delete triggers. Old `fts_chunks` /
  `chunk_tokens` tables are dropped and rebuilt during a transactional v1→v2
  migration. `quality_base` is immutable and maintenance only writes the derived
  `quality_score`.
- **Idempotent, safe ingestion.** Unchanged content is skipped; changed content
  replaces chunks atomically; missing files are soft-deleted (`missing_file`);
  operator-deleted sources stay deleted across re-indexing. Symlinks escaping the
  root, oversized files and non-UTF-8 files are rejected with a report.
- **BM25 retrieval with FTS injection safety.** Every query token is quoted before
  `MATCH`, results are filtered to active documents, deduplicated by content hash,
  limited per document for diversity, and bounded by `rag_max_chars` with
  `[source: path#chunk]` citations. Empty/no-match queries return `[]`; there is
  no arbitrary fallback.
- **Durable experience queue.** Replaced the select-then-delete `event_queue`
  with an `events` table using payload idempotency keys, `BEGIN IMMEDIATE` claims,
  worker leases, stale-lease reclaim, exponential backoff and dead-lettering
  after five attempts. Legacy `event_queue` rows migrate automatically.
- **New HTTP APIs.** `/api/rag/status`, `/api/rag/sources`, `GET/POST
  /api/rag/query`, `/api/rag/index`, `/api/rag/sources/delete`,
  `/api/rag/maintenance`, `/api/experience/status`, `/api/experience/drain` and
  `/api/experience/purge`. The legacy `/api/rag` action contract is preserved.
  Index and maintenance jobs are mutually exclusive (`409 index_busy`). Queries
  accept `source_type`/`path_prefix` filters and drains honor `max_events`.
- **RAG in the prompt and UI.** Retrieved context is injected as an untrusted
  `<<<BONSAI_RAG_CONTEXT>>>` block via `context_builder`; `/api/chat` returns
  structured `citations`; the UI shows indexed status, indexes/queries the real
  endpoints and renders citation cards.
- **Safe maintenance.** `maintenance_cycle` is non-self-referential, supports
  `dry_run`, and hard-deletes only `experience` chunks by default. Purging user
  documents requires `purge_documents=true`. Metrics go to
  `data/maintenance_log.jsonl`.
- **UI/CSP hardening.** Extracted `web/styles.css` and `web/app.js`, removed all
  inline event handlers in favor of delegated `data-action` listeners, and
  tightened CSP to `script-src 'self'` (plus `object-src 'none'`,
  `base-uri 'self'`, `form-action 'self'`, COOP/CORP/Permissions-Policy).
- **Shared runtime and transport cleanup.** Added `runtime.py` (single composition
  root for plugins/MCP/hooks/permissions/subagents), `model_client.py` (gateway and
  ACP no longer import a private gateway class), `config_schema.py`,
  `doctor.py --doctor`, `bonsai_logging.py` (JSON logs with secret redaction) and
  `scripts/backup.sh` / `scripts/restore.sh`.
- **Privilege fix.** Subagent tools are now intersected with the active persona
  allowlist in addition to the agent declaration, global allowlist and power gate.
- **Fail-safe API inputs.** `purge`/`purge_documents`/`dry_run` only honor real
  JSON booleans, and RAG `path_prefix` escapes LIKE metacharacters so a filter
  cannot silently broaden or shift its scope.
- **Quality gates.** Branch coverage with per-module floors
  (`scripts/check_coverage.py`), import-linter contracts, optional `pip-audit`,
  web/CSP regression tests, a GitHub Actions matrix (Linux Python 3.11–3.13 plus
  a Windows PowerShell launcher job) and a dev-tooling guide
  (`requirements-dev.txt`, `docs/TESTING.md`).
- **Docs.** Added `SECURITY.md`, `docs/THREAT_MODEL.md`, `CONTRIBUTING.md`,
  `docs/RAG.md`, `docs/PERSISTENCE.md`, `docs/OPERATIONS.md`, `docs/ACP.md`,
  `docs/PLUGINS.md`, `docs/API.md` and ADRs 001–008.
- **Tests**: 295 tests, 294 passing + 1 skipped (`pwsh` absent), up from 220. New coverage:
  RAG schema/migration/retrieval/injection, experience lease/retry/dead-letter
  concurrency, gateway RAG/persistence APIs, static UI/CSP, config schema,
  doctor and log redaction.

### Interoperability and hardening
- **AGENTS.md**: loads project instructions from the repository root down to the
  working directory, later files overriding earlier ones, capped at 32 KB. The
  content is labeled untrusted project context, never policy.
- **Agent Skills**: reads `SKILL.md` (agentskills.io format) from `skills/`,
  `.agents/skills/`, `.claude/skills/`, `.opencode/skills/`, `.gemini/skills/`,
  and `~/.agents/skills/`. Minimal stdlib YAML frontmatter parser; duplicate
  names keep the first root and are reported. Progressive disclosure: compact
  index in the prompt, full body via `load_skill` or `/skill-name`.
- **Context pipeline**: fixed order (policy → tool protocol → AGENTS.md → skill
  index → active skill → MCP catalog) with explicit delimiters and a rule that
  external content cannot grant permissions.
- **MCP stdio client**: JSON-RPC `initialize`, `tools/list`, `tools/call`;
  namespaced `mcp__server__tool`; per-server `tools_allow`; sanitized
  environment; no shell; timeouts; 4 MB message / 64 KB result caps. MCP
  descriptions and results are untrusted. Remote HTTP/OAuth is out of scope.
- **Tool registry**: local tools and MCP tools resolve through one surface while
  preserving the persona allowlist and power-tool gate.
- **Lifecycle hooks**: six events (`session_start`, `user_prompt`, `pre_llm`,
  `pre_tool`, `post_tool`, `session_end`). Commands run without a shell, with a
  sanitized environment, a timeout, and a 4 KB output cap. Exit `2` or a JSON
  `{"decision":"block"}` blocks; `user_prompt`/`pre_llm`/`pre_tool` fail closed.
- **Permission policy**: per-tool `allow`/`ask`/`deny` with `mcp__*` wildcard.
  `ask` fails closed in the headless gateway and ACP unless explicitly listed in
  `auto_approve`. Policy is owner configuration and cannot be changed by skills,
  plugins, or MCP metadata.
- **Plugin adapters**: read-only manifests for Claude Code
  (`.claude-plugin/plugin.json`), Gemini CLI (`gemini-extension.json`), Hermes
  (`plugin.yaml`), and Antigravity (`plugin.json`). Skills, prompt-only agents,
  MCP definitions, and hook declarations are imported; JS/TS/Python entrypoints
  are reported as unsupported and never executed. Plugin MCP servers remain
  disabled unless `plugins_enable_mcp` is set.
- **Subagents**: Markdown definitions in `agents/`, `.agents/agents/`, and
  `.claude/agents/`. Declared tools are an upper bound intersected with
  `ALLOWED_TOOLS` and the power-tool gate. Exposed through `GET /api/subagents`
  and `POST /api/subagent` when `subagents_enabled` is true (off by default).
- **ACP v1 (stdio)**: `launcher.py --acp` or standalone `acp_server.py`.
  JSON-RPC 2.0 with `initialize`, `authenticate`, `session/new`,
  `session/prompt`, `session/cancel`, and `session/update`. It advertises no
  filesystem, terminal, image, audio, or embedded-context capabilities, rejects
  client-provided MCP servers, and routes tools through the same hooks,
  permissions, and sandbox.
- **A2A is a non-goal.** rc.5 is local-only and exposes no remote agent
  transport; this is documented rather than stubbed.
- **Skill scripts are not executed.** Skill directories become read-only roots
  so `references/` and `assets/` work; `scripts/` are inert.
- **Vault v2**: one atomically replaced `owner.lock` (0600) holds KDF, salt, and
  Encrypt-then-MAC canary. Rotation is a single temp-file + `fsync` +
  `os.replace`, so a crash cannot leave a half-rotated vault.
- **PBKDF2 600k**: vault derivation raised from 100k to 600,000 iterations
  (~0.4 s per unlock here). Legacy 100k vaults still verify and migrate on the
  first successful unlock. A v2 `owner.lock` never falls back to stale legacy
  files, preventing downgrade. Stored iterations are range-checked.
- **IPv6**: `::1` is rejected at startup with an actionable message. The stdlib
  `ThreadingHTTPServer` is IPv4-only, and the Host/Origin allowlists no longer
  imply IPv6 support.
- **`.gitignore`**: `data/knowledge.db` (+ WAL/SHM) treated as runtime state.
- **Lint**: ruff now enables bugbear (`B`); the existing `zip(strict=)`, unused
  loop variables, and duplicate stop-word findings are fixed.
- **Tests**: 220 tests, 219 passing + 1 skipped (`pwsh` absent), up from 132.
  New coverage: vault migration/downgrade resistance, IPv6 rejection, AGENTS.md
  precedence/budget, skill parsing/discovery/progressive disclosure, context
  ordering, MCP stdio dispatch/allowlists, hooks/permissions, plugin adapters,
  subagent tool restriction, and ACP session/cancel/update flows.

## 1.0.0-rc.4

### Security and honesty release
- **Fail-closed auth**: production boots locked. `POST /api/session/unlock` no longer issues tokens without a credential. Removed the `set_server_unlocked(None)` fail-open path and the `yubikey_auto=True` default.
- **Owner vault**: `python launcher.py --set-password` writes `owner.lock`, `data/vault_salt.bin`, and `data/vault_canary.txt` atomically with mode `0600`. Unlock is rate-limited (5/min) and verified with the Encrypt-then-MAC canary.
- **YubiKey**: challenge-response is verified against the vault; a missing/failed key leaves the server locked instead of unlocking.
- **CORS**: removed `Access-Control-Allow-Origin: *`. Only the gateway's exact loopback origin is reflected. Foreign origins cannot unlock or POST.
- **Host validation**: rejects invalid `Host` headers (DNS-rebinding defense).
- **`/v1` auth test**: bearer is required in production; `/health` and `/api/health` stay public.
- **Thread-safe SQLite**: `TFIDFIndex` now opens one connection per operation (WAL + 30 s busy timeout) instead of sharing a connection created in the main thread. 64 concurrent searches and concurrent read/write pass.
- **Strict tool parser**: only `<tool_call>` blocks execute. Fenced code blocks and standalone JSON lines are ignored unless `BONSAI_LEGACY_TOOL_PARSING=1`. Unknown tools parse but cannot execute.
- **Containment**: RAG and experience persistence are disabled by default (`rag_enabled: false`, `experience_persistence: false`). `/api/rag` and `/api/experience/drain` return `503 feature_unavailable`; `/api/health` reports the disabled features.
- **`agent_step` persistence** is opt-in (`persist=False`, `drain_queue=False` by default).
- **Windows**: rewrote `start.ps1` as a valid PowerShell 5.1 Python launcher; removed `?.Source`, the dead direct-mode server, and the nonexistent `:8081` UI.
- **Repo**: added `.gitignore`, `README.md`, `config.example.json`, and an MIT `LICENSE`; version bumped; docs corrected (`ARCHITECTURE`, `MANUAL`, `RC_DOD`, `MASK_GUIDE`, `INTEGRATION_GUIDE`).
- **Tests**: 132 tests, 131 passing + 1 skipped (`pwsh` absent) on Linux, was 89. New coverage: auth fail-closed, vault provisioning, origin/Host, `/v1` auth, SQLite concurrency, strict parser, feature containment, and production-mode E2E.

### Post-fix audit corrections
- **Session tokens**: after a verified unlock, `POST /api/session/unlock {}` now mints a token instead of returning `403`; the UI and CLI no longer re-prompt in a new tab/client until restart.
- **CLI**: `--cli` prompts for the vault password when production is locked, and prints an actionable message when stdin is not a TTY.
- **Gateway hardening**: `/v1` proxying rejects negative `Content-Length`.
- **Memory cache**: `TFIDFIndex.clear()` rebinds snapshots instead of mutating dictionaries a concurrent `search()` may still reference.
- **Containment**: `execution_loop` no longer forces `persist=True`/`drain_queue=True`; `agent_step` defaults to `use_memory=False`, so the default path touches no database.
- **Docs**: documented the YubiKey limitation and that LAN mode is unsupported in rc.4.

### Retracted from rc.3
The rc.3 DoD claimed RAG, experience persistence, and production security as done. All three were broken in the shipped configuration. rc.4 retracts the RAG/persistence claims and disables those features until the drain, reindex, scoring, and reflection work lands.

## 1.0.0-rc.3

### Setup completo, consolidación y E2E en CI
- **--setup sin pasos manuales**: descarga e instala llama-server del release oficial del fork PrismML (`PrismML-Eng/llama.cpp`, asset `llama-*-bin-*`) con extracción completa (binario + `.so` hermanos vía `$ORIGIN`), verificación SHA-256 opcional vía `BONSAI_BIN_SHA256` (fail-closed) y fallback con instrucciones manuales. Cobertura: linux x64/arm64, macOS x64/arm64, Windows x64/arm64. URL muerta `Bonsai-demo` corregida. `agentportable` ya no anuncia `--setup` inexistente (apunta al launcher)
- `runtime_core.py`: implementación compartida única (plataforma, catálogo/descubrimiento/selección de modelos, binario llama-server, config) para `launcher.py` + `agentportable.py`; cada entrypoint conserva su catálogo y mensajes. Bono: `agentportable` ahora hereda la política de bind LAN (fail-closed sin `BONSAI_ALLOW_LAN`)
- Nueva env var `BONSAI_CONFIG` para override del `config.json` (usada por pruebas/E2E)
- `start.ps1` promovido a la raíz (entry point Windows); layout v1 antiguo archivado en `archive/v1-files/`
- Dead code eliminado: `_serve_web_ui` y probe de identidad sin uso en launcher
- `knowledge_db.py`: eliminadas 4 definiciones duplicadas (`search_history/constants/authors/concepts`) que shadoweaban en silencio
- Lint: ruff con reglas curadas en `pyproject.toml`, repositorio a **0 findings** (F401/F541/F811/F841/E701/E702/E703/E722) y stage en `ci.sh`
- CI: stage E2E real (`tests/test_e2e_boot.py` + tinyllama 1.1 MB en `tests/test_data/`): arranca launcher, health de llama-server y gateway, inferencia real por `/v1`, SIGTERM con verificación de shutdown limpio y puertos liberados (skip si no hay binario/modelo)
- Suite: 89 unit + 25 adversariales + 9 audit + 1 E2E

## 1.0.0-rc.2

### Follow-up hardening
- Model discovery: any `.gguf` in `models/` is picked up as `custom:*` (not just catalog names); `--model` accepts an explicit path (`python launcher.py --model /ruta/modelo.gguf`) — implemented via `runtime_core` with `tests/test_model_discovery.py` (15 cases)
- Launcher shutdown: deterministic exit after SIGTERM (reap llama-server, no hang on exit; E2E verified with a real GGUF round-trip through gateway `/v1`)
- Command: reject shell metacharacters (`$`, `` ` ``, `;`, `|`, …); `ok` reflects exit code
- Path args with `$`/`~`/`backticks` rejected
- `io` removed from python_exec allowlist
- `execute_tool_calls` filters kwargs to method signature
- `filter_calls_by_persona(None)` fail-closed (no POWER_TOOLS)
- `agentportable` uses `resolve_persona_tools` (no silent write elevation)
- CLI talks to `/api/chat` with session token (no local unfiltered tools)
- Static web path: `relative_to` instead of string prefix
- Memory fingerprints: MD5 → SHA-256

### Security remediation (enterprise audit)
- **Command sandbox**: removed `python`/`python3`/`find` from whitelist; path-jail all path-like args
- **python_exec**: AST import/name allowlist + isolated `python -I`; blocks `os`/`open`/dunders
- **POWER_TOOLS** gate: `execute_command`, `python_exec`, `fetch_url` require `BONSAI_POWER_TOOLS=1` or DEBUG
- **Gateway auth**: session bearer (`session_auth.py`); required when not DEBUG
- **POST /api/tools** disabled outside DEBUG (agent loop only)
- **RAG index** path confined to docs/workspace/datasets
- **Bind policy**: non-loopback host clamped unless `BONSAI_ALLOW_LAN=1`
- **Body limit** 2 MiB + rate limits on expensive POSTs
- **Security headers**: CSP, nosniff, frame-deny
- **Crypto**: reject legacy v0 (no MAC) by default; `allow_legacy=True` for migration
- **Persona allowlists**: canonical tools only; no silent write/power elevation
- **diagnose**: no absolute ROOT leak unless power/debug
- **UI**: Bearer bootstrap via `/api/session/unlock`
- **Tests**: adversarial escape + gateway auth + persona suites in CI
- **Docs**: honest redteam/README and threat model in ARCHITECTURE

## 1.0.0-rc.1

### Critical fixes
- File tools: `_safe_path` NameError fixed
- Memory file ingest: NOT NULL timestamps fixed
- Package layout: root `launcher.py`, `web/`, `./start.sh`
- Unified agent tool protocol (`<tool_call>JSON</tool_call>`)
- Gateway: single origin for UI, `/api/*`, `/v1` proxy

### Security
- `DEBUG` requires `BONSAI_DEBUG=1`
- Path confinement via resolved relative_to roots
- Restricted `python_exec`, sandboxed `execute_command`
- SSRF blocking for private/link-local hosts
- Vault crypto v1 with HMAC integrity

### Features
- `/api/chat` agent loop with tools + RAG + PII redact
- Experience bus: queue + audit_worker drain
- UX: tool cards, markdown, cancel, localStorage sidebar

### Tests
- Expanded unit suite + audit suite; `scripts/ci.sh`
