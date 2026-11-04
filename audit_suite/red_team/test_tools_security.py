import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import Toolbox, execute_tool_calls, parse_tool_calls


class TestToolsSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.toolbox = Toolbox(workspace=self.workspace)
        self._env = mock.patch.dict(
            os.environ, {"BONSAI_POWER_TOOLS": "1", "BONSAI_DEBUG": "0"}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_path_resolution_traversal(self):
        with self.assertRaises(PermissionError):
            self.toolbox.validate_path("/etc/passwd")
        with self.assertRaises(PermissionError):
            self.toolbox.validate_path("../outside.txt")

    def test_valid_path(self):
        path = self.toolbox.validate_path("test.txt")
        self.assertTrue(path.is_relative_to(self.workspace.resolve()))

    def test_private_method_not_callable_via_allowlist(self):
        # Attempt to invoke private method name
        calls = parse_tool_calls(
            '<tool_call>{"tool": "_log", "args": {"tool": "x", "args": {}, "result": {"ok": true}}}</tool_call>'
        )
        results = execute_tool_calls(calls, self.toolbox)
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["error"], "herramienta_no_encontrada")

    def test_command_injection_not_via_shell(self):
        # shell metacharacters rejected before execution
        r = self.toolbox.execute_command("echo hello; cat /etc/passwd")
        self.assertFalse(r.get("ok"), r)
        self.assertIn(
            r.get("error"),
            ("comando_invalido", "comando_no_permitido", "path_no_permitido"),
        )
        self.assertNotIn("root:", r.get("stdout") or "")

    def test_cat_etc_denied(self):
        r = self.toolbox.execute_command("cat /etc/passwd")
        self.assertFalse(r.get("ok"))
        self.assertNotIn("root:", r.get("stdout") or "")


if __name__ == "__main__":
    unittest.main()
