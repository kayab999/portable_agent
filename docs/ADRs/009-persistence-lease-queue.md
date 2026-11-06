# ADR 009 — Experience queue uses claim/lease/retry

Status: accepted

## Decision
Persist experiences in an `events` table with idempotency keys, status,
attempts, leases, backoff and dead-lettering.

## Rationale
Select-then-delete is not crash-safe and duplicates work under concurrency.
Leases make worker crashes recoverable without dropping events.

## Consequences
- A worker may re-process an event after a crash; the RAG writer is idempotent
  by event id.
- Operators can inspect and purge dead letters.
