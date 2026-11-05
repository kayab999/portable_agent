# Plugin authoring

Plugins let a project expose skills, Markdown agents and stdio MCP server
declarations to Bonsai Portable. **Plugin code is never executed.** Adapters
only read declarative manifests.

Enable explicitly:

```json
{
  "plugins_enabled": true,
  "plugin_paths": ["/path/to/plugins"],
  "plugins_enable_mcp": false
}
```

`plugins_enable_mcp` must also be true to merge plugin-declared MCP servers.

## Supported layouts

The adapter recognizes common declarative formats:

| Format | Manifest | Declares |
|---|---|---|
| Claude | `plugin.json` / `.claude-plugin/plugin.json` | skills, agents, MCP servers |
| Gemini | `gemini-extension.json` | skills, MCP servers |
| Antigravity | `plugin.toml` / `plugin.json` | skills, agents |
| Hermes | `hermes.yaml` | skills, agents, MCP servers |

Unknown or malformed manifests are reported and skipped; they never abort
startup.

## Skills and agents

- Skills are Markdown files with YAML frontmatter (`name`, `description`).
- Agents are Markdown files with `name`, `description`, optional `tools` and
  `model`.
- Agent `tools` are an **upper bound**, intersected with the global allowlist,
  the power-tool gate and the active persona allowlist.
- Bodies are untrusted prompt content and are wrapped in delimiters.

## MCP servers

Plugin MCP declarations are merged only when `mcp_enabled` and
`plugins_enable_mcp` are both true. Only stdio commands are supported. The
runtime never accepts remote HTTP/SSE/OAuth MCP endpoints. If a plugin declares
`tools_allow`, only those tools are exposed; an explicit empty list exposes
none, while omitting the key exposes no tools unless `allow_all_tools: true` is set.

## What plugins cannot do

- Execute JS/TS/Python or install packages.
- Add tools outside `ALLOWED_TOOLS`.
- Enable power tools (`execute_command`, `fetch_url`, or `python_exec` with its
  separate `BONSAI_UNSAFE_PYTHON_EXEC=1` opt-in).
- Change hooks, permissions, sandbox roots or network policy.
- Override built-in skills or agents silently (duplicates are reported).

## Validation

`tests/test_plugins_subagents.py` covers manifest parsing, duplicate handling,
missing manifests and privilege intersection.
