# Experience persistence

Experience persistence lets the agent turn completed interactions into
searchable memory. It is **opt-in** (`experience_persistence: false`) and uses a
durable SQLite queue with claim/lease semantics.

## Why not a fire-and-forget queue

The rc.5 queue selected rows, indexed them, then deleted them. Concurrent
drainers could process the same event twice, and a crash between indexing and
deletion lost track of the event. The replacement:

- deduplicates enqueue with an idempotency key;
- claims batches inside `BEGIN IMMEDIATE`;
- leases claims so crashed workers can be reclaimed;
- retries with exponential backoff;
- dead-letters after five attempts instead of dropping;
- keeps completed events for a retention window.

## Schema

```sql
events(
  id, event_type, payload, idempotency_key UNIQUE,
  status,              -- pending | processing | done | dead
  attempts, available_at,
  locked_by, locked_at,
  last_error,
  created_at, updated_at, processed_at
)
```

Indexes cover ready work (`status, available_at, id`) and lease reclaim
(`status, locked_at`).

## Semantics

| Situation | Behavior |
|---|---|
| Duplicate enqueue | `INSERT OR IGNORE`; returns `0`, no duplicate row |
| Normal drain | claim up to `max_events`, index each, mark `done` |
| Handler raises | `attempts += 1`, status `pending`, backoff `30s × 2^(n-1)` capped at 1h |
| Attempts ≥ 5 | status `dead`, error retained |
| Worker crash | expired lease (>120s) is reset to `pending` on the next claim |
| Malformed payload | treated as a failure and eventually dead-lettered |
| Purge | deletes terminal events older than the retention window (default 7 days) |

## Recording a turn

`agent_loop.enqueue_experience` accepts the full turn object:

```python
{
  "query": "...",
  "plan": "...",
  "result": "...",
  "final": "...",
  "reflection": "...",     # nullable; never fabricated by the gateway
  "success": true,
  "model": "...",
  "tool_results": [{"tool": "read_file", "ok": true}]
}
```

The worker writes a human-readable retrieval document:

```
Query: ...
Plan: ...
Result: ...
Final: ...
Reflection: ...
Success: True
RAW_JSON: {...}          # capped at 4000 characters
```

The event id is part of the document path (`experience/<id>`), so retries are
idempotent at the RAG layer as well.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/experience/status` | pending/processing/done/dead counts, oldest pending age |
| `POST` | `/api/experience/drain` | drain a batch, return processed count and status |
| `POST` | `/api/experience/purge` | `{older_than_days:7}` purge terminal events |

All require auth and respect the feature flag (`503 feature_unavailable` when
disabled).

## Privacy

Experience documents can contain user prompts, tool output and model answers.
They live in the same `data/vector_db/memory.db` as RAG documents and inherit
the same at-rest protections (none beyond filesystem permissions). Disable
persistence, delete `memory.db`, or purge events if this is not acceptable.
