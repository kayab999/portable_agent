"""agent_trace.py — redacted JSONL tracing for agent runs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.resolve()
TRACE_DIR = ROOT / "data" / "traces"

_REDACT_KEYS = {"token", "password", "secret", "authorization", "api_key"}
_MAX_STRING = 2000


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in _REDACT_KEYS:
                result[key] = "[redacted]"
            else:
                result[key] = _redact(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            return value[:_MAX_STRING] + "...[truncated]"
        return value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


def trace_path(run_id: str) -> Path:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")
    return TRACE_DIR / f"{safe}.jsonl"


def emit_event(
    run_id: str,
    event_type: str,
    *,
    name: str = "",
    status: str = "ok",
    duration_ms: int | None = None,
    payload: dict[str, Any] | None = None,
    parent_event_id: int | None = None,
) -> int:
    path = trace_path(run_id)
    event = {
        "timestamp": int(time.time()),
        "event_type": event_type,
        "name": name,
        "status": status,
        "duration_ms": duration_ms,
        "parent_event_id": parent_event_id,
        "payload": _redact(payload or {}),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return 0


def read_events(run_id: str) -> list[dict]:
    path = trace_path(run_id)
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events