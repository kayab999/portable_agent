#!/usr/bin/env python3
"""mcp_client.py — Minimal, dependency-free MCP stdio client.

Scope for rc.5:
  - JSON-RPC 2.0 over newline-delimited stdio
  - initialize / tools/list / tools/call (tools only)
  - per-server allowlist and namespacing (``mcp__server__tool``)
  - strict timeouts, output caps, sanitized environment, no shell

Remote HTTP transports and OAuth are intentionally out of scope. Tool
descriptions and results are untrusted metadata: this module never treats
them as instructions and never lets them change permissions.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL_VERSION = "2026-07-28"
DEFAULT_TIMEOUT = 20.0
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_RESULT_CHARS = 64 * 1024
_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

# Only these environment variables are forwarded to stdio servers. Paths that
# could leak Bonsai's vault or configuration are deliberately excluded.
_ENV_ALLOW = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
)


class MCPError(RuntimeError):
    """Base error for MCP transport/protocol failures."""


class MCPTimeout(MCPError):
    """Raised when a server does not answer before the deadline."""


@dataclass(frozen=True)
class MCPTool:
    server: str
    name: str
    namespaced: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def catalog_line(self) -> str:
        description = " ".join((self.description or "").split())
        if len(description) > 240:
            description = description[:237] + "..."
        return f"- {self.namespaced}: {description}"


class MCPServer:
    """One stdio MCP server process."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        tools_allow: list[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        if not name or not re.match(r"^[a-z0-9][a-z0-9_-]{0,40}$", name):
            raise MCPError(f"invalid MCP server name: {name!r}")
        if not command:
            raise MCPError("MCP server command is required")
        self.name = name
        self.command = command
        self.args = [str(arg) for arg in (args or [])]
        self.extra_env = {str(k): str(v) for k, v in (env or {}).items()}
        self.cwd = str(cwd) if cwd else None
        self.tools_allow = tuple(tools_allow or ())
        self.timeout = float(timeout)
        self.proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._closed = False
        self.tools: list[MCPTool] = []

    # ── lifecycle ────────────────────────────────────────────────────────

    def _sanitized_env(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in _ENV_ALLOW if key in os.environ}
        env.update(self.extra_env)
        return env

    def start(self) -> None:
        if self.proc is not None:
            return
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=self.cwd,
                env=self._sanitized_env(),
                shell=False,
                bufsize=0,
            )
        except OSError as exc:
            raise MCPError(f"cannot start MCP server {self.name!r}: {exc}") from exc
        self._reader = threading.Thread(
            target=self._read_loop, name=f"mcp-{self.name}", daemon=True
        )
        self._reader.start()
        self._initialize()

    def _read_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        stdout = proc.stdout
        while True:
            try:
                line = stdout.readline(MAX_MESSAGE_BYTES + 1)
            except (OSError, ValueError):
                break
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES:
                continue
            try:
                message = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(message, dict):
                self._queue.put(message)
        self._queue.put({"__eof__": True})

    def _send(self, message: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise MCPError("MCP server is not running")
        data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(data) > MAX_MESSAGE_BYTES:
            raise MCPError("MCP request exceeds size cap")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except OSError as exc:
            raise MCPError(f"MCP write failed: {exc}") from exc

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPTimeout(f"MCP {self.name}:{method} timed out")
                try:
                    message = self._queue.get(timeout=remaining)
                except queue.Empty as exc:
                    raise MCPTimeout(f"MCP {self.name}:{method} timed out") from exc
                if message.get("__eof__"):
                    raise MCPError(f"MCP server {self.name!r} closed the stream")
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message["error"]
                    raise MCPError(
                        f"MCP {self.name}:{method} error: "
                        f"{error.get('message') or error}"
                    )
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._send(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}
        )

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "bonsai-portable", "version": "rc.5"},
            },
        )
        self._notify("notifications/initialized")

    def list_tools(self) -> list[MCPTool]:
        tools: list[MCPTool] = []
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            for raw in result.get("tools") or []:
                if not isinstance(raw, dict):
                    continue
                original = str(raw.get("name") or "").strip()
                if not original:
                    continue
                if self.tools_allow and original not in self.tools_allow:
                    continue
                namespaced = (
                    f"mcp__{_NAME_RE.sub('_', self.name)}__"
                    f"{_NAME_RE.sub('_', original)}"
                )
                schema = raw.get("inputSchema")
                tools.append(
                    MCPTool(
                        server=self.name,
                        name=original,
                        namespaced=namespaced,
                        description=str(raw.get("description") or ""),
                        input_schema=schema if isinstance(schema, dict) else {},
                    )
                )
            cursor = result.get("nextCursor")
            if not cursor or cursor in seen_cursors or not isinstance(cursor, str):
                break
            seen_cursors.add(cursor)
        self.tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        return _compact_result(result)

    def close(self) -> None:
        self._closed = True
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        finally:
            for stream in (self.proc.stdin, self.proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            self.proc = None


def _compact_result(result: dict) -> dict:
    """Normalize MCP tool output and enforce a hard size cap."""
    content = result.get("content")
    is_error = bool(result.get("isError"))
    if isinstance(content, list):
        normalized = []
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                normalized.append({"type": "text", "text": str(item.get("text", ""))})
            elif kind in ("image", "audio"):
                normalized.append({"type": kind, "data": "(binary omitted)"})
            elif kind == "resource":
                resource = item.get("resource") or {}
                normalized.append(
                    {
                        "type": "resource",
                        "uri": str(resource.get("uri", "")),
                        "text": str(resource.get("text", ""))[:4096],
                    }
                )
        payload = {"ok": not is_error, "content": normalized}
    else:
        payload = {"ok": not is_error, "content": str(content or "")}
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) > MAX_RESULT_CHARS:
        payload = {
            "ok": not is_error,
            "content": serialized[:MAX_RESULT_CHARS],
            "truncated": True,
        }
    return payload


def _validate_arguments(schema: dict, arguments: dict) -> str | None:
    """Minimal JSON Schema check (required + primitive types)."""
    if not isinstance(schema, dict):
        return None
    required = schema.get("required") or []
    if isinstance(required, list):
        missing = [key for key in required if key not in arguments]
        if missing:
            return f"missing required argument(s): {', '.join(map(str, missing))}"
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    checks = {
        "string": lambda value: isinstance(value, str),
        "number": lambda value: isinstance(value, (int, float))
        and not isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "array": lambda value: isinstance(value, list),
        "object": lambda value: isinstance(value, dict),
        "null": lambda value: value is None,
    }
    for key, value in arguments.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        check = checks.get(expected) if isinstance(expected, str) else None
        if check is not None and not check(value):
            return f"argument '{key}' must be {expected}"
    return None


class MCPManager:
    """Collection of configured stdio servers."""

    def __init__(self, servers: list[MCPServer] | None = None):
        self.servers: dict[str, MCPServer] = {
            server.name: server for server in (servers or [])
        }
        self.errors: list[dict] = []

    @classmethod
    def from_config(cls, configs: list[dict] | None) -> "MCPManager":
        servers: list[MCPServer] = []
        errors: list[dict] = []
        for raw in configs or []:
            if not isinstance(raw, dict) or not raw.get("enabled", False):
                continue
            try:
                servers.append(
                    MCPServer(
                        name=str(raw.get("name", "")),
                        command=str(raw.get("command", "")),
                        args=raw.get("args") or [],
                        env=raw.get("env") or {},
                        cwd=raw.get("cwd"),
                        tools_allow=raw.get("tools_allow") or [],
                        timeout=float(raw.get("timeout", DEFAULT_TIMEOUT)),
                    )
                )
            except (MCPError, TypeError, ValueError) as exc:
                errors.append({"name": raw.get("name"), "error": str(exc)})
        manager = cls(servers)
        manager.errors.extend(errors)
        return manager

    def start_all(self) -> "MCPManager":
        for server in list(self.servers.values()):
            try:
                server.start()
                server.list_tools()
            except (MCPError, OSError) as exc:
                self.errors.append({"name": server.name, "error": str(exc)})
                server.close()
                self.servers.pop(server.name, None)
        return self

    def tools(self) -> list[MCPTool]:
        return [tool for server in self.servers.values() for tool in server.tools]

    def names(self) -> frozenset[str]:
        return frozenset(tool.namespaced for tool in self.tools())

    def catalog_text(self, max_chars: int = 6000) -> str:
        lines: list[str] = []
        used = 0
        for tool in sorted(self.tools(), key=lambda item: item.namespaced):
            line = tool.catalog_line()
            if used + len(line) + 1 > max_chars:
                lines.append("- ... additional MCP tools omitted ...")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def resolve(self, namespaced: str) -> tuple[MCPServer, MCPTool] | None:
        for server in self.servers.values():
            for tool in server.tools:
                if tool.namespaced == namespaced:
                    return server, tool
        return None

    def call(self, namespaced: str, arguments: dict) -> dict:
        found = self.resolve(namespaced)
        if found is None:
            return {
                "ok": False,
                "error": "mcp_tool_not_found",
                "message": f"MCP tool '{namespaced}' is not available.",
            }
        server, tool = found
        if not isinstance(arguments, dict):
            arguments = {}
        validation_error = _validate_arguments(tool.input_schema, arguments)
        if validation_error:
            return {
                "ok": False,
                "error": "mcp_invalid_arguments",
                "message": validation_error,
                "tool": namespaced,
            }
        try:
            result = server.call_tool(tool.name, arguments)
        except (MCPError, OSError) as exc:
            return {
                "ok": False,
                "error": "mcp_call_failed",
                "message": str(exc),
                "tool": namespaced,
            }
        result["tool"] = namespaced
        result["source"] = f"mcp:{server.name}/{tool.name}"
        result["trust"] = "untrusted"
        return result

    def close(self) -> None:
        for server in self.servers.values():
            server.close()
        self.servers.clear()
