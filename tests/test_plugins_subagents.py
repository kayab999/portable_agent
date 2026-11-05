#!/usr/bin/env python3
"""Declarative plugin adapters and subagent definitions."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin_adapters import (
    PluginError,
    load_plugin,
    load_plugins,
    merge_mcp_servers,
    skill_roots,
)
from subagents import (
    SubagentError,
    SubagentIndex,
    allowed_tools_for,
    run_subagent,
    subagent_from_file,
)


def _write_skill(root: Path, name: str):
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: demo\n"
        "---\n\nBody\n",
        encoding="utf-8",
    )


def _write_agent(root: Path, name: str, tools: str = "read_file, list_dir"):
    folder = root / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: reviewer\n"
        f"tools: [{tools}]\n"
        "model: bonsai-8b\n"
        "---\n\nYou review code.\n",
        encoding="utf-8",
    )


class TestPluginAdapters(unittest.TestCase):
    def test_claude_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "my-plugin",
                        "mcpServers": {
                            "docs": {"command": "node", "args": ["server.js"]}
                        },
                    }
                )
            )
            _write_skill(root, "demo-skill")
            _write_agent(root, "reviewer")
            (root / "index.js").write_text("console.log('x')")
            plugin = load_plugin(root)
        self.assertEqual(plugin.format, "claude")
        self.assertEqual(plugin.name, "my-plugin")
        self.assertEqual(len(plugin.skills), 1)
        self.assertEqual(len(plugin.agents), 1)
        self.assertEqual(plugin.mcp_servers[0]["name"], "my-plugin__docs")
        self.assertFalse(plugin.mcp_servers[0]["enabled"])
        self.assertIn("index.js", plugin.unsupported)

    def test_gemini_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "gemini-extension.json").write_text(
                json.dumps(
                    {
                        "name": "gemini-demo",
                        "mcpServers": {
                            "server": {"command": "python", "args": ["-m", "x"]}
                        },
                    }
                )
            )
            plugin = load_plugin(root)
        self.assertEqual(plugin.format, "gemini")
        self.assertEqual(plugin.mcp_servers[0]["command"], "python")

    def test_hermes_yaml_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plugin.yaml").write_text(
                "name: hermes-demo\n"
                "plugin_type: hook\n"
                "hooks: [pre_llm_call]\n",
                encoding="utf-8",
            )
            plugin = load_plugin(root)
        self.assertEqual(plugin.format, "hermes")
        self.assertEqual(plugin.name, "hermes-demo")

    def test_antigravity_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plugin.json").write_text(json.dumps({"name": "anti-demo"}))
            plugin = load_plugin(root)
        self.assertEqual(plugin.format, "antigravity")

    def test_collects_and_merges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "merge-demo",
                        "mcpServers": {"one": {"command": "echo"}},
                    }
                )
            )
            _write_skill(root, "merge-skill")
            plugin = load_plugin(root)
            plugins, errors = load_plugins([root, root / "missing"])
        self.assertEqual(errors[0]["path"], str(root / "missing"))
        self.assertEqual(skill_roots(plugins), plugin.skills)
        disabled = merge_mcp_servers(plugins)
        enabled = merge_mcp_servers(plugins, enable=True)
        self.assertFalse(disabled[0]["enabled"])
        self.assertTrue(enabled[0]["enabled"])

    def test_mcp_tools_allow_preserves_omitted_vs_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "allow-demo",
                        "mcpServers": {
                            "omitted": {"command": "echo"},
                            "empty": {"command": "echo", "tools_allow": []},
                            "listed": {
                                "command": "echo",
                                "tools_allow": ["echo"],
                            },
                        },
                    }
                )
            )
            plugin = load_plugin(root)
        by_name = {
            server["name"].split("__", 1)[1]: server
            for server in plugin.mcp_servers
        }
        self.assertNotIn("tools_allow", by_name["omitted"])
        self.assertEqual(by_name["empty"]["tools_allow"], [])
        self.assertEqual(by_name["listed"]["tools_allow"], ["echo"])

    def test_missing_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(PluginError):
                load_plugin(Path(td))


class TestSubagents(unittest.TestCase):
    def test_parse_and_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_agent(root, "reviewer", "read_file, write_file, python_exec")
            agent = subagent_from_file(root / "agents" / "reviewer.md", root=root / "agents")
        self.assertEqual(agent.name, "reviewer")
        self.assertIn("read_file", agent.tools)
        allowed = allowed_tools_for(agent)
        self.assertIn("read_file", allowed)
        self.assertIn("load_skill", allowed)
        self.assertIn("write_file", allowed)
        self.assertIn("python_exec", allowed)  # still power-gated at execution

    def test_default_allowlist_excludes_power_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "agents"
            folder.mkdir()
            (folder / "safe.md").write_text(
                "---\nname: safe\ndescription: safe\n---\n\nBe safe.\n",
                encoding="utf-8",
            )
            agent = subagent_from_file(folder / "safe.md", root=folder)
        allowed = allowed_tools_for(agent)
        self.assertNotIn("python_exec", allowed)
        self.assertNotIn("execute_command", allowed)

    def test_invalid_name_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "agents"
            folder.mkdir()
            (folder / "bad.md").write_text(
                "---\nname: Bad Name\ndescription: x\n---\n\nbody\n",
                encoding="utf-8",
            )
            with self.assertRaises(SubagentError):
                subagent_from_file(folder / "bad.md", root=folder)

    def test_discovery_and_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_agent(root, "reviewer")
            # Lower-priority duplicate must be reported, not overwrite.
            extra = root / "plugin-agents"
            extra.mkdir()
            (extra / "reviewer.md").write_text(
                "---\nname: reviewer\ndescription: duplicate\n---\n\nbody\n",
                encoding="utf-8",
            )
            index = SubagentIndex.load(root, extra_roots=[extra])
        self.assertIsNotNone(index.get("reviewer"))
        self.assertTrue(index.errors)

    def test_run_subagent_returns_answer(self):
        class FakeClient:
            def chat_completion(self, model, messages):
                return {"content": "FINAL ANSWER: reviewed"}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_agent(root, "reviewer")
            agent = subagent_from_file(
                root / "agents" / "reviewer.md", root=root / "agents"
            )
            result = run_subagent(agent, "check this", FakeClient())
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "reviewed")

    def test_run_subagent_restricts_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_agent(root, "reviewer", "read_file")
            agent = subagent_from_file(
                root / "agents" / "reviewer.md", root=root / "agents"
            )
            with patch(
                "agent_loop.interactive_react", return_value=("ok", [], [])
            ) as mocked:
                run_subagent(agent, "check", object())
        tools = mocked.call_args.kwargs["persona_tools"]
        self.assertIn("read_file", tools)
        self.assertNotIn("write_file", tools)

    def test_run_subagent_cannot_widen_persona_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_agent(root, "reviewer", "read_file")
            agent = subagent_from_file(
                root / "agents" / "reviewer.md", root=root / "agents"
            )
            with patch(
                "agent_loop.interactive_react", return_value=("ok", [], [])
            ) as mocked:
                run_subagent(
                    agent,
                    "check",
                    object(),
                    persona_tools=frozenset({"execute_command"}),
                )
        tools = mocked.call_args.kwargs["persona_tools"]
        self.assertNotIn("read_file", tools)
        self.assertNotIn("execute_command", tools)
        self.assertEqual(tools, frozenset())


if __name__ == "__main__":
    unittest.main()
