# Bonsai Portable

[![CI](https://github.com/kayab999/portable_agent/actions/workflows/ci.yml/badge.svg)](https://github.com/kayab999/portable_agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Local, single-user AI runtime with a sandboxed agent loop, a same-origin web UI,
and an OpenAI-compatible `/v1` proxy backed by `llama-server`.

Version: `1.0.0-rc.5` (see `VERSION`).

> **Release scope:** rc.4 hardened auth, CORS/Host, SQLite, parsing, and the
> Windows launcher. rc.5 adds fail-closed compatibility surfaces: AGENTS.md
> context, read-only Agent Skills, an opt-in stdio MCP client, lifecycle hooks,
> a per-tool permission policy, declarative plugin adapters, markdown subagents,
> and an ACP v1 stdio server. RAG (FTS5/BM25, schema v2) and experience
> persistence (claim/lease queue) are now complete but remain **opt-in** for
> privacy. See [Known limitations](#known-limitations).

## Requirements

- Python 3.11.4+ (tarfile extraction filter and stdlib APIs)
- A `llama-server` binary for your platform (`python launcher.py --setup`)
- At least one `.gguf` model under `models/`

## Quick start

```bash
# Download the llama-server binary and choose models (optional interactive)
python3 launcher.py --setup

# Create the owner password vault (first production run)
python3 launcher.py --set-password

# Start gateway + UI + model
./start.sh
# or: python3 launcher.py --no-browser
```

The gateway serves the UI at `http://127.0.0.1:8080` and proxies inference at
`http://127.0.0.1:8080/v1`. The internal `llama-server` listens on
`port + llm_port_offset` (default `8180`) and is not exposed.

### Development mode

```bash
BONSAI_DEBUG=1 ./start.sh          # no auth, direct tools, power tools
BONSAI_INSECURE=1 ./start.sh       # issues tokens without a password (dev only)
BONSAI_POWER_TOOLS=1 ./start.sh    # enable shell / fetch_url
BONSAI_UNSAFE_PYTHON_EXEC=1 ./start.sh  # also allow non-isolated python_exec
```

`BONSAI_DEBUG=1` and `BONSAI_INSECURE=1` print a loud warning and must not be
used for a distributed build. `python_exec` is not an OS sandbox and stays
disabled even with power tools unless `BONSAI_UNSAFE_PYTHON_EXEC=1` is set.

## Authentication

Production boots **locked**:

- Without `owner.lock`, `POST /api/session/unlock` returns
  `403 not_provisioned`. There is no default password and no fail-open path.
- `python launcher.py --set-password` writes a single `owner.lock` file
  (mode `0600`) containing the KDF parameters, salt, and Encrypt-then-MAC
  canary. Rotation is one atomic `os.replace`.
- PBKDF2-HMAC-SHA256 runs at 600,000 iterations. Existing rc.3 vaults
  (separate salt/canary files at 100k iterations) still unlock and are
  re-encrypted at the current cost on first successful verification.
- Unlock is rate-limited (5 attempts/minute) and returns a bearer token.
- A client holding a valid token can mint another token. Without one,
  `POST /api/session/unlock` requires the vault password; process-level unlock
  state alone (including YubiKey auto-unlock) is not authorization.
- For hardware unlock, the launcher hands a short-lived token only to the
  CLI/browser it starts; the public endpoint never mints from unlock state.
- `./start.sh --cli` prompts for the vault password when the server is locked.
- `/api/*` and `/v1/*` require that token. `/health` and `/api/health` are public.
- CORS and Host headers are restricted to the gateway's own loopback origin.
- `--yubikey` verifies the challenge-response against the owner vault; a missing
  or failed key leaves the server locked. This only succeeds if the vault was
  provisioned with a password equal to the YubiKey response, so treat hardware
  unlock as experimental.

Tokens live in RAM only and expire after 24 hours (or on restart).

## Feature status

| Capability | Status |
|---|---|
| One-command start (Linux/macOS) | Working |
| One-command start (Windows) | `start.ps1`, Python-first; parse-tested in CI when `pwsh` is available |
| Gateway UI + `/api/*` + `/v1` proxy | Working |
| Password vault / session auth | Working |
| Origin/Host hardening | Working |
| Sandboxed read/write/list/stats tools | Working |
| `python_exec` (opt-in, no OS isolation) | Working; separate unsafe gate |
| `knowledge_lookup` (opt-in knowledge DB) | Working; `knowledge_db_enabled` |
| `execute_command` / `fetch_url` | Working, power-gated |
| Persona tool allowlists | Working |
| Canonical `<tool_call>` protocol | Working, strict by default |
| AGENTS.md project context | Working, read-only, capped at 32 KB |
| Agent Skills (`SKILL.md`) | Working, read-only; scripts never executed |
| MCP tools (stdio) | Opt-in (`mcp_enabled`), namespaced, allowlisted |
| MCP remote HTTP / OAuth | Not implemented |
| Lifecycle hooks | Opt-in (`hooks.enabled`), no shell, fail-closed pre-events |
| Per-tool permissions (`allow`/`ask`/`deny`) | Working; `ask` fails closed headless |
| Plugin manifests (Claude/Gemini/Hermes/Antigravity) | Read-only declarative import; code never executed |
| Subagents (`agents/*.md`) | Working, restricted tool intersection |
| ACP v1 (stdio) | Opt-in (`launcher.py --acp`) |
| A2A | **Out of scope** for local-only deployments |
| RAG index/retrieval (FTS5/BM25, schema v2) | Working, opt-in (`rag_enabled`) |
| Experience persistence (lease/retry/dead-letter) | Working, opt-in (`experience_persistence`) |
| Maintenance/GC scoring | Working, dry-run and document-safe purge |
| RAG API + citations + UI indicator | Working |
| Knowledge DB / prompt linter | Legacy, unreachable from production |

## Compatibility

Bonsai reads the ecosystem's declarative surfaces. It never executes foreign
plugin code.

| Surface | Behavior |
|---|---|
| `AGENTS.md` | Loaded root→cwd, later files override; 32 KB cap |
| Agent Skills | `skills/`, `.agents/skills/`, `.claude/skills/`, `.opencode/skills/`, `.gemini/skills/`, `~/.agents/skills/` |
| Skill activation | Compact index in the prompt; `load_skill(name)` or `/skill-name` loads the body |
| MCP (stdio) | Config-declared servers; tools exposed as `mcp__server__tool` |
| Plugin packages | Read-only manifest adapters for Claude Code, Gemini CLI, Hermes, and Antigravity; only skills/agents/MCP/hook declarations are imported |
| Subagents | Markdown files in `agents/`, `.agents/agents/`, `.claude/agents/`; declared tools are intersected with the global allowlist |
| Hooks | Six lifecycle events; JSON on stdin, exit `2` or `{"decision":"block"}` blocks |
| ACP | `python3 launcher.py --acp`; JSON-RPC v1 over stdio, no fs/terminal bridge |

Skill directories are read-only roots for `read_file`, so `references/` and
`assets/` work. `scripts/` are never executed by Bonsai.

Example MCP configuration:

```json
{
  "mcp_enabled": true,
  "mcp_servers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/data"],
      "enabled": true,
      "tools_allow": ["read_file", "list_directory"]
    }
  ]
}
```

The client speaks MCP `2026-07-28`: it probes with `server/discover`, sends
protocol version, client identity and capabilities in per-request `_meta`, and
enforces `resultType`. If a stdio server does not implement the modern
revision, the client falls back to the legacy `initialize` handshake. A
recognized modern error never triggers that fallback.

`tools_allow` is optional. Omit it to expose every tool advertised by the
server; an explicit empty list exposes none. Calls for tools that were not
advertised or not allowlisted are rejected even if invoked directly.

MCP tool descriptions and results are treated as untrusted content. Every call
is gated by the configured server allowlist, the permission policy and the
active persona.

## Hooks and permissions

Both surfaces are owner-controlled and off by default. External content cannot
create or alter them.

```json
{
  "hooks": {
    "enabled": true,
    "pre_tool": [
      {
        "command": "python3",
        "args": ["hooks/check_tool.py"],
        "matcher": "write_file",
        "timeout_ms": 3000
      }
    ]
  },
  "tool_permissions": {
    "default": "allow",
    "rules": {"write_file": "ask", "mcp__*": "deny"},
    "auto_approve": []
  }
}
```

- Events: `session_start`, `user_prompt`, `pre_llm`, `pre_tool`, `post_tool`,
  `session_end`.
- Hooks receive a JSON payload on stdin, run without a shell, with a sanitized
  environment and a bounded timeout/output.
- Exit `0` allows, exit `2` blocks, other exits warn without blocking. A
  `{"decision": "block", "reason": "..."}` object on stdout also blocks.
- `user_prompt`, `pre_llm`, and `pre_tool` fail closed on timeout or start
  errors. Post-events do not block.
- `ask` requires interactive approval; headless gateway and ACP turns treat it
  as `deny` unless the tool is listed in `auto_approve`.
- Hooks and permissions load independently. An invalid hook configuration never
  disables the permission policy; an invalid permission configuration falls
  back to a deny-all policy.
- MCP tools (`mcp__*`) are denied unless a rule explicitly allows them. A
  missing policy never authorizes an external tool.

## Plugins and subagents

`plugins_enabled: true` reads manifests without executing plugin code:

| Package | Manifest |
|---|---|
| Claude Code | `.claude-plugin/plugin.json` |
| Gemini CLI | `gemini-extension.json` |
| Hermes | `plugin.yaml` |
| Antigravity | `plugin.json` |

Skills, prompt-only agents, MCP server definitions, and hook declarations are
imported. `package.json`, `index.js`, `index.ts`, `main.py`, and similar runtime
entrypoints are reported as unsupported and never run. Plugin MCP servers stay
disabled unless `plugins_enable_mcp: true` is also set.

Subagents are Markdown files with frontmatter:

```markdown
---
name: reviewer
description: Reviews a change for regressions
tools: [read_file, list_dir, grep]
---

You are a careful reviewer. Report findings without modifying files.
```

Declared tools are an upper bound: they are intersected with the global
allowlist and the power-tool gate. Without a `tools` list, subagents get the
normal non-power toolset. Exposed at `GET /api/subagents` and
`POST /api/subagent` when `subagents_enabled` is true. Subagents are **off by
default**; enable them in `config.json` after reviewing the agent files.

## ACP (Agent Client Protocol)

```bash
python3 launcher.py --acp
# or attach to an existing llama-server:
BONSAI_LLM_BASE=http://127.0.0.1:8180 python3 acp_server.py
```

ACP v1 runs JSON-RPC 2.0 over stdin/stdout and supports `initialize`,
`authenticate`, `session/new`, `session/prompt`, `session/cancel`, and
`session/update` notifications. It advertises no filesystem, terminal, image,
audio, or embedded-context capabilities. `session/new` rejects client-provided
MCP servers, and tool calls still pass through hooks, permissions, and the
workspace sandbox. A2A is an explicit non-goal: the product is local-only and
has no remote agent transport.

## Security model

- Default bind is loopback. `BONSAI_ALLOW_LAN=1` permits a non-loopback bind but
  LAN is **not supported** yet: Host validation only accepts loopback names and
  will reject LAN clients.
- The gateway is a `ThreadingHTTPServer`; SQLite access uses one connection per
  operation (WAL + busy timeout), so concurrent requests are safe.
- Read roots: `workspace/`, `docs/`, and `data/KAYAB_DATASETS` when it exists.
  Write root: `workspace/`.
- Subprocess tools use a command whitelist without interpreters, an allowlisted
  flag set, and a workspace cwd. Every path argument, including bare relative
  names and symlink targets, is resolved and must stay under the read roots.
  `python_exec` is opt-in via `BONSAI_UNSAFE_PYTHON_EXEC=1`; it uses an AST
  pre-filter and `python -I` but has no OS isolation, so the filter is not a
  security boundary against arbitrary Python.
- SSRF protection blocks loopback, private, link-local, CGNAT and other
  non-global destinations. Redirects are followed manually and validated before
  each connection; the socket connects to the validated IP while TLS keeps the
  original hostname for SNI and certificate verification. Environment proxies
  are not used by `fetch_url`, redirects share one timeout budget, and response
  bodies are capped at 1 MiB with a truncation flag.
- Tool JSON is only executed from explicit `<tool_call>...</tool_call>` blocks.
  Fenced code and standalone JSON lines are ignored unless
  `BONSAI_LEGACY_TOOL_PARSING=1` is set.
- AGENTS.md, skill bodies, MCP tool descriptions, and MCP results are delimited
  and labeled untrusted. They cannot grant permissions or override policy.
- External content influence is separated from execution: a skill declaring
  `allowed-tools` never expands the persona/global allowlist.
- MCP servers run as child processes with a sanitized environment, no shell, a
  cwd jail, timeouts, and output caps. Local MCP servers still have the host
  user's filesystem access; enable only servers you trust.
- Hooks are local commands declared only in the owner's configuration. They run
  without a shell and receive only an allowlisted environment; secrets in the
  parent process are not forwarded.
- `ask` permissions cannot be answered by the headless gateway or ACP, so they
  fail closed unless explicitly auto-approved.
- Plugin adapters parse declarative metadata only. JavaScript, TypeScript, and
  Python plugin entrypoints are never imported or executed.

This is **not** an OS sandbox and not multi-tenant. A process with filesystem
access to the vault can delete or replace it.

## Known limitations

- **RAG and persistence are opt-in.** They are fully implemented but disabled
  by default (`rag_enabled: false`, `experience_persistence: false`) because
  indexing local files and retaining turns are privacy decisions.
- **RAG is lexical.** FTS5/BM25 is the baseline; optional local embeddings are
  not part of 1.0.
- **`knowledge_db.py` is now an opt-in capability** (`knowledge_db_enabled`);
  `linter.py` remains legacy and unreachable from production.
- `agentportable.py` is a legacy compatibility shim that delegates to
  `launcher.main()`.
- **IPv6 is not supported.** `ThreadingHTTPServer` is IPv4-only, so a configured
  `::1` is rejected at startup with a clear message instead of failing at bind.
- **Skill scripts are not executed.** `scripts/` is readable but inert; there is
  no OS sandbox to run third-party code safely.
- **MCP is stdio-only.** Remote HTTP/SSE and OAuth are explicit non-goals.
- **Plugin support is declarative.** Plugin code is never executed; plugin MCP
  servers remain disabled until `plugins_enable_mcp` is enabled.
- **A2A is out of scope.** ACP is the only editor integration and is stdio-only.
- **LAN mode is not supported.** `BONSAI_ALLOW_LAN=1` permits a non-loopback
  bind, but Host validation only accepts loopback hostnames, so LAN clients are
  rejected. Loopback is the only tested configuration.
- **YubiKey unlock is experimental** and only works when the vault password is
  the hardware challenge-response.
- **Windows/macOS are best-effort.** Linux is the first-class CI target; the
  `start.ps1` path needs a real Windows runner to be claimed as verified.
- **No at-rest encryption for RAG/persistence data.** Use full-disk encryption
  if the device may be stolen.
- No LAN mode, no multi-user auth, no OS-level sandbox.

## Configuration

`config.json` is local state and ignored by git. Start from the example:

```bash
cp config.example.json config.json
BONSAI_CONFIG=/path/to/config.json ./start.sh
```

## Tests

```bash
./scripts/ci.sh
python3 -m unittest discover -s tests -v
python3 redteam/run_all.py
python3 launcher.py --doctor
```

See `docs/THREAT_MODEL.md` for the threat-to-test mapping.

The rc.5 regression suite covers fail-closed auth, single-file vault rotation,
PBKDF2 cost and legacy migration, origin/Host validation (including IPv6
rejection), `/v1` auth, concurrent SQLite access, strict tool parsing, feature
containment, AGENTS.md loading, Agent Skills parsing/discovery, MCP stdio
dispatch, hooks and permission enforcement, plugin adapters, subagent tool
restriction, ACP session/cancel flows, and production-mode E2E. Current count:
**295 tests** (1 skipped when `pwsh` is absent), plus coverage floors,
import contracts, web/CSP checks, RAG migration/relevance tests, experience
lease/retry tests and production probes.

## License

MIT — see [`LICENSE`](LICENSE).
