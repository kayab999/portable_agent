#!/usr/bin/env python3
import unittest

from persona import resolve_persona_tools
from tools import ALLOWED_TOOLS, POWER_TOOLS


class TestPersonaTools(unittest.TestCase):
    def test_campo_no_power_tools(self):
        tools = resolve_persona_tools("campo")
        self.assertTrue(tools <= ALLOWED_TOOLS)
        self.assertNotIn("python_exec", tools)
        self.assertNotIn("execute_command", tools)
        self.assertIn("diagnose", tools)
        self.assertIn("read_file", tools)

    def test_investigador_no_python(self):
        tools = resolve_persona_tools("investigador")
        self.assertNotIn("python_exec", tools)
        self.assertIn("write_file", tools)

    def test_analista_may_have_python(self):
        tools = resolve_persona_tools("analista")
        self.assertIn("python_exec", tools)
        # Power tool still gated by BONSAI_POWER_TOOLS at runtime
        self.assertTrue("python_exec" in POWER_TOOLS)

    def test_no_alias_leaks(self):
        tools = resolve_persona_tools("arquitecto")
        for t in tools:
            self.assertIn(t, ALLOWED_TOOLS)


if __name__ == "__main__":
    unittest.main()
