#!/usr/bin/env python3
"""Agent Skills parsing, discovery and progressive disclosure."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills import SkillError, SkillIndex, parse_frontmatter, skill_from_file
from tools import Toolbox


def _skill(directory: Path, name: str, description: str, body: str = "Do the thing."):
    folder = directory / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "license: MIT\n"
        "compatibility: Bonsai Portable\n"
        "allowed-tools:\n"
        "  - read_file\n"
        "  - write_file\n"
        "metadata:\n"
        "  author: test\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return folder


class TestFrontmatter(unittest.TestCase):
    def test_parses_scalars_lists_and_metadata(self):
        meta, body = parse_frontmatter(
            "---\n"
            "name: demo-skill\n"
            "description: A demo\n"
            "allowed-tools: [read_file, write_file]\n"
            "metadata:\n"
            "  version: 1\n"
            "  tags:\n"
            "    - a\n"
            "    - b\n"
            "---\n\nBody here.\n"
        )
        self.assertEqual(meta["name"], "demo-skill")
        self.assertEqual(meta["allowed-tools"], ["read_file", "write_file"])
        self.assertEqual(meta["metadata"]["version"], "1")
        self.assertEqual(meta["metadata"]["tags"], ["a", "b"])
        self.assertEqual(body, "Body here.")

    def test_literal_block(self):
        meta, _ = parse_frontmatter("---\ndescription: |\n  line one\n  line two\n---\n")
        self.assertEqual(meta["description"], "line one\nline two")

    def test_unterminated_frontmatter_rejected(self):
        with self.assertRaises(SkillError):
            parse_frontmatter("---\nname: x\n")

    def test_flow_mapping_rejected(self):
        with self.assertRaises(SkillError):
            parse_frontmatter("---\nmetadata: {a: b}\n---\n")


class TestSkillValidation(unittest.TestCase):
    def test_valid_skill_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = _skill(root, "demo-skill", "A demo")
            skill = skill_from_file(folder / "SKILL.md", root=root)
        self.assertEqual(skill.name, "demo-skill")
        self.assertEqual(skill.allowed_tools, ("read_file", "write_file"))
        self.assertEqual(skill.license, "MIT")

    def test_invalid_name_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "bad"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: Bad_Name\ndescription: nope\n---\n", encoding="utf-8"
            )
            with self.assertRaises(SkillError):
                skill_from_file(folder / "SKILL.md", root=root)

    def test_missing_description_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "bad"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: bad-skill\n---\n", encoding="utf-8"
            )
            with self.assertRaises(SkillError):
                skill_from_file(folder / "SKILL.md", root=root)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            outside = _skill(Path(other), "escape-skill", "outside")
            with self.assertRaises(SkillError):
                skill_from_file(outside / "SKILL.md", root=root)


class TestSkillIndex(unittest.TestCase):
    def test_discovery_precedence_and_duplicate_report(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            root = Path(td)
            first = root / ".agents" / "skills"
            second = root / ".claude" / "skills"
            _skill(first, "shared-skill", "from agents")
            _skill(second, "shared-skill", "from claude")
            _skill(second, "claude-only", "only claude")
            index = SkillIndex.load(root, Path(home))
        self.assertEqual(index.get("shared-skill").description, "from agents")
        self.assertIsNotNone(index.get("claude-only"))
        self.assertTrue(any(c["name"] == "shared-skill" for c in index.conflicts))

    def test_malformed_skill_is_skipped(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            root = Path(td)
            root.joinpath(".agents/skills/bad-skill").mkdir(parents=True)
            root.joinpath(".agents/skills/bad-skill/SKILL.md").write_text(
                "---\nname: BAD\ndescription: x\n---\n", encoding="utf-8"
            )
            _skill(root / ".agents" / "skills", "good-skill", "good")
            index = SkillIndex.load(root, Path(home))
        self.assertIsNone(index.get("bad-skill"))
        self.assertIsNotNone(index.get("good-skill"))
        self.assertTrue(index.conflicts)

    def test_index_text_is_bounded(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            root = Path(td)
            for i in range(50):
                _skill(
                    root / ".agents" / "skills",
                    f"skill-{i}",
                    "description " * 30,
                )
            index = SkillIndex.load(root, Path(home))
            text = index.index_text(max_chars=500)
        self.assertLessEqual(len(text), 560)
        self.assertIn("omitted", text)

    def test_search_ranks_by_overlap(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            root = Path(td)
            _skill(root / ".agents" / "skills", "pdf-tools", "extract text from PDF")
            _skill(root / ".agents" / "skills", "image-tools", "resize images")
            index = SkillIndex.load(root, Path(home))
        results = index.search("extract pdf text")
        self.assertEqual(results[0].name, "pdf-tools")


class TestLoadSkillTool(unittest.TestCase):
    def test_load_skill_returns_body(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            root = Path(td)
            _skill(root / ".agents" / "skills", "demo-skill", "A demo", "STEP ONE")
            index = SkillIndex.load(root, Path(home))
            toolbox = Toolbox(root / "workspace")
            with patch("skills.get_skill_index", return_value=index):
                result = toolbox.load_skill("demo-skill")
        self.assertTrue(result["ok"])
        self.assertIn("STEP ONE", result["instructions"])

    def test_load_skill_unknown_lists_available(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
            root = Path(td)
            _skill(root / ".agents" / "skills", "demo-skill", "A demo")
            index = SkillIndex.load(root, Path(home))
            toolbox = Toolbox(root / "workspace")
            with patch("skills.get_skill_index", return_value=index):
                result = toolbox.load_skill("missing-skill")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "skill_no_encontrada")


if __name__ == "__main__":
    unittest.main()
