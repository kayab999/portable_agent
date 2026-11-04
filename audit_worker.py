#!/usr/bin/env python3
"""Audit bus consumer: drain event_queue → CRS memory chunks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from local_memory import DB_PATH, get_memory


def _score_from_experience(data: dict) -> float:
    if "success" in data and isinstance(data["success"], bool):
        quality = 0.75 if data["success"] else 0.25
    else:
        ref = str(data.get("reflection", "")).lower()
        quality = 0.6
        if "fail" in ref or "error" in ref or "fracaso" in ref:
            quality -= 0.3
        if "partial" in ref:
            quality += 0.1
        if "success" in ref or "éxito" in ref or "exito" in ref:
            quality += 0.3
    return max(0.1, min(1.0, quality))


def audit_worker(
    *,
    db_path: Path | None = None,
    max_events: int = 100,
) -> int:
    """
    Process up to max_events from event_queue into CRS.
    Returns number of events processed.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    mem = get_memory(path)
    processed = 0

    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS event_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )"""
        )
        events = conn.execute(
            "SELECT id, event_type, payload FROM event_queue ORDER BY id ASC LIMIT ?",
            (max_events,),
        ).fetchall()

        for eid, event_type, payload in events:
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                conn.execute("DELETE FROM event_queue WHERE id = ?", (eid,))
                continue

            quality = _score_from_experience(data)
            source = f"experience:{event_type or 'PERSIST_EXPERIENCE'}"
            # Human-readable content for retrieval (not only raw JSON)
            content = (
                f"Query: {data.get('query', '')}\n"
                f"Plan: {data.get('plan', '')}\n"
                f"Result: {data.get('result', '')}\n"
                f"Final: {data.get('final', '')}\n"
                f"Reflection: {data.get('reflection', '')}\n"
                f"Success: {data.get('success')}\n"
                f"RAW_JSON: {json.dumps(data, ensure_ascii=False)[:4000]}"
            )
            mem.index_docs(
                [
                    {
                        "content": content,
                        "source": source,
                        "quality_score": quality,
                    }
                ]
            )
            conn.execute("DELETE FROM event_queue WHERE id = ?", (eid,))
            processed += 1

        conn.commit()
    finally:
        conn.close()

    return processed


if __name__ == "__main__":
    print("Iniciando AuditWorker...")
    n = audit_worker()
    print(f"Eventos procesados: {n}")
