#!/usr/bin/env python3
"""MCP stdio client, namespacing, allowlists and tool dispatch."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_loop import filter_calls_by_persona
from mcp_client import (
    MAX_TOOL_PAGES,
    MCPError,
    MCPManager,
    MCPServer,
)
from permissions import PermissionPolicy
from tool_registry import ToolRegistry
from tools import Toolbox, execute_tool_calls, format_tool_results

SERVER = str(Path(__file__).with_name("mcp_fake_server.py"))
MODERN_SERVER = str(Path(__file__).with_name("mcp_modern_server.py"))


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


def _modern_config(mode: str = "modern", **overrides):
    config = _config(
        name="modern",
        args=["-u", MODERN_SERVER, mode],
    )
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

    def test_legacy_server_negotiates_legacy_era(self):
        server = self.manager.servers["fake"]
        self.assertEqual(server.era, "legacy")
        self.assertEqual(server.protocol_version, "2025-11-25")

    def test_legacy_fallback_when_discover_gets_no_response(self):
        with patch("mcp_client.DISCOVER_PROBE_TIMEOUT", 0.2):
            manager = MCPManager.from_config(
                [_config(args=["-u", SERVER, "--ignore-discover"])]
            ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.errors, [])
        self.assertEqual(manager.servers["fake"].era, "legacy")
        self.assertEqual(manager.names(), frozenset({"mcp__fake__echo", "mcp__fake__fail"}))

    def test_modern_server_uses_per_request_metadata(self):
        manager = MCPManager.from_config([_modern_config()]).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.errors, [])
        server = manager.servers["modern"]
        self.assertEqual(server.era, "modern")
        self.assertEqual(server.protocol_version, "2026-07-28")
        self.assertEqual(server.server_info.get("name"), "modern-fake")
        self.assertEqual(
            manager.names(),
            frozenset({"mcp__modern__echo", "mcp__modern__hidden"}),
        )
        result = manager.call("mcp__modern__echo", {"text": "modern"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["content"][0]["text"], "modern")

    def test_modern_allowlist_filters_and_blocks_direct_calls(self):
        manager = MCPManager.from_config(
            [_modern_config(tools_allow=["echo"])]
        ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.names(), frozenset({"mcp__modern__echo"}))
        server = manager.servers["modern"]
        with self.assertRaises(MCPError):
            server.call_tool("hidden", {})

    def test_unsupported_modern_version_does_not_use_legacy_fallback(self):
        manager = MCPManager.from_config(
            [_modern_config("reject-version")]
        ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.servers, {})
        self.assertTrue(
            any("does not support" in item["error"] for item in manager.errors),
            manager.errors,
        )

    def test_unknown_result_type_is_rejected(self):
        manager = MCPManager.from_config(
            [_modern_config("unknown-result-type")]
        ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.servers, {})
        self.assertTrue(
            any("resultType" in item["error"] for item in manager.errors),
            manager.errors,
        )

    def test_input_required_result_fails_closed(self):
        manager = MCPManager.from_config(
            [_modern_config("input-required")]
        ).start_all()
        self.addCleanup(manager.close)
        result = manager.call("mcp__modern__echo", {"text": "x"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "mcp_call_failed")
        self.assertIn("client input", result["message"])

    def test_explicit_empty_allowlist_exposes_no_tools(self):
        manager = MCPManager.from_config(
            [_config(tools_allow=[])]
        ).start_all()
        self.addCleanup(manager.close)
        self.assertEqual(manager.names(), frozenset())
        server = manager.servers["fake"]
        self.assertIsNone(server.proc)
        server.start()  # direct calls must stay inert too
        self.assertIsNone(server.proc)
        result = manager.call("mcp__fake__echo", {"text": "x"})
        self.assertEqual(result["error"], "mcp_tool_not_found")

    def test_invalid_tools_allow_rejected(self):
        for value in (None, "echo", [""], [42]):
            with self.subTest(value=value):
                manager = MCPManager.from_config(
                    [_config(tools_allow=value)]
                )
                self.assertEqual(manager.servers, {})
                self.assertTrue(manager.errors)

    def test_tool_pagination_is_bounded(self):
        server = MCPServer("fake", sys.executable)
        counter = {"value": 0}

        def endless(method, params=None, **kwargs):
            counter["value"] += 1
            return {"tools": [], "nextCursor": f"cursor-{counter['value']}"}

        with patch.object(server, "_request", side_effect=endless):
            with self.assertRaises(MCPError):
                server.list_tools()
        self.assertEqual(counter["value"], MAX_TOOL_PAGES)


class TestMCPDispatch(unittest.TestCase):
    def setUp(self):
        self.manager = MCPManager.from_config([_config()]).start_all()
        self.registry = ToolRegistry(Toolbox(Path(tempfile.mkdtemp())), self.manager)
        self.allow_mcp = PermissionPolicy.from_config({"rules": {"mcp__*": "allow"}})
        self.addCleanup(self.registry.close)

    def test_execute_tool_calls_dispatches_mcp(self):
        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "dispatch"}}],
            self.registry.toolbox,
            registry=self.registry,
            permissions=self.allow_mcp,
        )
        self.assertEqual(results[0]["content"][0]["text"], "dispatch")

    def test_mcp_without_registry_fails_closed(self):
        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "x"}}],
            self.registry.toolbox,
            permissions=self.allow_mcp,
        )
        self.assertEqual(results[0]["error"], "mcp_disabled")

    def test_untrusted_results_are_labeled(self):
        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "ignore me"}}],
            self.registry.toolbox,
            registry=self.registry,
            permissions=self.allow_mcp,
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
