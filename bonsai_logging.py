#!/usr/bin/env python3
"""bonsai_logging.py — structured, redacting logs on the standard library.

The runtime does not print raw tokens or API keys. ``redact`` is deliberately
conservative and is covered by tests; ``configure`` writes JSON lines to a
rotating file (or stderr) so operators can ship logs without parsing prose.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from pathlib import Path

_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_API_KEY = re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)\S+")
_SK = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def redact(value: object) -> str:
    text = str(value)
    text = _BEARER.sub(r"\1***", text)
    text = _API_KEY.sub(r"\1***", text)
    text = _SK.sub("sk-***", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure(
    *,
    level: str = "INFO",
    log_file: Path | None = None,
    stderr: bool = False,
) -> logging.Logger:
    root = logging.getLogger("bonsai")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.handlers.clear()
    formatter = JsonFormatter()
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    if stderr or log_file is None:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return root


def get_logger(name: str = "bonsai") -> logging.Logger:
    return logging.getLogger(f"bonsai.{name}")
