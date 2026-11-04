#!/usr/bin/env python3
"""Shared composition root and model transport contracts."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from model_client import HttpModelClient, ScriptedModelClient
from runtime import build_runtime


class TestRuntimeComposition(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_agent(self, name: str = "reviewer") -> None:
        folder = self.root / "agents"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: reviewer\n"
            "tools: [read_file, list_dir]\n"
            "---\n\nYou review code.\n",
            encoding="utf-8",
        )

    def test_defaults_keep_optional_surfaces_off(self):
        runtime = build_runtime({}, self.root, load_subagents=True)
        try:
            self.assertIsNone(runtime.registry)
            self.assertIsNone(runtime.subagent_index)
            self.assertEqual(runtime.plugins, [])
            self.assertEqual(runtime.mcp_configs, [])
            self.assertIsNotNone(runtime.toolbox)
            self.assertIsNotNone(runtime.hooks)
            self.assertFalse(runtime.hooks.enabled)
            self.assertIsNotNone(runtime.permissions)
            self.assertEqual(runtime.permissions.default, "allow")
        finally:
            runtime.close()

    def test_subagents_require_explicit_opt_in(self):
        self._write_agent()
        disabled = build_runtime({}, self.root, load_subagents=True)
        self.assertIsNone(disabled.subagent_index)
        disabled.close()

        enabled = build_runtime(
            {"subagents_enabled": True}, self.root, load_subagents=True
        )
        try:
            self.assertIsNotNone(enabled.subagent_index)
            self.assertIn("reviewer", enabled.subagent_index.agents)
        finally:
            enabled.close()

    def test_subagents_not_loaded_for_acp_transport(self):
        self._write_agent()
        runtime = build_runtime(
            {"subagents_enabled": True}, self.root, load_subagents=False
        )
        try:
            self.assertIsNone(runtime.subagent_index)
        finally:
            runtime.close()

    def test_invalid_hooks_fail_closed(self):
        warnings = []
        runtime = build_runtime(
            {"hooks": {"pre_tool": [{"args": []}]}},
            self.root,
            on_warning=warnings.append,
        )
        try:
            self.assertIsNone(runtime.hooks)
            self.assertIsNone(runtime.permissions)
            self.assertTrue(warnings)
        finally:
            runtime.close()

    def test_close_without_registry_is_noop(self):
        runtime = build_runtime({}, self.root)
        runtime.close()
        runtime.close()

    def test_plugins_are_declarative_and_mcp_is_opt_in(self):
        plugin = SimpleNamespace(
            name="demo",
            format="claude",
            skills=["skill"],
            mcp_servers=[],
            agents=[self.root / "plugins" / "demo" / "agents"],
        )
        with (
            patch(
                "plugin_adapters.load_plugins",
                return_value=([plugin], [{"path": "bad", "error": "broken"}]),
            ) as loader,
            patch("plugin_adapters.merge_mcp_servers", return_value=[]),
            patch("skills.configure_skill_roots") as configure,
        ):
            runtime = build_runtime(
                {"plugins_enabled": True, "plugin_paths": ["plugins/demo"]},
                self.root,
                load_subagents=True,
            )
        try:
            self.assertEqual(runtime.plugins, [plugin])
            self.assertEqual(runtime.plugin_agent_roots, plugin.agents)
            loader.assert_called_once()
            configure.assert_called_once()
            # Plugin MCP servers are not started without mcp_enabled.
            self.assertIsNone(runtime.registry)
        finally:
            runtime.close()

    def test_plugin_failure_disables_plugins_not_startup(self):
        warnings = []
        with patch("plugin_adapters.load_plugins", side_effect=RuntimeError("boom")):
            runtime = build_runtime(
                {"plugins_enabled": True}, self.root, on_warning=warnings.append
            )
        try:
            self.assertEqual(runtime.plugins, [])
            self.assertTrue(any("Plugins" in w for w in warnings))
        finally:
            runtime.close()

    def test_mcp_registry_is_built_and_closed(self):
        manager = SimpleNamespace(errors=[], servers={})
        manager.start_all = lambda: manager
        registry = SimpleNamespace(closed=False)
        registry.close = lambda: setattr(registry, "closed", True)
        with (
            patch("mcp_client.MCPManager.from_config", return_value=manager) as factory,
            patch("tool_registry.ToolRegistry", return_value=registry),
        ):
            runtime = build_runtime(
                {"mcp_enabled": True, "mcp_servers": [{"name": "x", "command": "cmd"}]},
                self.root,
            )
            self.assertIs(runtime.registry, registry)
            factory.assert_called_once()
            runtime.close()
        self.assertTrue(registry.closed)

    def test_mcp_failure_fails_closed(self):
        warnings = []
        with patch("mcp_client.MCPManager.from_config", side_effect=RuntimeError("no")):
            runtime = build_runtime(
                {"mcp_enabled": True}, self.root, on_warning=warnings.append
            )
        try:
            self.assertIsNone(runtime.registry)
            self.assertTrue(any("MCP" in w for w in warnings))
        finally:
            runtime.close()


class TestScriptedModelClient(unittest.TestCase):
    def test_responses_and_call_log(self):
        client = ScriptedModelClient(["one", "two"])
        first = client.chat_completion("bonsai", [{"role": "user", "content": "a"}])
        second = client.chat_completion("bonsai", [])
        self.assertEqual(first["content"], "one")
        self.assertEqual(second["content"], "two")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0][0], "bonsai")


class _LlmStub(BaseHTTPRequestHandler):
    payload = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).payload = json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps(
            {"choices": [{"message": {"content": "stub-answer"}}]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class TestHttpModelClient(unittest.TestCase):
    def setUp(self):
        _LlmStub.payload = None
        self.server = HTTPServer(("127.0.0.1", 0), _LlmStub)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_chat_completion_roundtrip(self):
        client = HttpModelClient(self.base + "/", model="fallback", timeout=5)
        self.assertFalse(client.llm_base.endswith("/"))
        result = client.chat_completion(
            "", [{"role": "user", "content": "hola"}]
        )
        self.assertEqual(result["content"], "stub-answer")
        self.assertEqual(result["raw"]["choices"][0]["message"]["content"], "stub-answer")
        self.assertEqual(_LlmStub.payload["model"], "fallback")
        self.assertEqual(_LlmStub.payload["messages"][0]["content"], "hola")
        self.assertFalse(_LlmStub.payload["stream"])

    def test_explicit_model_overrides_default(self):
        client = HttpModelClient(self.base, model="fallback", timeout=5)
        client.chat_completion("chosen", [])
        self.assertEqual(_LlmStub.payload["model"], "chosen")


if __name__ == "__main__":
    unittest.main()
