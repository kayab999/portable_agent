#!/usr/bin/env python3
"""ACP v1 stdio server regressions."""
from __future__ import annotations

import io
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from acp_server import (
    AcpCancelled,
    AcpError,
    AcpServer,
    _prompt_text,
)
from hooks import HookManager


def _serve(server: AcpServer, lines: list[str]) -> list[dict]:
    reader = io.StringIO("\n".join(lines) + "\n")
    writer = io.StringIO()
    server.serve(reader, writer)
    return [json.loads(line) for line in writer.getvalue().splitlines() if line]


def _request(server: AcpServer, method: str, params: dict | None = None, id=1):
    responses = server.handle_message(
        {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
    )
    return responses[0]


def _new_session(server: AcpServer, cwd: str | None = None) -> str:
    cwd = cwd or tempfile.gettempdir()
    response = _request(server, "session/new", {"cwd": cwd, "mcpServers": []})
    return response["result"]["sessionId"]


def _echo_runner(session, text, emit):
    emit(
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": f"echo: {text}"},
        }
    )
    return {
        "answer": f"echo: {text}",
        "emitted": True,
        "tool_results": [{"tool": "read_file", "ok": True}],
    }


class TestAcpProtocol(unittest.TestCase):
    def test_initialize_advertises_narrow_capabilities(self):
        server = AcpServer(_echo_runner, agent_version="1.0.0-rc.5")
        response = _request(server, "initialize", {"protocolVersion": 1})
        result = response["result"]
        self.assertEqual(result["protocolVersion"], 1)
        self.assertFalse(result["agentCapabilities"]["loadSession"])
        caps = result["agentCapabilities"]["promptCapabilities"]
        self.assertFalse(caps["image"])
        self.assertFalse(caps["audio"])
        self.assertFalse(caps["embeddedContext"])
        self.assertEqual(result["authMethods"], [])
        self.assertEqual(result["agentInfo"]["version"], "1.0.0-rc.5")

    def test_initialize_requires_protocol_version(self):
        server = AcpServer(_echo_runner)
        response = _request(server, "initialize", {})
        self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_method(self):
        server = AcpServer(_echo_runner)
        response = _request(server, "session/load", {})
        self.assertEqual(response["error"]["code"], -32601)

    def test_notification_gets_no_response(self):
        server = AcpServer(_echo_runner)
        responses = server.handle_message(
            {"jsonrpc": "2.0", "method": "session/cancel", "params": {}}
        )
        self.assertEqual(responses, [])

    def test_malformed_json(self):
        server = AcpServer(_echo_runner)
        responses = _serve(server, ["{not json}"])
        self.assertEqual(responses[0]["error"]["code"], -32700)

    def test_non_object_request(self):
        server = AcpServer(_echo_runner)
        responses = _serve(server, ["[]"])
        self.assertEqual(responses[0]["error"]["code"], -32600)


class TestAcpSessions(unittest.TestCase):
    def test_session_new_requires_absolute_cwd(self):
        server = AcpServer(_echo_runner)
        response = _request(server, "session/new", {"cwd": "relative/path"})
        self.assertEqual(response["error"]["code"], -32602)

    def test_session_new_rejects_client_mcp_servers(self):
        server = AcpServer(_echo_runner)
        response = _request(
            server,
            "session/new",
            {
                "cwd": tempfile.gettempdir(),
                "mcpServers": [{"name": "evil", "command": "sh"}],
            },
        )
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("MCP", response["error"]["message"])

    def test_prompt_unknown_session(self):
        server = AcpServer(_echo_runner)
        response = _request(
            server,
            "session/prompt",
            {"sessionId": "nope", "prompt": [{"type": "text", "text": "hi"}]},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_prompt_rejects_non_text_content(self):
        server = AcpServer(_echo_runner)
        session_id = _new_session(server)
        response = _request(
            server,
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "image", "data": "..."}],
            },
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_prompt_rejects_empty_text(self):
        server = AcpServer(_echo_runner)
        session_id = _new_session(server)
        response = _request(
            server,
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "  "}]},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_session_limit_evicts_idle_session(self):
        server = AcpServer(_echo_runner, max_sessions=1)
        first = _new_session(server)
        second = _new_session(server)
        self.assertNotEqual(first, second)
        response = _request(
            server,
            "session/prompt",
            {"sessionId": first, "prompt": [{"type": "text", "text": "hi"}]},
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_prompt_emits_updates_before_response(self):
        server = AcpServer(_echo_runner)
        session_id = _new_session(server)
        messages = _serve(
            server,
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 7,
                        "method": "session/prompt",
                        "params": {
                            "sessionId": session_id,
                            "prompt": [{"type": "text", "text": "hello"}],
                        },
                    }
                )
            ],
        )
        updates = [m for m in messages if m.get("method") == "session/update"]
        self.assertEqual(len(updates), 2)
        kinds = [m["params"]["update"]["sessionUpdate"] for m in updates]
        self.assertEqual(kinds, ["agent_message_chunk", "tool_call"])
        self.assertEqual(updates[0]["params"]["update"]["content"]["text"], "echo: hello")
        self.assertEqual(updates[1]["params"]["update"]["status"], "completed")
        response = next(m for m in messages if m.get("id") == 7)
        self.assertEqual(response["result"]["stopReason"], "end_turn")

    def test_runner_answer_is_emitted_once(self):
        def runner(session, text, emit):
            return {"answer": "only once"}

        server = AcpServer(runner)
        session_id = _new_session(server)
        messages = _serve(
            server,
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "session/prompt",
                        "params": {
                            "sessionId": session_id,
                            "prompt": [{"type": "text", "text": "x"}],
                        },
                    }
                )
            ],
        )
        chunks = [
            m
            for m in messages
            if m.get("method") == "session/update"
            and m["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
        ]
        self.assertEqual(len(chunks), 1)

    def test_cancel_returns_cancelled(self):
        started = threading.Event()

        def runner(session, text, emit):
            started.set()
            while not session.cancel_event.wait(0.01):
                pass
            raise AcpCancelled()

        server = AcpServer(runner)
        session_id = _new_session(server)
        messages = _serve(
            server,
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session/prompt",
                        "params": {
                            "sessionId": session_id,
                            "prompt": [{"type": "text", "text": "slow"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": session_id},
                    }
                ),
            ],
        )
        self.assertTrue(started.is_set())
        response = next(m for m in messages if m.get("id") == 3)
        self.assertEqual(response["result"]["stopReason"], "cancelled")

    def test_prompt_blocked_by_hook(self):
        manager = HookManager.from_config(
            {
                "enabled": True,
                "user_prompt": [
                    {
                        "command": sys.executable,
                        "args": ["-c", "import sys; sys.exit(2)"],
                    }
                ],
            }
        )
        called = threading.Event()

        def runner(session, text, emit):
            called.set()
            return {"answer": "should not run"}

        server = AcpServer(runner, hooks=manager)
        session_id = _new_session(server)
        response = _request(
            server,
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]},
        )
        self.assertEqual(response["result"]["stopReason"], "refusal")
        self.assertFalse(called.is_set())

    def test_session_end_hook_runs_on_eof(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "ended.txt"
            manager = HookManager.from_config(
                {
                    "enabled": True,
                    "session_end": [
                        {
                            "command": sys.executable,
                            "args": [
                                "-c",
                                "import sys; open(sys.argv[1], 'w').write('ok')",
                                str(marker),
                            ],
                        }
                    ],
                }
            )
            server = AcpServer(_echo_runner, hooks=manager)
            _new_session(server)
            _serve(server, [])
            self.assertTrue(marker.exists())


ROOT = Path(__file__).parent.parent
LAUNCHER = ROOT / "launcher.py"
BINARY = ROOT / "bin" / "linux" / "x86_64" / "llama-server"
MODEL = ROOT / "tests" / "test_data" / "stories260K.gguf"


def _free_port_pair() -> tuple[int, int]:
    for _ in range(10):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port + 100))
            probe.close()
            return port, port + 100
        except OSError:
            probe.close()
    raise RuntimeError("no free port pair")


def _start_reader(proc) -> queue.Queue:
    messages: queue.Queue = queue.Queue()

    def read_lines() -> None:
        for line in proc.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                # Any human log on stdout is a protocol violation.
                messages.put({"__protocol_error__": line})
        messages.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    return messages


def _read_until(messages: queue.Queue, target_id, timeout: float = 90.0):
    seen: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = messages.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            break
        if message is None:
            break
        if "__protocol_error__" in message:
            raise AssertionError(
                f"non-JSON stdout frame: {message['__protocol_error__']!r}"
            )
        seen.append(message)
        if message.get("id") == target_id:
            return message, seen
    return None, seen


@unittest.skipUnless(os.name == "posix", "solo POSIX")
@unittest.skipUnless(BINARY.exists(), "binario llama-server ausente")
@unittest.skipUnless(MODEL.exists(), "modelo de prueba ausente")
class TestAcpLauncherE2E(unittest.TestCase):
    def test_launcher_acp_keeps_stdout_clean(self):
        gateway_port, _ = _free_port_pair()
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            cfg.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "enabled": True,
                            "pre_llm": [
                                {
                                    "command": sys.executable,
                                    "args": ["-c", "import sys; sys.exit(2)"],
                                    "matcher": "interactive_react",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = dict(
                os.environ,
                BONSAI_DEBUG="1",
                BONSAI_CONFIG=str(cfg),
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(LAUNCHER),
                    "-m",
                    str(MODEL),
                    "--port",
                    str(gateway_port),
                    "--ngl",
                    "0",
                    "--acp",
                ],
                env=env,
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                assert proc.stdin is not None and proc.stdout is not None
                messages = _start_reader(proc)
                proc.stdin.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"protocolVersion": 1},
                        }
                    )
                    + "\n"
                )
                proc.stdin.flush()
                response, seen = _read_until(messages, 1)
                self.assertIsNotNone(response, f"no initialize response: {seen}")
                self.assertEqual(response["result"]["protocolVersion"], 1)

                proc.stdin.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "session/new",
                            "params": {"cwd": td, "mcpServers": []},
                        }
                    )
                    + "\n"
                )
                proc.stdin.flush()
                session, _ = _read_until(messages, 2)
                self.assertIsNotNone(session)
                session_id = session["result"]["sessionId"]

                proc.stdin.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "session/prompt",
                            "params": {
                                "sessionId": session_id,
                                "prompt": [{"type": "text", "text": "Say hi."}],
                            },
                        }
                    )
                    + "\n"
                )
                proc.stdin.flush()
                prompt, seen = _read_until(messages, 3)
                self.assertIsNotNone(prompt, f"no prompt response: {seen}")
                self.assertIn(
                    prompt["result"]["stopReason"],
                    ("end_turn", "refusal", "cancelled", "max_tokens"),
                )
                chunks = [
                    message
                    for message in seen
                    if message.get("method") == "session/update"
                    and message["params"]["update"]["sessionUpdate"]
                    == "agent_message_chunk"
                ]
                self.assertTrue(chunks, "expected at least one agent message chunk")
                self.assertIn(
                    "Bloqueado por hook",
                    chunks[0]["params"]["update"]["content"]["text"],
                )
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                try:
                    proc.stdout.close()
                except Exception:
                    pass


class TestAcpPromptText(unittest.TestCase):
    def test_string_prompt_tolerated(self):
        self.assertEqual(_prompt_text("hello"), "hello")

    def test_empty_list_rejected(self):
        with self.assertRaises(AcpError):
            _prompt_text([])

    def test_non_object_block_rejected(self):
        with self.assertRaises(AcpError):
            _prompt_text(["hi"])


class TestAcpAsyncFallback(unittest.TestCase):
    def test_unhandled_exception_still_returns_internal_error(self):
        server = AcpServer()
        writer = io.StringIO()
        server._writer = writer

        def boom(_message):
            raise RuntimeError("SECRET_PATH_LEAK")

        server.handle_message = boom
        server._run_async(
            {"jsonrpc": "2.0", "id": 7, "method": "session/prompt", "params": {}}
        )
        payload = json.loads(writer.getvalue())
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["error"]["code"], -32603)
        self.assertNotIn("SECRET_PATH_LEAK", writer.getvalue())


if __name__ == "__main__":
    unittest.main()
