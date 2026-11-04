#!/usr/bin/env python3
"""Adversarial sandbox escape tests — must stay red-team green in CI."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import Toolbox, execute_tool_calls, validate_python_code


class TestSandboxEscape(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "workspace"
        self.ws.mkdir()
        self.tb = Toolbox(workspace=self.ws)
        # Enable power tools so we test real jail, not the kill-switch
        self._env = mock.patch.dict(
            os.environ, {"BONSAI_POWER_TOOLS": "1", "BONSAI_DEBUG": "0"}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cat_etc_passwd_denied(self):
        r = self.tb.execute_command("cat /etc/passwd")
        self.assertFalse(r.get("ok"), r)
        self.assertIn(r.get("error"), ("path_no_permitido", "comando_no_permitido"))
        self.assertNotIn("root:", r.get("stdout") or "")

    def test_find_removed_from_whitelist(self):
        r = self.tb.execute_command("find /etc -name passwd")
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "comando_no_permitido")

    def test_python3_removed_from_whitelist(self):
        r = self.tb.execute_command(
            "python3 -c \"print(open('/etc/passwd').read()[:20])\""
        )
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "comando_no_permitido")

    def test_grep_absolute_path_denied(self):
        r = self.tb.execute_command("grep root /etc/passwd")
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "path_no_permitido")

    def test_python_import_os_denied(self):
        r = self.tb.python_exec("import os; print(os.listdir('/'))")
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "codigo_bloqueado")

    def test_python_open_denied(self):
        r = self.tb.python_exec("print(open('/etc/passwd').read()[:40])")
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "codigo_bloqueado")

    def test_python_safe_math_ok(self):
        r = self.tb.python_exec("import math; print(int(math.sqrt(16)))")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("4", r.get("stdout", ""))

    def test_validate_python_blocks_dunder(self):
        reason = validate_python_code("print(__builtins__)")
        self.assertIsNotNone(reason)

    def test_power_tools_disabled_without_flag(self):
        with mock.patch.dict(os.environ, {"BONSAI_POWER_TOOLS": "0", "BONSAI_DEBUG": "0"}):
            results = execute_tool_calls(
                [{"tool": "python_exec", "args": {"code": "print(1)"}}],
                self.tb,
            )
            self.assertFalse(results[0]["ok"])
            self.assertEqual(results[0]["error"], "power_tools_disabled")

    def test_workspace_cat_ok(self):
        (self.ws / "note.txt").write_text("hello-ws")
        r = self.tb.execute_command("cat note.txt")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("hello-ws", r.get("stdout", ""))

    def test_shell_metacharacters_denied(self):
        for cmd in (
            "echo hello; cat /etc/passwd",
            "cat $HOME/../etc/passwd",
            "echo $(whoami)",
            "ls | cat",
        ):
            r = self.tb.execute_command(cmd)
            self.assertFalse(r.get("ok"), msg=f"{cmd} -> {r}")
            self.assertIn(
                r.get("error"),
                ("comando_invalido", "path_no_permitido", "comando_no_permitido"),
            )

    def test_missing_file_is_not_ok(self):
        r = self.tb.execute_command("cat missing_nope.txt")
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "exit_nonzero")

    def test_io_module_denied(self):
        r = self.tb.python_exec("import io; print(1)")
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r.get("error"), "codigo_bloqueado")


if __name__ == "__main__":
    unittest.main()
