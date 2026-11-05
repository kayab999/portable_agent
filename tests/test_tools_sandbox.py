#!/usr/bin/env python3
"""Regression tests for toolbox path confinement and core tools."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import (
    ALLOWED_TOOLS,
    Toolbox,
    execute_tool_calls,
    format_tool_results,
    parse_tool_calls,
)


class TestToolsSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "workspace"
        self.ws.mkdir()
        self.tb = Toolbox(workspace=self.ws)
        self._env = mock.patch.dict(
            os.environ,
            {
                "BONSAI_POWER_TOOLS": "1",
                "BONSAI_UNSAFE_PYTHON_EXEC": "1",
                "BONSAI_DEBUG": "0",
            },
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_write_roundtrip(self):
        w = self.tb.write_file("hello.txt", "rc-content")
        self.assertTrue(w["ok"], w)
        r = self.tb.read_file("hello.txt")
        self.assertTrue(r["ok"], r)
        self.assertIn("rc-content", r["content"])

    def test_path_traversal_blocked(self):
        with self.assertRaises(PermissionError):
            self.tb.validate_path("/etc/passwd")
        with self.assertRaises(PermissionError):
            self.tb.validate_path("../outside.txt")
        with self.assertRaises(PermissionError):
            self.tb.validate_path("../outside.txt", write=True)

    def test_prefix_sibling_not_confused(self):
        # workspace_evil must not match workspace via startswith
        evil = Path(str(self.ws) + "_evil")
        evil.mkdir(exist_ok=True)
        (evil / "secret.txt").write_text("nope")
        # absolute path into sibling
        with self.assertRaises(PermissionError):
            self.tb.validate_path(str(evil / "secret.txt"))

    def test_write_outside_denied(self):
        r = self.tb.write_file("/tmp/bonsai_should_fail.txt", "x")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "acceso_denegado")

    def test_python_exec(self):
        r = self.tb.python_exec("print(2+2)")
        self.assertTrue(r["ok"], r)
        self.assertIn("4", r.get("stdout", ""))

    def test_python_exec_blocked_pattern(self):
        r = self.tb.python_exec("import subprocess; print(1)")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "codigo_bloqueado")

    def test_allowed_tools_enforced(self):
        results = execute_tool_calls(
            [{"tool": "destroy_system", "args": {}}], self.tb
        )
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["error"], "herramienta_no_encontrada")

    def test_format_tool_results_non_mutating(self):
        original = {"tool": "diagnose", "ok": True, "x": 1}
        payload = [original]
        text = format_tool_results(payload)
        self.assertIn("diagnose", text)
        self.assertEqual(original.get("tool"), "diagnose")
        self.assertIn("tool", original)

    def test_parse_and_execute_list_dir(self):
        calls = parse_tool_calls(
            '<tool_call>{"tool": "list_dir", "args": {"path": "."}}</tool_call>'
        )
        self.assertEqual(len(calls), 1)
        results = execute_tool_calls(calls, self.tb)
        self.assertTrue(results[0]["ok"], results)

    def test_execute_command_cwd_workspace(self):
        (self.ws / "marker.txt").write_text("here")
        r = self.tb.execute_command("ls")
        self.assertTrue(r["ok"], r)
        self.assertIn("marker.txt", r.get("stdout", ""))

    def test_list_tool_names(self):
        names = self.tb.list_tool_names()
        self.assertIn("python_exec", names)
        self.assertEqual(set(names), set(ALLOWED_TOOLS))


if __name__ == "__main__":
    unittest.main()
