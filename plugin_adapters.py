#!/usr/bin/env python3
"""plugin_adapters.py — Read-only adapters for foreign plugin packages.

Supported manifests (metadata only):
  - Claude Code:  .claude-plugin/plugin.json
  - Gemini CLI:   gemini-extension.json
  - Hermes:       plugin.yaml
  - Antigravity:  plugin.json

Only declarative components are imported: skills, prompt-only agents, MCP
server definitions, and hook declarations. JavaScript/TypeScript/Python plugin
code is never executed. Anything that would require a runtime is reported as
unsupported.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from skills import SkillError, parse_frontmatter

_NAME_RE = re.compile(r"[^a-z0-9_-]+")
_UNSUPPORTED_MARKERS = (
    "package.json",
    "index.js",
    "index.ts",
    "index.mjs",
    "index.cjs",
    "main.py",
    "__init__.py",
)


class PluginError(ValueError):
    """Raised when a plugin directory cannot be interpreted safely."""


@dataclass
class Plugin:
    name: str
    root: Path
    format: str
    skills: list[Path] = field(default_factory=list)
    agents: list[Path] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)


def _sanitize(value: str) -> str:
    cleaned = _NAME_RE.sub("_", str(value or "").lower()).strip("_")
    return cleaned or "plugin"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginError(f"{path} must contain a JSON object")
    return data


def _read_yaml(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginError(f"cannot read {path}: {exc}") from exc
    try:
        meta, _ = parse_frontmatter(f"---\n{text}\n---\n")
    except SkillError as exc:
        raise PluginError(f"cannot parse {path}: {exc}") from exc
    return meta


def _find_manifest(root: Path) -> tuple[str, Path] | None:
    candidates = (
        ("claude", root / ".claude-plugin" / "plugin.json"),
        ("gemini", root / "gemini-extension.json"),
        ("hermes", root / "plugin.yaml"),
        ("antigravity", root / "plugin.json"),
    )
    for kind, path in candidates:
        if path.is_file():
            return kind, path
    return None


def _collect_dirs(root: Path, explicit: list, kind: str) -> list[Path]:
    found: list[Path] = []
    root = root.resolve()

    def add_skill_tree(candidate: Path) -> None:
        candidate = candidate.resolve()
        if not candidate.is_dir() or not candidate.is_relative_to(root):
            return
        if (candidate / "SKILL.md").is_file():
            found.append(candidate)
            return
        try:
            children = sorted(candidate.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir() and (child / "SKILL.md").is_file():
                found.append(child.resolve())

    if kind == "skill":
        for item in explicit:
            if isinstance(item, str):
                add_skill_tree(root / item)
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs if d not in ("node_modules", ".git", ".venv")
            ]
            if "SKILL.md" in files:
                found.append(Path(current).resolve())
    else:
        for item in explicit:
            if isinstance(item, str):
                candidate = (root / item).resolve()
                if candidate.is_dir() and candidate.is_relative_to(root):
                    found.append(candidate)
        default = (root / "agents").resolve()
        if default.is_dir():
            found.append(default)
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                d for d in dirs if d not in ("node_modules", ".git", ".venv")
            ]
            if "agents" in Path(current).parts and any(
                name.endswith(".md") for name in files
            ):
                found.append(Path(current).resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _normalize_mcp(plugin_name: str, raw) -> list[dict]:
    entries: list[tuple[str, dict]] = []
    if isinstance(raw, dict):
        entries = [(str(name), cfg) for name, cfg in raw.items()]
    elif isinstance(raw, list):
        entries = [
            (str(cfg.get("name", f"server-{index}")), cfg)
            for index, cfg in enumerate(raw)
            if isinstance(cfg, dict)
        ]
    servers: list[dict] = []
    for name, cfg in entries:
        if not isinstance(cfg, dict):
            continue
        command = cfg.get("command")
        if not command:
            continue
        servers.append(
            {
                "name": _sanitize(f"{plugin_name}__{name}")[:41],
                "command": str(command),
                "args": [str(arg) for arg in (cfg.get("args") or [])],
                "env": {
                    str(key): str(value)
                    for key, value in (cfg.get("env") or {}).items()
                },
                # Plugin MCP servers are inert until the owner opts in.
                "enabled": False,
                "tools_allow": [
                    str(tool) for tool in (cfg.get("tools_allow") or [])
                ],
            }
        )
    return servers


def load_plugin(path: Path) -> Plugin:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise PluginError(f"plugin path is not a directory: {path}")
    manifest_info = _find_manifest(root)
    if manifest_info is None:
        raise PluginError(f"no supported plugin manifest in {path}")
    kind, manifest_path = manifest_info
    manifest = (
        _read_yaml(manifest_path)
        if manifest_path.suffix == ".yaml"
        else _read_json(manifest_path)
    )
    plugin_name = _sanitize(str(manifest.get("name") or root.name))

    skills = _collect_dirs(root, _as_list(manifest.get("skills")), "skill")
    agents = _collect_dirs(root, _as_list(manifest.get("agents")), "agent")

    raw_mcp = (
        manifest.get("mcpServers")
        or manifest.get("mcp_servers")
        or {}
    )
    mcp_servers = _normalize_mcp(plugin_name, raw_mcp)

    hooks: dict = {}
    hooks_file = root / "hooks" / "hooks.json"
    if hooks_file.is_file():
        hooks = {"path": str(hooks_file), "enabled": False, "raw": _read_json(hooks_file)}
    elif isinstance(manifest.get("hooks"), dict):
        hooks = {"enabled": False, "raw": manifest["hooks"]}

    unsupported: list[str] = []
    for marker in _UNSUPPORTED_MARKERS:
        if (root / marker).exists():
            unsupported.append(marker)
    for pattern in ("*.ts", "*.js", "*.mjs", "*.cjs"):
        unsupported.extend(
            str(candidate.relative_to(root))
            for candidate in root.glob(pattern)
        )
    unsupported = sorted(set(unsupported))

    return Plugin(
        name=plugin_name,
        root=root,
        format=kind,
        skills=skills,
        agents=agents,
        mcp_servers=mcp_servers,
        hooks=hooks,
        unsupported=unsupported,
        manifest=manifest,
    )


def load_plugins(paths: list[str | Path] | None) -> tuple[list[Plugin], list[dict]]:
    plugins: list[Plugin] = []
    errors: list[dict] = []
    for path in paths or []:
        try:
            plugins.append(load_plugin(Path(path)))
        except PluginError as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return plugins, errors


def merge_mcp_servers(plugins: list[Plugin], *, enable: bool = False) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for plugin in plugins:
        for server in plugin.mcp_servers:
            if server["name"] in seen:
                continue
            seen.add(server["name"])
            entry = dict(server)
            entry["enabled"] = bool(enable or server.get("enabled"))
            merged.append(entry)
    return merged


def skill_roots(plugins: list[Plugin]) -> list[Path]:
    roots: list[Path] = []
    for plugin in plugins:
        roots.extend(plugin.skills)
    return roots
