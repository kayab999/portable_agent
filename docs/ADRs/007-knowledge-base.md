# ADR 007 — Knowledge base promotion

## Status

Accepted for implementation behind `knowledge_db_enabled` (default false).

## Context

`knowledge_db.py` already contains a structured SQLite/FTS5 store with
formulas, history, constants, authors, and concepts. It is currently legacy and
disabled by default. The product needs deterministic answers for exact factual
data without relying on RAG hallucination.

## Decision

Promote the store through a read-only `KnowledgeRouter`:

- `KnowledgeDB` remains the only owner of the SQLite schema and seed data.
- `KnowledgeRouter` is the only query entry point.
- Routing is deterministic and parameterized.
- Results use a normalized citation schema.
- The capability is disabled by default and gated by config, permissions,
  persona allowlists, and hooks.
- Knowledge context injection is separately gated by
  `knowledge_context_enabled`.

## Consequences

- Exact factual queries can be answered without RAG.
- RAG remains the fallback for unstructured context.
- The legacy module must be migrated, not duplicated.
- Additional tests and security review are required before default-on.