#!/usr/bin/env python3
"""Lifecycle hooks and per-tool permission policy."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hooks import HookError, HookManager
from permissions import PermissionPolicy
from tools import Toolbox, execute_tool_calls


def _hook(code: str, *, event: str = "pre_tool", timeout_ms: int = 5000):
    return {
        "enabled": True,
        event: [
            {
                "command": sys.executable,
                "args": ["-c", code],
                "timeout_ms": timeout_ms,
            }
        ],
    }


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.toolbox = Toolbox(self.tmp / "workspace")

    def test_pre_tool_block_prevents_execution(self):
        manager = HookManager.from_config(
            _hook(
                "import json,sys; d=json.load(sys.stdin); "
                "sys.exit(2 if d['tool']=='write_file' else 0)"
            ),
            cwd=self.tmp,
        )
        results = execute_tool_calls(
            [{"tool": "write_file", "args": {"path": "blocked.txt", "content": "x"}}],
            self.toolbox,
            hooks=manager,
        )
        self.assertEqual(results[0]["error"], "blocked_by_hook")
        self.assertFalse((self.tmp / "workspace" / "blocked.txt").exists())

    def test_pre_tool_allows_other_tools(self):
        manager = HookManager.from_config(
            _hook(
                "import json,sys; d=json.load(sys.stdin); "
                "sys.exit(2 if d['tool']=='write_file' else 0)"
            ),
            cwd=self.tmp,
        )
        results = execute_tool_calls(
            [{"tool": "list_dir", "args": {"path": "."}}],
            self.toolbox,
            hooks=manager,
        )
        self.assertTrue(results[0]["ok"])

    def test_json_block_decision(self):
        manager = HookManager.from_config(
            _hook("print('{\"decision\":\"block\",\"reason\":\"nope\"}')"),
            cwd=self.tmp,
        )
        result = manager.run("pre_tool", {"tool": "read_file"}, matcher="read_file")
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "nope")

    def test_timeout_fails_closed_for_pre_tool(self):
        manager = HookManager.from_config(
            _hook("import time; time.sleep(2)", timeout_ms=100),
            cwd=self.tmp,
        )
        result = manager.run("pre_tool", {"tool": "read_file"})
        self.assertTrue(result.blocked)

    def test_timeout_does_not_block_post_tool(self):
        manager = HookManager.from_config(
            {
                "enabled": True,
                "post_tool": [
                    {
                        "command": sys.executable,
                        "args": ["-c", "import time; time.sleep(2)"],
                        "timeout_ms": 100,
                    }
                ],
            },
            cwd=self.tmp,
        )
        result = manager.run("post_tool", {"tool": "read_file"})
        self.assertFalse(result.blocked)

    def test_secrets_are_not_forwarded(self):
        manager = HookManager.from_config(
            _hook(
                "import os,sys; "
                "sys.exit(2 if os.environ.get('BONSAI_SECRET') else 0)"
            ),
            cwd=self.tmp,
        )
        with patch.dict(os.environ, {"BONSAI_SECRET": "leak"}):
            result = manager.run("pre_tool", {"tool": "read_file"})
        self.assertFalse(result.blocked)

    def test_matcher_limits_scope(self):
        manager = HookManager.from_config(
            {
                "enabled": True,
                "pre_tool": [
                    {
                        "command": sys.executable,
                        "args": ["-c", "import sys; sys.exit(2)"],
                        "matcher": "write_file",
                    }
                ],
            },
            cwd=self.tmp,
        )
        self.assertFalse(manager.run("pre_tool", {"tool": "read_file"}, matcher="read_file").blocked)
        self.assertTrue(manager.run("pre_tool", {"tool": "write_file"}, matcher="write_file").blocked)

    def test_invalid_config_rejected(self):
        with self.assertRaises(HookError):
            HookManager.from_config({"enabled": True, "pre_tool": [{"args": []}]})

    def test_disabled_manager_is_inert(self):
        manager = HookManager.from_config({"pre_tool": [{"command": "false"}]})
        self.assertFalse(manager.run("pre_tool", {"tool": "read_file"}).blocked)


    def test_mcp_calls_run_post_tool_hook(self):
        marker = self.tmp / "mcp-post.txt"
        manager = HookManager.from_config(
            {
                "enabled": True,
                "post_tool": [
                    {
                        "command": sys.executable,
                        "args": [
                            "-c",
                            "import json,sys; d=json.load(sys.stdin); "
                            "open(sys.argv[1], 'w').write(d['tool'])",
                            str(marker),
                        ],
                    }
                ],
            },
            cwd=self.tmp,
        )

        class FakeRegistry:
            def call(self, name, args):
                return {"tool": name, "ok": True}

        results = execute_tool_calls(
            [{"tool": "mcp__fake__echo", "args": {"text": "hi"}}],
            self.toolbox,
            registry=FakeRegistry(),
            hooks=manager,
        )
        self.assertTrue(results[0]["ok"])
        self.assertEqual(marker.read_text(), "mcp__fake__echo")


class TestPermissions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.toolbox = Toolbox(self.tmp / "workspace")

    def test_deny_blocks_before_execution(self):
        policy = PermissionPolicy.from_config({"rules": {"write_file": "deny"}})
        results = execute_tool_calls(
            [{"tool": "write_file", "args": {"path": "x.txt", "content": "x"}}],
            self.toolbox,
            permissions=policy,
        )
        self.assertEqual(results[0]["error"], "tool_denied_by_policy")
        self.assertFalse((self.tmp / "workspace" / "x.txt").exists())

    def test_ask_fails_closed_without_auto_approve(self):
        policy = PermissionPolicy.from_config({"rules": {"write_file": "ask"}})
        results = execute_tool_calls(
            [{"tool": "write_file", "args": {"path": "x.txt", "content": "x"}}],
            self.toolbox,
            permissions=policy,
        )
        self.assertEqual(results[0]["error"], "tool_denied_by_policy")

    def test_ask_allowed_with_auto_approve(self):
        policy = PermissionPolicy.from_config(
            {"rules": {"write_file": "ask"}, "auto_approve": ["write_file"]}
        )
        results = execute_tool_calls(
            [{"tool": "write_file", "args": {"path": "x.txt", "content": "x"}}],
            self.toolbox,
            permissions=policy,
        )
        self.assertTrue(results[0]["ok"])

    def test_mcp_wildcard_rule(self):
        policy = PermissionPolicy.from_config({"rules": {"mcp__*": "deny"}})
        self.assertFalse(policy.allowed("mcp__fake__echo"))
        self.assertTrue(policy.allowed("read_file"))

    def test_default_policy_is_allow(self):
        self.assertTrue(PermissionPolicy.from_config({}).allowed("read_file"))


if __name__ == "__main__":
    unittest.main()
