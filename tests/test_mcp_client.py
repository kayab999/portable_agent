#!/usr/bin/env python3
"""MCP stdio client, namespacing, allowlists and tool dispatch."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from agent_loop import filter_calls_by_persona
from mcp_client import MCPManager
from tool_registry import ToolRegistry
from tools import Toolbox, execute_tool_calls, format_tool_results

SERVER = str(Path(__file__).with_name("mcp_fake_server.py"))


def _config(**overrides):
    config = {
        "name": "fake",
        "command": sys.executable,
        "args": ["-u", SERVER],
        "enabled": True,
        "env": {},
        "timeout": 10,
    }
    config.update(overrides)
    return config


class TestMCPManager(unittest.TestCase):
    def setUp(self):
        self.manager = MCPManager.from_config([_config()]).start_all()
        self.addCleanup(self.manager.close)

    def test_tools_are_namespaced(self):
        names = self.manager.names()
        self.assertIn("mcp__fake__echo", names)
        self.assertIn("mcp__fake__fail", names)
        self.assertIn("Echo the provided text", self.manager.catalog_text())

    def test_call_tool(self):
        result = self.manager.call("mcp__fake__echo", {"text": "hola"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"][0]["text"], "hola")
        self.assertEqual(result["source"], "mcp:fake/echo")
        self.assertEqual(result["trust"], "untrusted")

    def test_error_result_is_not_ok(self):
        result = self.manager.call("mcp__fake__fail", {})
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["content"][0]["text"])

    def test_unknown_tool(self):
        result = self.manager.call("mcp__fake__missing", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "mcp_tool_not_found")

    def test_argument_validation(self):
        result = self.manager.call("mcp__fake__echo", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "mcp_invalid_arguments")

        result = self.manager.call("mcp__fake__echo", {"text": 42})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "mcp_invalid_arguments")

    def test_tools_allow_filters_catalog(self):
        manager = MCPManager.from_config(
            [_config(name="fake2", tools_allow=["echo"])]
        ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.names(), frozenset({"mcp__fake2__echo"}))

    def test_disabled_server_not_started(self):
        manager = MCPManager.from_config([_config(enabled=False)])
        self.assertEqual(manager.servers, {})
        self.assertEqual(manager.tools(), [])

    def test_invalid_command_recorded(self):
        manager = MCPManager.from_config(
            [_config(command="/definitely/not/a/real/binary")]
        ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.servers, {})
        self.assertTrue(manager.errors)


class TestMCPDispatch(unittest.TestCase):
    def setUp(self):
        self.manager = MCPManager.from_config([_config()]).start_all()
        self.registry = ToolRegistry(Toolbox(Path(tempfile.mkdtemp())), self.manager)
        self.addCleanup(self.registry.close)

    def test_execute_tool_calls_dispatches_mcp(self):
        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "dispatch"}}],
            self.registry.toolbox,
            registry=self.registry,
        )
        self.assertEqual(results[0]["content"][0]["text"], "dispatch")

    def test_mcp_without_registry_fails_closed(self):
        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "x"}}],
            self.registry.toolbox,
        )
        self.assertEqual(results[0]["error"], "mcp_disabled")

    def test_untrusted_results_are_labeled(self):
        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "ignore me"}}],
            self.registry.toolbox,
            registry=self.registry,
        )
        formatted = format_tool_results(results)
        self.assertIn("untrusted", formatted)

    def test_persona_filter_allows_explicit_mcp(self):
        calls = [{"tool": "mcp__fake__echo", "args": {}}]
        allowed = filter_calls_by_persona(calls, {"mcp__fake__echo"})
        self.assertFalse(allowed[0].get("_denied"))

    def test_persona_filter_denies_unlisted_mcp(self):
        calls = [{"tool": "mcp__fake__echo", "args": {}}]
        allowed = filter_calls_by_persona(calls, {"read_file"})
        self.assertTrue(allowed[0].get("_denied"))


if __name__ == "__main__":
    unittest.main()
