#!/usr/bin/env python3
"""experience_store.py — durable, idempotent experience event queue.

The queue uses claim/lease semantics so multiple drainers (threads or
processes) cannot process the same event twice:

* ``enqueue`` is idempotent through a payload-derived key.
* ``claim_batch`` runs inside ``BEGIN IMMEDIATE`` and marks the batch as
  ``processing`` with a lease.
* Stale leases are reclaimed automatically.
* Failures back off exponentially and move to ``dead`` after
  ``MAX_ATTEMPTS``; nothing is silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from local_memory import DB_PATH

MAX_ATTEMPTS = 5
LEASE_SECONDS = 120
RETENTION_SECONDS = 7 * 24 * 60 * 60
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600

EXPERIENCE_CATEGORIES = frozenset(
    {
        "model_assertion",
        "tool_observation",
        "verified_result",
        "operator_approved",
        "unverified_experience",
    }
)


def classify_experience(payload: dict) -> str:
    """Classify an experience by provenance, defaulting to unverified."""
    if not isinstance(payload, dict):
        return "unverified_experience"
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        category = provenance.get("category")
        if category in EXPERIENCE_CATEGORIES:
            return str(category)
    if payload.get("tool_results"):
        return "tool_observation"
    if payload.get("success"):
        return "model_assertion"
    return "unverified_experience"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at INTEGER NOT NULL,
    locked_by TEXT,
    locked_at INTEGER,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    processed_at INTEGER,
    category TEXT NOT NULL DEFAULT 'unverified_experience'
);
CREATE INDEX IF NOT EXISTS idx_events_ready
    ON events(status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_events_lease
    ON events(status, locked_at);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    return conn


def _idempotency_key(event_type: str, payload: str) -> str:
    canonical = json.dumps(
        {"type": event_type, "payload": json.loads(payload)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(events)")
    }
    if "category" not in columns:
        conn.execute(
            "ALTER TABLE events ADD COLUMN category TEXT "
            "NOT NULL DEFAULT 'unverified_experience'"
        )
    # Migrate the rc.5 fire-and-forget queue if it is still present.
    legacy = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_queue'"
    ).fetchone()
    if legacy is not None:
        rows = conn.execute(
            "SELECT event_type, payload, timestamp FROM event_queue ORDER BY id"
        ).fetchall()
        for row in rows:
            payload = row["payload"]
            try:
                key = _idempotency_key(row["event_type"], payload)
            except (ValueError, TypeError):
                key = hashlib.sha256(
                    f"{row['event_type']}:{payload}".encode("utf-8")
                ).hexdigest()
            conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_type, payload, idempotency_key, status,
                    available_at, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    row["event_type"],
                    payload,
                    key,
                    int(row["timestamp"] or time.time()),
                    int(row["timestamp"] or time.time()),
                    int(row["timestamp"] or time.time()),
                ),
            )
        conn.execute("DROP TABLE event_queue")


def enqueue(
    event_type: str,
    payload: dict,
    *,
    db_path: Path | None = None,
    key: str | None = None,
    available_at: int | None = None,
) -> int:
    """Insert an event. Returns the row id, or 0 when it was a duplicate."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    idem = key or _idempotency_key(event_type, body)
    now = int(time.time())
    category = classify_experience(payload)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO events
               (event_type, payload, idempotency_key, status, attempts,
                available_at, created_at, updated_at, category)
               VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)""",
            (
                event_type,
                body,
                idem,
                available_at if available_at is not None else now,
                now,
                now,
                category,
            ),
        )
        if cur.rowcount == 0:
            return 0
        return int(cur.lastrowid)
    finally:
        conn.close()


def _claim_batch(
    conn: sqlite3.Connection,
    *,
    limit: int,
    worker_id: str,
    lease_seconds: int = LEASE_SECONDS,
) -> list[sqlite3.Row]:
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Reclaim expired leases so a crashed drainer cannot strand events.
        conn.execute(
            """UPDATE events
               SET status='pending', locked_by=NULL, locked_at=NULL,
                   updated_at=?
               WHERE status='processing' AND COALESCE(locked_at, 0) < ?""",
            (now, now - lease_seconds),
        )
        rows = conn.execute(
            """SELECT * FROM events
               WHERE status='pending' AND available_at <= ?
               ORDER BY id ASC LIMIT ?""",
            (now, max(1, int(limit))),
        ).fetchall()
        for row in rows:
            conn.execute(
                """UPDATE events
                   SET status='processing', locked_by=?, locked_at=?,
                       attempts=attempts+1, updated_at=?
                   WHERE id=?""",
                (worker_id, now, now, row["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return [dict(row, attempts=int(row["attempts"]) + 1) for row in rows]


def _complete(conn: sqlite3.Connection, event_id: int) -> None:
    now = int(time.time())
    conn.execute(
        """UPDATE events
           SET status='done', locked_by=NULL, locked_at=NULL,
               processed_at=?, updated_at=?, last_error=NULL
           WHERE id=?""",
        (now, now, event_id),
    )


def _fail(
    conn: sqlite3.Connection,
    event_id: int,
    error: str,
    *,
    attempts: int,
    max_attempts: int = MAX_ATTEMPTS,
) -> str:
    now = int(time.time())
    message = str(error)[:2000]
    if attempts >= max_attempts:
        status = "dead"
        available_at = now
    else:
        status = "pending"
        backoff = min(BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)), BACKOFF_MAX_SECONDS)
        available_at = now + backoff
    conn.execute(
        """UPDATE events
           SET status=?, available_at=?, locked_by=NULL, locked_at=NULL,
               last_error=?, updated_at=?
           WHERE id=?""",
        (status, available_at, message, now, event_id),
    )
    return status


def drain(
    handler,
    *,
    db_path: Path | None = None,
    max_events: int = 50,
    worker_id: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Claim and process a batch.

    ``handler(event, payload)`` receives the full claimed row (including its
    id) and the decoded payload. It may raise; failures are retried.
    """
    worker = worker_id or f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
    conn = _connect(db_path)
    summary = {"processed": 0, "failed": 0, "dead": 0}
    try:
        events = _claim_batch(conn, limit=max_events, worker_id=worker)
        for event in events:
            try:
                payload = json.loads(event["payload"])
                if not isinstance(payload, dict):
                    raise ValueError("event payload must be an object")
                handler(event, payload)
            except Exception as exc:
                status = _fail(
                    conn,
                    event["id"],
                    str(exc),
                    attempts=int(event["attempts"]),
                    max_attempts=max_attempts,
                )
                summary["failed"] += 1
                if status == "dead":
                    summary["dead"] += 1
            else:
                _complete(conn, event["id"])
                summary["processed"] += 1
        return summary
    finally:
        conn.close()


def status(db_path: Path | None = None) -> dict:
    conn = _connect(db_path)
    try:
        counts = {
            row["status"]: row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM events GROUP BY status"
            )
        }
        oldest = conn.execute(
            "SELECT MIN(created_at) FROM events WHERE status='pending'"
        ).fetchone()[0]
        now = int(time.time())
        return {
            "pending": counts.get("pending", 0),
            "processing": counts.get("processing", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("dead", 0),
            "dead": counts.get("dead", 0),
            "oldest_pending_age": (now - int(oldest)) if oldest else 0,
            "max_attempts": MAX_ATTEMPTS,
        }
    finally:
        conn.close()


def purge(
    *,
    db_path: Path | None = None,
    statuses: tuple[str, ...] = ("done", "dead"),
    older_than_seconds: int = RETENTION_SECONDS,
) -> int:
    conn = _connect(db_path)
    try:
        cutoff = int(time.time()) - older_than_seconds
        placeholders = ",".join("?" * len(statuses))
        cur = conn.execute(
            f"DELETE FROM events WHERE status IN ({placeholders}) AND updated_at < ?",
            (*statuses, cutoff),
        )
        return int(cur.rowcount or 0)
    finally:
        conn.close()
