#!/usr/bin/env python3
"""model_client.py — shared OpenAI-compatible model transport.

Both the HTTP gateway and the ACP stdio server use this client. Keeping it in
its own module removes the previous transport → HTTP coupling where the ACP
server imported a private class from ``gateway``.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Protocol


class ModelClient(Protocol):
    def chat_completion(self, model: str, messages: list[dict]) -> dict: ...


class HttpModelClient:
    """Minimal OpenAI-compatible client against llama-server."""

    def __init__(self, llm_base: str, model: str = "bonsai", *, timeout: int = 300):
        self.llm_base = llm_base.rstrip("/")
        self.model = model
        self.timeout = timeout

    def chat_completion(self, model: str, messages: list[dict]) -> dict:
        payload = json.dumps(
            {
                "model": model or self.model,
                "messages": messages,
                "stream": False,
                "temperature": 0.7,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.llm_base}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return {"content": content, "raw": data}


class ScriptedModelClient:
    """Deterministic client for tests and offline demos."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, list[dict]]] = []

    def chat_completion(self, model: str, messages: list[dict]) -> dict:
        self.calls.append((model, messages))
        content = self.responses.pop(0) if self.responses else "done"
        return {"content": content, "raw": {}}


# Backwards-compatible alias used by earlier gateway internals/tests.
_HttpModelClient = HttpModelClient
