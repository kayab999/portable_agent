# RAG (local retrieval)

Bonsai Portable ships a **fully functional lexical RAG** built on SQLite FTS5
and BM25. It has zero runtime dependencies and never sends documents off the
machine.

RAG remains **opt-in** (`rag_enabled: false`) because indexing local files is a
privacy decision. Enable it explicitly:

```json
{
  "rag_enabled": true,
  "rag_max_chars": 6000,
  "rag_top_k": 3
}
```

## Data model (schema v2)

```sql
schema_meta(key, value)             -- version = 2
documents(id, source_type, path, root, content_hash, mtime, size,
          status, indexed_at, error)
chunks(id, document_id, file_path, chunk_index, ordinal, content,
       file_hash, hash, quality_base, quality_score, is_active, ...)
chunks_fts                         -- FTS5 external-content table
doc_stats                          -- legacy compatibility summary
```

- `documents` is authoritative. Paths are **relative to the index root**, so
  `a/notes.md` and `b/notes.md` never collide.
- `chunks_fts` is kept in sync by insert/delete/update triggers; the runtime
  never edits it directly.
- `quality_base` is immutable owner-supplied quality. `quality_score` is a
  maintenance snapshot and is never fed back into scoring.
- `status` is `active`, `deleted` (user or missing file) or `error`.

## Ingestion

Supported sources: `.txt`, `.md`, `.markdown`, `.rst`, `.csv`, `.json`.
Index roots: `docs/`, `workspace/`, `data/KAYAB_DATASETS`.

Rules:

- Files are read as UTF-8; invalid files are reported and skipped.
- Files larger than 10 MiB are skipped.
- Symlinks escaping the root are rejected.
- Chunking is overlapping word windows (512 words, 64 overlap), capped at
  2000 chunks per file.
- Repeated indexing is idempotent: unchanged content is skipped.
- A changed file replaces its chunks atomically.
- A file missing from disk is **soft-deleted**; its chunks are never returned.
- A source deleted by the operator stays deleted across re-indexing.

Experiences drained from the persistence queue are indexed as
`source_type='experience'` with path `experience/<event-id>`.

## Retrieval

- Query tokens are lowercased, stop-word filtered and individually quoted
  before `MATCH`; FTS operators (`*`, `:`, `NEAR`, parentheses) cannot be
  injected.
- Ranking is BM25 (`bm25(chunks_fts)`), best first.
- Only `is_active=1` chunks belonging to `status='active'` documents are
  returned.
- Near-duplicate chunks are removed by content hash; at most two chunks per
  document are returned (diversity).
- An empty query or no matches returns an **empty list**. There is no
  arbitrary fallback.
- Context is bounded by `rag_max_chars` and labelled with citations:
  `[source: relative/path#chunk]`.
- Optional `source_type` and `path_prefix` filters narrow results. Prefixes are
  matched literally (`%` and `_` are escaped, never treated as wildcards).
- Destructive API options (`purge`, `purge_documents`, `dry_run`) require real
  JSON booleans; strings never enable a purge or disable a dry run.

## HTTP API

All endpoints require the normal session auth and rate limiting.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/rag/status` | schema version, documents, chunks, DB size, last index |
| `GET` | `/api/rag/sources` | indexed sources |
| `GET` | `/api/rag/query?q=...&top_k=3&source_type=...&path_prefix=...` | query (read-only) |
| `POST` | `/api/rag/query` | `{query, top_k, source_type?, path_prefix?}` |
| `POST` | `/api/rag/index` | `{path}` or `{paths:[...]}`; confined to allowed roots |
| `POST` | `/api/rag/sources/delete` | `{id, purge?:false}`; soft delete by default |
| `POST` | `/api/rag/maintenance` | `{dry_run:true, purge_documents:false}` |
| `POST` | `/api/rag` | legacy `{action: list|status|index|query|delete|maintenance}` |

Only one index/maintenance job can run at a time (`409 index_busy`).

## Prompt safety

RAG context is injected through `context_builder.build_system_prompt` as a
`<<<BONSAI_RAG_CONTEXT>>>` untrusted block. Document content cannot close the
delimiter, grant tools, or change permissions. The `/api/chat` response also
returns structured `citations` for the UI.

## Maintenance

`maintenance_cycle`:
- backfills defaults and re-scores retention from `quality_base`;
- soft-kills low-quality, low-usage chunks;
- hard-deletes only `experience` chunks by default;
- `purge_documents=true` is required to hard-delete user-document chunks;
- `dry_run=true` computes a summary without writing.

Maintenance metrics are written to `data/maintenance_log.jsonl`.

## Migration from rc.5

Opening an older database automatically:
1. creates `schema_meta`/`documents`;
2. adds `document_id`, `ordinal`, `quality_base`, `token_count`;
3. backfills documents from chunk paths;
4. drops the old `fts_chunks`/`chunk_tokens` pair;
5. rebuilds `chunks_fts` from chunks;
6. records schema version 2.

The migration is covered by `tests/test_rag_store.py` and preserves searchable
content.

## Retrieval evaluation

`scripts/evaluate_rag.py` builds a temporary index with the production
`TFIDFIndex` and measures Recall@1/3/5, MRR and malformed-query resilience over
a small Spanish/English corpus. It is a baseline, not a benchmark claim:

```bash
python scripts/evaluate_rag.py --output dist/rag_eval.json
```

CI publishes the JSON alongside the SBOM. `tests/test_rag_eval.py` fails if the
default corpus drops below Recall@1 = 0.8 or MRR = 0.8.
