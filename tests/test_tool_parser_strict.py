#!/usr/bin/env python3
"""The tool parser must not execute JSON that was only described in prose."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent_loop import extract_tool_calls
from tools import execute_tool_calls, get_toolbox, parse_tool_calls

FENCE = "```"


def canonical(payload: str) -> str:
    return f"<tool_call>{payload}</tool_call>"


class TestStrictToolParser(unittest.TestCase):
    def test_fenced_json_is_ignored_by_default(self):
        text = (
            "Aqui va un ejemplo:\n"
            f"{FENCE}json\n"
            '{"tool": "write_file", "args": {"path": "x.txt", "content": "boom"}}\n'
            f"{FENCE}\n"
            "No lo ejecutes."
        )
        self.assertEqual(parse_tool_calls(text), [])

    def test_standalone_json_line_is_ignored(self):
        text = 'El JSON fue:\n{"tool": "list_dir", "args": {"path": "."}}'
        self.assertEqual(parse_tool_calls(text), [])

    def test_canonical_block_is_parsed(self):
        text = canonical('{"tool": "diagnose", "args": {}}')
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "diagnose")

    def test_unknown_tool_parses_but_cannot_execute(self):
        text = canonical('{"tool": "rm_rf", "args": {"path": "/"}}')
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        results = execute_tool_calls(calls, get_toolbox())
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["error"], "herramienta_no_encontrada")

    def test_non_dict_args_are_ignored(self):
        text = canonical('{"tool": "list_dir", "args": ["."]}')
        self.assertEqual(parse_tool_calls(text), [])

    def test_call_limit(self):
        text = "".join(
            canonical(f'{{"tool": "diagnose", "args": {{"n": {i}}}}}')
            for i in range(20)
        )
        self.assertLessEqual(len(parse_tool_calls(text)), 8)

    def test_legacy_mode_restores_fenced_parsing(self):
        text = (
            f"{FENCE}json\n"
            '{"tool": "diagnose", "args": {}}\n'
            f"{FENCE}"
        )
        calls = parse_tool_calls(text, legacy=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "diagnose")

    def test_agent_loop_does_not_parse_tool_lines_by_default(self):
        with patch.dict(os.environ, {"BONSAI_LEGACY_TOOL_PARSING": "0"}):
            self.assertEqual(extract_tool_calls("TOOL: list_dir ."), [])
            self.assertEqual(
                extract_tool_calls('{"tool": "list_dir", "args": {"path": "."}}'),
                [],
            )

    def test_agent_loop_legacy_flag(self):
        with patch.dict(os.environ, {"BONSAI_LEGACY_TOOL_PARSING": "1"}):
            calls = extract_tool_calls("TOOL: list_dir .")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["tool"], "list_dir")

    def test_canonical_still_works_with_prose(self):
        text = (
            "Necesito revisar el archivo.\n"
            + canonical('{"tool": "read_file", "args": {"path": "docs/a.md"}}')
            + "\nGracias."
        )
        calls = parse_tool_calls(text)
        self.assertEqual(calls[0]["tool"], "read_file")


if __name__ == "__main__":
    unittest.main()
