# HTTP API reference

All endpoints are served on `http://127.0.0.1:<port>/` and require
`Authorization: Bearer <token>` for `/api/*` and `/v1/*` unless the process is
in explicit debug/insecure mode. Responses are JSON unless noted.

## Public

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/health` | gateway/LLM/feature status |
| `GET` | `/api/session/status` | auth required, provisioned, unlocked |
| `POST` | `/api/session/unlock` | issue a token after a verified credential |
| `GET` | `/` and static assets | web UI (`/app.js`, `/styles.css`) |
| `GET` | `/health`, `/v1/*` | loopback proxy to llama-server |

## Identity and tools

| Method | Path | Notes |
|---|---|---|
| `GET/POST` | `/api/identity` | read/write identity (persona, owner) |
| `POST` | `/api/identity/reset` | reset identity |
| `GET` | `/api/personas` | persona catalog |
| `GET/POST` | `/api/tools` | direct tool execution; disabled unless debug |
| `GET` | `/api/subagents` | list discovered subagents (opt-in) |
| `POST` | `/api/subagent` | run one subagent turn |
| `GET` | `/api/config` | effective runtime config (no secrets) |

## Chat

| Method | Path | Body |
|---|---|---|
| `POST` | `/api/chat` | `{message, messages?, persona?, rag?, model?, max_iterations?, extra_context?}` |
| `POST` | `/api/agent` | alias of `/api/chat` |

Response includes `reply`, `tools`, `rag_used`, `citations`, `redacted` and a
trimmed message history. RAG citations are `{citation, path, chunk, score,
source_type}`.

## RAG

| Method | Path | Body / query |
|---|---|---|
| `GET` | `/api/rag/status` | schema, counts, size, last index |
| `GET` | `/api/rag/sources` | indexed sources |
| `GET` | `/api/rag/query` | `q`/`query`, `top_k`, `source_type`, `path_prefix` |
| `POST` | `/api/rag/query` | `{query, top_k}` |
| `POST` | `/api/rag/index` | `{path}` or `{paths:[...]}` |
| `POST` | `/api/rag/sources/delete` | `{id, purge?}` |
| `POST` | `/api/rag/maintenance` | `{dry_run?, purge_documents?}` |
| `POST` | `/api/rag` | legacy `{action: ...}` |

## Knowledge base

| Method | Path | Body / query |
|---|---|---|
| `GET` | `/api/knowledge/status` | enabled flag and table counts |
| `POST` | `/api/knowledge/query` | `{query, mode?, limit?}` |

`mode` is `auto`, `structured`, `rag`, or `hybrid`. The endpoint requires a
bearer token and is rate limited. Feature-disabled responses use
`{error: "knowledge_db_disabled"}`.

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/experience/status` | queue counts |
| `POST` | `/api/experience/drain` | `{max_events?}` |
| `POST` | `/api/experience/purge` | `{older_than_days?}` |

## Agent runs

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/runs` | list recent runs (auth required) |
| `GET` | `/api/runs/{run_id}` | run metadata and steps |
| `GET` | `/api/runs/{run_id}/trace` | redacted trace events |

Runs are disabled by default (`agent_runs_enabled`). Tracing is separately
controlled by `agent_tracing_enabled`.

| Status | Meaning |
|---|---|
| `400` | invalid JSON/params |
| `401/403` | missing token, bad Origin/Host, blocked by hook, forbidden path |
| `404` | unknown route/source |
| `409` | index job already running |
| `413` | body/query too large |
| `429` | rate limit |
| `500` | internal error |
| `502` | upstream LLM error |
| `503` | feature disabled |

Error bodies use `{error, message?}`. Feature-disabled bodies also include
`feature`.
