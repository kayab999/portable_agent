#!/usr/bin/env python3
"""acp_server.py — Agent Client Protocol (ACP) v1 over stdio.

This is a local editor↔agent bridge: JSON-RPC 2.0, one object per line, on
stdin/stdout. It is intentionally narrow:

  initialize      → capabilities (no fs, no terminal, no embedded context)
  authenticate    → no-op (no auth methods are advertised)
  session/new     → create an in-memory session (no MCP servers accepted)
  session/prompt  → run one turn, stream session/update notifications
  session/cancel  → cancel the turn at the next model call

Security posture:
  - Disabled unless the caller starts it explicitly (`launcher.py --acp`).
  - stdio only; no TCP listener, no remote transport.
  - Client-provided MCP servers and filesystem/terminal bridging are refused.
  - Tool execution still goes through hooks, the permission policy and the
    existing workspace sandbox. ACP cannot widen any of them.
  - Diagnostics go to stderr; stdout carries protocol frames only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL_VERSION = 1
_MAX_LINE_BYTES = 2 * 1024 * 1024
_MAX_PROMPT_CHARS = 64_000
_MAX_SESSIONS = 8

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_SENTINEL = object()


class AcpError(Exception):
    """JSON-RPC error raised by a method handler."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class AcpCancelled(Exception):
    """Raised by the default runner when the client cancels a turn."""


@dataclass
class AcpSession:
    id: str
    cwd: str
    created: float = field(default_factory=time.time)
    messages: list[dict] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    busy: threading.Lock = field(default_factory=threading.Lock)


class _CancellableClient:
    """Model client wrapper that aborts between calls when cancel is observed."""

    def __init__(self, client, cancel_event: threading.Event):
        self._client = client
        self._cancel = cancel_event

    def chat_completion(self, model: str, messages: list[dict]) -> dict:
        if self._cancel.is_set():
            raise AcpCancelled()
        return self._client.chat_completion(model, messages)


def _error(message_id, code: int, message: str, data=None) -> dict:
    body = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": body}


def _prompt_text(prompt) -> str:
    if isinstance(prompt, str):
        return prompt.strip()
    if not isinstance(prompt, list) or not prompt:
        raise AcpError(INVALID_PARAMS, "prompt must be a non-empty array")
    parts: list[str] = []
    for block in prompt:
        if not isinstance(block, dict):
            raise AcpError(INVALID_PARAMS, "prompt blocks must be objects")
        if block.get("type") != "text":
            raise AcpError(
                INVALID_PARAMS,
                f"unsupported prompt content type: {block.get('type')!r}",
            )
        text = block.get("text")
        if not isinstance(text, str):
            raise AcpError(INVALID_PARAMS, "text prompt blocks need a string")
        parts.append(text)
    return "\n".join(parts).strip()


def _read_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


class AcpServer:
    """Protocol dispatcher with an injectable turn runner.

    ``turn_runner(session, text, emit)`` must return a dict with an optional
    ``answer`` and ``tool_results``. ``emit(update)`` sends one
    ``session/update`` notification for the current session.
    """

    def __init__(
        self,
        turn_runner=None,
        *,
        hooks=None,
        permissions=None,
        max_sessions: int = _MAX_SESSIONS,
        max_prompt_chars: int = _MAX_PROMPT_CHARS,
        agent_name: str = "Bonsai Portable",
        agent_version: str = "",
    ):
        self.turn_runner = turn_runner
        self.hooks = hooks
        self.permissions = permissions
        self.max_sessions = max(1, int(max_sessions))
        self.max_prompt_chars = max(1, int(max_prompt_chars))
        self.agent_name = agent_name
        self.agent_version = agent_version
        self._sessions: dict[str, AcpSession] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._writer = None
        self._threads: list[threading.Thread] = []

    # ── transport ────────────────────────────────────────────────────────────
    def _send(self, message: dict) -> None:
        with self._write_lock:
            writer = self._writer
            if writer is None:
                return
            writer.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
            try:
                writer.flush()
            except Exception:
                pass

    def notify(self, session: AcpSession, update: dict) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session.id, "update": update},
            }
        )

    def serve(self, reader, writer, *, threaded: bool = True) -> None:
        """Read JSON-RPC lines until EOF. Prompts run in worker threads."""
        self._writer = writer
        try:
            for raw in reader:
                line = raw.strip()
                if not line:
                    continue
                if len(line.encode("utf-8", "replace")) > _MAX_LINE_BYTES:
                    self._send(
                        _error(None, INVALID_REQUEST, "request exceeds size limit")
                    )
                    continue
                try:
                    message = json.loads(line)
                except ValueError as exc:
                    self._send(_error(None, PARSE_ERROR, f"invalid JSON: {exc}"))
                    continue
                if not isinstance(message, dict):
                    self._send(_error(None, INVALID_REQUEST, "request must be an object"))
                    continue
                method = message.get("method")
                if (
                    threaded
                    and method == "session/prompt"
                    and "id" in message
                ):
                    worker = threading.Thread(
                        target=self._run_async,
                        args=(message,),
                        daemon=True,
                    )
                    worker.start()
                    self._threads.append(worker)
                else:
                    for response in self.handle_message(message):
                        self._send(response)
        finally:
            for worker in self._threads:
                worker.join()
            with self._lock:
                sessions = list(self._sessions.values())
                self._sessions.clear()
                self._order.clear()
            for session in sessions:
                if self.hooks is not None:
                    self.hooks.run(
                        "session_end",
                        {"sessionId": session.id, "source": "acp"},
                        matcher="acp",
                    )
            self._writer = None

    def _run_async(self, message: dict) -> None:
        try:
            for response in self.handle_message(message):
                self._send(response)
        except Exception as exc:  # keep the reader loop alive
            print(f"acp: unhandled error: {exc}", file=sys.stderr)
            if isinstance(message, dict):
                message_id = message.get("id", _SENTINEL)
            else:
                message_id = _SENTINEL
            if message_id is not _SENTINEL:
                self._send(
                    _error(message_id, INTERNAL_ERROR, "internal error")
                )

    # ── dispatch ─────────────────────────────────────────────────────────────
    def handle_message(self, message: dict) -> list[dict]:
        if not isinstance(message, dict):
            return [_error(None, INVALID_REQUEST, "request must be an object")]
        if "method" not in message:
            # Responses from the client (to agent→client requests) are ignored.
            if "id" in message and ("result" in message or "error" in message):
                return []
            return [_error(message.get("id"), INVALID_REQUEST, "method is required")]

        method = str(message.get("method"))
        message_id = message.get("id", _SENTINEL)
        is_notification = message_id is _SENTINEL
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._fail(message_id, is_notification, INVALID_PARAMS, "params must be an object")

        try:
            result = self.dispatch(method, params)
        except AcpError as exc:
            return self._fail(
                message_id, is_notification, exc.code, exc.message, exc.data
            )
        except Exception as exc:
            print(f"acp: {method} failed: {exc}", file=sys.stderr)
            return self._fail(
                message_id, is_notification, INTERNAL_ERROR, "internal error"
            )

        if is_notification:
            return []
        return [{"jsonrpc": "2.0", "id": message_id, "result": result}]

    def _fail(self, message_id, is_notification: bool, code: int, message: str, data=None):
        if is_notification:
            return []
        return [_error(message_id, code, message, data)]

    def dispatch(self, method: str, params: dict):
        if method == "initialize":
            return self._initialize(params)
        if method == "authenticate":
            return {}
        if method == "session/new":
            return self._session_new(params)
        if method == "session/prompt":
            return self._session_prompt(params)
        if method == "session/cancel":
            self._session_cancel(params)
            return {}
        raise AcpError(METHOD_NOT_FOUND, f"method not found: {method}")

    # ── methods ──────────────────────────────────────────────────────────────
    def _initialize(self, params: dict) -> dict:
        version = params.get("protocolVersion")
        if not isinstance(version, int):
            raise AcpError(INVALID_PARAMS, "protocolVersion is required")
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
                "mcpCapabilities": {"http": False, "sse": False},
            },
            "agentInfo": {
                "name": self.agent_name,
                "version": self.agent_version or "unknown",
            },
            "authMethods": [],
        }

    def _session_new(self, params: dict) -> dict:
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip() or "\x00" in cwd:
            raise AcpError(INVALID_PARAMS, "cwd must be a non-empty path")
        if not Path(cwd).expanduser().is_absolute():
            raise AcpError(INVALID_PARAMS, "cwd must be an absolute path")
        mcp_servers = params.get("mcpServers") or []
        if not isinstance(mcp_servers, list):
            raise AcpError(INVALID_PARAMS, "mcpServers must be an array")
        if mcp_servers:
            raise AcpError(
                INVALID_PARAMS,
                "client-provided MCP servers are not supported by this build",
            )

        if self.hooks is not None:
            decision = self.hooks.run(
                "session_start",
                {"cwd": cwd, "source": "acp"},
                matcher="acp",
            )
            if decision.blocked:
                raise AcpError(INTERNAL_ERROR, "session rejected by hook")

        with self._lock:
            while len(self._sessions) >= self.max_sessions:
                victim = None
                for candidate_id in self._order:
                    candidate = self._sessions.get(candidate_id)
                    if candidate is not None and not candidate.busy.locked():
                        victim = candidate_id
                        break
                if victim is None:
                    raise AcpError(INTERNAL_ERROR, "session limit reached")
                self._sessions.pop(victim, None)
                self._order.remove(victim)
            session_id = f"acp-{uuid.uuid4().hex}"
            self._sessions[session_id] = AcpSession(id=session_id, cwd=cwd)
            self._order.append(session_id)
        return {"sessionId": session_id}

    def _session_cancel(self, params: dict) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str):
            return
        with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            session.cancel_event.set()

    def _get_session(self, params: dict) -> AcpSession:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise AcpError(INVALID_PARAMS, "sessionId is required")
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise AcpError(INVALID_PARAMS, "unknown sessionId")
        return session

    def _session_prompt(self, params: dict) -> dict:
        session = self._get_session(params)
        text = _prompt_text(params.get("prompt"))
        if not text:
            raise AcpError(INVALID_PARAMS, "prompt has no text content")
        if len(text) > self.max_prompt_chars:
            raise AcpError(
                INVALID_PARAMS,
                f"prompt exceeds {self.max_prompt_chars} characters",
            )
        if not session.busy.acquire(blocking=False):
            raise AcpError(INVALID_PARAMS, "session already has an active prompt")

        session.cancel_event.clear()

        def emit(update: dict) -> None:
            if isinstance(update, dict):
                self.notify(session, update)

        try:
            if self.hooks is not None:
                decision = self.hooks.run(
                    "user_prompt",
                    {"text": text, "source": "acp"},
                    matcher="acp",
                )
                if decision.blocked:
                    emit(
                        {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": f"Blocked by policy: {decision.reason}",
                            },
                        }
                    )
                    return {"stopReason": "refusal"}

            if self.turn_runner is None:
                raise AcpError(INTERNAL_ERROR, "no turn runner configured")

            try:
                outcome = self.turn_runner(session, text, emit) or {}
            except AcpCancelled:
                return {"stopReason": "cancelled"}

            if session.cancel_event.is_set():
                return {"stopReason": "cancelled"}

            answer = str(outcome.get("answer") or "")
            if answer and not outcome.get("emitted"):
                emit(
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": answer},
                    }
                )
            self._emit_tools(session, outcome.get("tool_results") or [])
            return {"stopReason": "end_turn"}
        finally:
            session.busy.release()

    def _emit_tools(self, session: AcpSession, results: list[dict]) -> None:
        for item in results:
            if not isinstance(item, dict):
                continue
            self.notify(
                session,
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": f"tool-{uuid.uuid4().hex}",
                    "title": str(item.get("tool") or "tool"),
                    "status": "completed" if item.get("ok", True) else "failed",
                    "rawOutput": item,
                },
            )


# ── default runtime (local model + real toolbox) ─────────────────────────────
def build_system_for(
    text: str,
    *,
    root: Path,
    config: dict,
    registry=None,
) -> str:
    from agent_loop import TOOL_PROTOCOL_HINT
    from context_builder import build_system_prompt
    from persona import build_persona_system, load_identity

    identity = load_identity() or {}
    persona_key = identity.get("persona") or "investigador"
    base = build_persona_system(persona_key)

    agents_md = ""
    if config.get("agents_md_enabled", True):
        try:
            from agents_md import load_agents_md

            agents_md = load_agents_md(root)
        except Exception:
            agents_md = ""

    skills_index = ""
    active_skills: list[tuple[str, str]] = []
    if config.get("skills_enabled", True):
        try:
            from skills import get_skill_index, skills_enabled

            if skills_enabled():
                index = get_skill_index(root)
                skills_index = index.index_text()
                if text.startswith("/"):
                    requested = text.split(None, 1)[0][1:]
                    skill = index.get(requested)
                    if skill is not None:
                        active_skills.append((skill.name, skill.body))
        except Exception:
            skills_index = ""

    mcp_catalog = ""
    if registry is not None:
        try:
            mcp_catalog = registry.mcp.catalog_text()
        except Exception:
            mcp_catalog = ""

    return build_system_prompt(
        base,
        tool_hint=TOOL_PROTOCOL_HINT,
        agents_md=agents_md,
        skills_index=skills_index,
        active_skills=active_skills,
        mcp_catalog=mcp_catalog,
    )


def make_turn_runner(
    *,
    root: Path,
    config: dict,
    model_client,
    model: str,
    toolbox=None,
    registry=None,
    hooks=None,
    permissions=None,
    max_iterations: int = 5,
):
    """Build the default runner: model + tools + hooks + permissions."""
    from agent_loop import interactive_react
    from persona import load_identity, resolve_persona_tools
    from tools import get_toolbox

    toolbox = toolbox or get_toolbox()
    identity = load_identity() or {}
    persona_key = identity.get("persona") or "investigador"

    def runner(session: AcpSession, text: str, emit) -> dict:
        system = build_system_for(
            text,
            root=root,
            config=config,
            registry=registry,
        )
        history = [m for m in session.messages if m.get("role") != "system"]
        session.messages = [{"role": "system", "content": system}] + history
        persona_tools = resolve_persona_tools(persona_key)
        if registry is not None:
            persona_tools = frozenset(
                set(persona_tools) | set(registry.mcp_names())
            )
        client = _CancellableClient(model_client, session.cancel_event)
        answer, messages, results = interactive_react(
            text,
            client,
            session.messages,
            model=model or "bonsai",
            toolbox=toolbox,
            persona_tools=persona_tools,
            registry=registry,
            hooks=hooks,
            permissions=permissions,
            max_iterations=max(1, min(int(max_iterations), 8)),
        )
        session.messages = messages
        if answer:
            emit(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": answer},
                }
            )
        return {"answer": answer, "tool_results": results, "emitted": True}

    return runner


def runtime_from_config(
    config: dict,
    root: Path,
    *,
    llm_base: str,
    model: str,
    max_sessions: int = _MAX_SESSIONS,
) -> AcpServer:
    """Assemble the shared runtime and wrap it in an ACP server."""
    from model_client import HttpModelClient
    from runtime import build_runtime

    root = Path(root).resolve()
    runtime = build_runtime(
        config,
        root,
        load_subagents=False,
        on_warning=lambda message: print(f"acp: {message}", file=sys.stderr),
    )
    client = HttpModelClient(llm_base, model=model)
    runner = make_turn_runner(
        root=root,
        config=config or {},
        model_client=client,
        model=model,
        toolbox=runtime.toolbox,
        registry=runtime.registry,
        hooks=runtime.hooks,
        permissions=runtime.permissions,
        max_iterations=min(int((config or {}).get("max_iterations", 5)), 8),
    )
    return AcpServer(
        runner,
        hooks=runtime.hooks,
        permissions=runtime.permissions,
        max_sessions=max_sessions,
        agent_version=_read_version(root),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bonsai Portable — ACP v1 agent over stdio"
    )
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument(
        "--llm-base",
        default=None,
        help="OpenAI-compatible base URL (default: BONSAI_LLM_BASE or config port+100)",
    )
    parser.add_argument("--model", default=None, help="Model name for the backend")
    parser.add_argument("--max-sessions", type=int, default=_MAX_SESSIONS)
    args = parser.parse_args(argv)

    from runtime_core import config_file, load_config

    root = Path(__file__).parent.resolve()
    cfg_path = (
        Path(args.config).expanduser()
        if args.config
        else config_file(root / "config.json")
    )
    config = load_config(cfg_path)
    llm_base = (
        args.llm_base
        or os.environ.get("BONSAI_LLM_BASE")
        or f"http://127.0.0.1:{int(config.get('port', 8080)) + int(config.get('llm_port_offset', 100))}"
    )
    model = args.model or config.get("last_model") or "bonsai"
    server = runtime_from_config(
        config,
        root,
        llm_base=llm_base,
        model=model,
        max_sessions=args.max_sessions,
    )
    server.serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
