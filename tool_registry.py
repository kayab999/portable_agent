#!/usr/bin/env python3
"""tool_registry.py — Unified local + MCP tool surface.

Local tools keep their existing allowlist semantics. MCP tools are additive,
namespaced (``mcp__server__tool``), and only exposed when the manager has a
successfully initialized server. Trust is explicit so callers/UI can label
remote output as untrusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp_client import MCPManager


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    source: str
    trust: str
    input_schema: dict = field(default_factory=dict)

    @property
    def power(self) -> bool:
        from tools import POWER_TOOLS

        return self.name in POWER_TOOLS


class ToolRegistry:
    """Resolve tool names to a local toolbox method or an MCP server call."""

    def __init__(self, toolbox, mcp: MCPManager | None = None):
        self.toolbox = toolbox
        self.mcp = mcp

    def mcp_names(self) -> frozenset[str]:
        return self.mcp.names() if self.mcp else frozenset()

    def has(self, name: str) -> bool:
        if self.mcp is not None and self.mcp.resolve(name) is not None:
            return True
        return hasattr(self.toolbox, name) and name in self._local_names()

    def specs(self) -> list[ToolSpec]:
        from tools import ALLOWED_TOOLS

        specs = [
            ToolSpec(
                name=name,
                description="",
                source="local",
                trust="local",
            )
            for name in sorted(ALLOWED_TOOLS)
        ]
        if self.mcp is not None:
            specs.extend(
                ToolSpec(
                    name=tool.namespaced,
                    description=tool.description,
                    source=f"mcp:{tool.server}",
                    trust="untrusted",
                    input_schema=tool.input_schema,
                )
                for tool in self.mcp.tools()
            )
        return specs

    def call(self, name: str, args: dict) -> dict:
        if self.mcp is not None and self.mcp.resolve(name) is not None:
            return self.mcp.call(name, args)
        if name in self._local_names() and hasattr(self.toolbox, name):
            method = getattr(self.toolbox, name)
            return method(**(args or {}))
        return {
            "ok": False,
            "error": "herramienta_no_encontrada",
            "message": f"La herramienta '{name}' no existe o no está permitida.",
        }

    @staticmethod
    def _local_names() -> frozenset[str]:
        from tools import ALLOWED_TOOLS

        return ALLOWED_TOOLS

    def close(self) -> None:
        if self.mcp is not None:
            self.mcp.close()
