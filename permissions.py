#!/usr/bin/env python3
"""permissions.py — Explicit per-tool policy (allow / ask / deny).

``ask`` cannot be answered in a headless gateway, so it fails closed unless the
tool is listed in ``auto_approve``. Policy is owner-controlled configuration;
skills and MCP metadata can never change it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VALID_DECISIONS = frozenset({"allow", "ask", "deny"})


@dataclass(frozen=True)
class PermissionPolicy:
    rules: dict[str, str] = field(default_factory=dict)
    default: str = "allow"
    auto_approve: frozenset[str] = frozenset()

    @classmethod
    def from_config(cls, config: dict | None) -> "PermissionPolicy":
        config = config if isinstance(config, dict) else {}
        raw_rules = config.get("rules") or config.get("tools") or {}
        rules = {
            str(name): str(decision).lower()
            for name, decision in (raw_rules or {}).items()
            if str(decision).lower() in VALID_DECISIONS
        }
        default = str(config.get("default", "allow")).lower()
        if default not in VALID_DECISIONS:
            default = "allow"
        raw_auto = config.get("auto_approve") or []
        auto = frozenset(str(name) for name in raw_auto if isinstance(name, str))
        return cls(rules=rules, default=default, auto_approve=auto)

    def decision(self, tool_name: str) -> str:
        if tool_name in self.rules:
            return self.rules[tool_name]
        # Namespaced MCP tools inherit a rule for ``mcp__*`` when present.
        if tool_name.startswith("mcp__") and "mcp__*" in self.rules:
            return self.rules["mcp__*"]
        return self.default

    def allowed(self, tool_name: str) -> bool:
        decision = self.decision(tool_name)
        if decision == "allow":
            return True
        if decision == "deny":
            return False
        return tool_name in self.auto_approve

    def reason(self, tool_name: str) -> str:
        decision = self.decision(tool_name)
        if decision == "ask" and tool_name not in self.auto_approve:
            return (
                f"'{tool_name}' requiere aprobación interactiva; en modo headless "
                "se deniega (auto_approve para permitirla)."
            )
        return f"'{tool_name}' denegada por la política de herramientas."
