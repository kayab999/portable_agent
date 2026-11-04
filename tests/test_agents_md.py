#!/usr/bin/env python3
"""AGENTS.md discovery, precedence and context budgeting."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_md import MAX_AGENTS_BYTES, discover_agents_files, load_agents_md


class TestAgentsMd(unittest.TestCase):
    def test_root_and_nested_files_load_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENTS.md").write_text("root rules", encoding="utf-8")
            sub = root / "pkg"
            sub.mkdir()
            (sub / "AGENTS.md").write_text("nested rules", encoding="utf-8")
            files = discover_agents_files(root, sub)
            self.assertEqual(files, [root / "AGENTS.md", sub / "AGENTS.md"])
            text = load_agents_md(root, sub)
        self.assertLess(text.index("root rules"), text.index("nested rules"))
        self.assertIn("### AGENTS.md", text)
        self.assertIn("### pkg/AGENTS.md", text)

    def test_no_files_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_agents_md(Path(td), Path(td)), "")

    def test_budget_is_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "AGENTS.md").write_text("x" * (MAX_AGENTS_BYTES * 2))
            text = load_agents_md(root, root)
        self.assertLessEqual(len(text), MAX_AGENTS_BYTES + 120)
        self.assertIn("truncated", text)

    def test_cwd_outside_root_is_ignored(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            (root / "AGENTS.md").write_text("inside", encoding="utf-8")
            outside = Path(other)
            (outside / "AGENTS.md").write_text("outside", encoding="utf-8")
            files = discover_agents_files(root, outside)
            text = load_agents_md(root, outside)
        self.assertEqual(files, [root / "AGENTS.md"])
        self.assertNotIn("outside", text)


if __name__ == "__main__":
    unittest.main()
