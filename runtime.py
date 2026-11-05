#!/usr/bin/env python3
"""runtime.py — shared composition root for gateway, CLI and ACP transports.

Historically ``launcher.py`` and ``acp_server.runtime_from_config`` assembled
plugins, MCP servers, hooks, permissions and subagents independently, which
allowed the two transports to drift apart. This module is the single place
where optional capability surfaces are wired together.

Security invariants preserved here:

* every optional surface is gated by its config flag (default off);
* plugin files are parsed as declarative manifests, never executed;
* MCP servers are stdio-only and opt-in;
* hooks/permissions failures degrade to "disabled", never to "allow all".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from permissions import PermissionPolicy
from tools import get_toolbox


@dataclass
class Runtime:
    toolbox: object
    hooks: object | None = None
    permissions: object | None = None
    registry: object | None = None
    subagent_index: object | None = None
    plugins: list = field(default_factory=list)
    plugin_agent_roots: list[Path] = field(default_factory=list)
    mcp_configs: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def close(self) -> None:
        if self.registry is not None:
            try:
                self.registry.close()
            except Exception:
                pass


def build_runtime(
    config: dict,
    root: Path,
    *,
    load_subagents: bool = False,
    on_warning: Callable[[str], None] | None = None,
    on_info: Callable[[str], None] | None = None,
) -> Runtime:
    """Assemble optional capability surfaces exactly once per process."""
    config = config or {}
    root = Path(root).resolve()
    toolbox = get_toolbox()

    def warn(message: str) -> None:
        if on_warning:
            on_warning(message)

    def info(message: str) -> None:
        if on_info:
            on_info(message)

    mcp_configs = list(config.get("mcp_servers") or [])
    plugins: list = []
    plugin_agent_roots: list[Path] = []
    if config.get("plugins_enabled"):
        try:
            from plugin_adapters import load_plugins, merge_mcp_servers, skill_roots
            from skills import configure_skill_roots

            plugins, plugin_errors = load_plugins(config.get("plugin_paths") or [])
            for item in plugin_errors:
                warn(f"Plugin {item.get('path')}: {item.get('error')}")
            configure_skill_roots(skill_roots(plugins))
            plugin_agent_roots = [agent for plugin in plugins for agent in plugin.agents]
            mcp_configs.extend(
                merge_mcp_servers(
                    plugins, enable=bool(config.get("plugins_enable_mcp", False))
                )
            )
            if plugins:
                info(
                    "Plugins: "
                    + ", ".join(
                        f"{plugin.name} ({plugin.format}, "
                        f"{len(plugin.skills)} skills, "
                        f"{len(plugin.mcp_servers)} MCP)"
                        for plugin in plugins
                    )
                )
        except Exception as exc:
            warn(f"Plugins deshabilitados por error: {exc}")

    subagent_index = None
    if load_subagents and config.get("subagents_enabled", False):
        try:
            from subagents import SubagentIndex

            subagent_index = SubagentIndex.load(root, extra_roots=plugin_agent_roots)
            for item in subagent_index.errors:
                warn(f"Subagente {item.get('path')}: {item.get('error')}")
            if subagent_index.agents:
                info(f"Subagentes: {', '.join(sorted(subagent_index.agents))}")
        except Exception as exc:
            warn(f"Subagentes deshabilitados por error: {exc}")

    registry = None
    if config.get("mcp_enabled"):
        try:
            from mcp_client import MCPManager
            from tool_registry import ToolRegistry

            manager = MCPManager.from_config(mcp_configs).start_all()
            for item in manager.errors:
                warn(f"MCP {item.get('name')}: {item.get('error')}")
            if manager.servers:
                info(
                    "MCP: "
                    + ", ".join(
                        f"{name} ({len(server.tools)} tools)"
                        for name, server in manager.servers.items()
                    )
                )
            registry = ToolRegistry(toolbox, manager)
        except Exception as exc:
            warn(f"MCP deshabilitado por error: {exc}")

    hooks = None
    try:
        from hooks import HookManager

        hooks = HookManager.from_config(config.get("hooks"), cwd=root)
        if hooks.enabled:
            active = sum(len(items) for items in hooks.specs.values())
            info(f"Hooks: {active} declarados")
    except Exception as exc:
        warn(f"Hooks deshabilitados por error: {exc}")
        hooks = None

    try:
        permissions = PermissionPolicy.from_config(config.get("tool_permissions"))
    except Exception as exc:
        warn(f"Permisos en deny-all por error de configuración: {exc}")
        permissions = PermissionPolicy.deny_all()

    return Runtime(
        toolbox=toolbox,
        hooks=hooks,
        permissions=permissions,
        registry=registry,
        subagent_index=subagent_index,
        plugins=plugins,
        plugin_agent_roots=plugin_agent_roots,
        mcp_configs=mcp_configs,
    )
