#!/usr/bin/env python3
"""Audit bus consumer: drain durable experience events into RAG chunks."""

from __future__ import annotations

import json
from pathlib import Path

import experience_store
from local_memory import get_memory


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


def _experience_text(data: dict) -> str:
    fields = (
        ("Query", data.get("query", "")),
        ("Plan", data.get("plan", "")),
        ("Result", data.get("result", "")),
        ("Final", data.get("final", "")),
        ("Reflection", data.get("reflection", "")),
        ("Success", data.get("success")),
    )
    lines = [f"{label}: {value}" for label, value in fields if value not in (None, "")]
    lines.append(
        "RAW_JSON: " + json.dumps(data, ensure_ascii=False, sort_keys=True)[:4000]
    )
    return "\n".join(lines)


def _index_event(event: dict, payload: dict, db_path: Path | None) -> None:
    memory = get_memory(db_path)
    memory.index_docs(
        [
            {
                "content": _experience_text(payload),
                "source": f"experience/{event['id']}",
                "source_type": "experience",
                "quality_score": _score_from_experience(payload),
            }
        ]
    )


def audit_worker(
    *,
    db_path: Path | None = None,
    max_events: int = 100,
    worker_id: str | None = None,
) -> int:
    """Process up to ``max_events`` claims. Returns the number completed."""
    summary = experience_store.drain(
        lambda event, payload: _index_event(event, payload, db_path),
        db_path=db_path,
        max_events=max_events,
        worker_id=worker_id,
    )
    return int(summary["processed"])


if __name__ == "__main__":
    print("Iniciando AuditWorker...")
    n = audit_worker()
    print(f"Eventos procesados: {n}")
