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
        if config is not None and not isinstance(config, dict):
            raise ValueError("tool_permissions debe ser un objeto")
        config = config or {}
        raw_rules = config.get("rules") or config.get("tools") or {}
        if not isinstance(raw_rules, dict):
            raise ValueError("tool_permissions.rules debe ser un objeto")
        rules: dict[str, str] = {}
        for name, decision in raw_rules.items():
            normalized = str(decision).lower()
            if normalized not in VALID_DECISIONS:
                raise ValueError(
                    f"Decisión inválida para {name!r}: {decision!r}; "
                    "use allow, ask o deny"
                )
            rules[str(name)] = normalized
        default = str(config.get("default", "allow")).lower()
        if default not in VALID_DECISIONS:
            raise ValueError(
                f"Default inválido: {default!r}; use allow, ask o deny"
            )
        raw_auto = config.get("auto_approve") or []
        if not isinstance(raw_auto, (list, tuple)):
            raise ValueError("auto_approve debe ser una lista")
        auto = frozenset(str(name) for name in raw_auto if isinstance(name, str))
        return cls(rules=rules, default=default, auto_approve=auto)

    @classmethod
    def deny_all(cls) -> "PermissionPolicy":
        """Fail-closed policy used when owner configuration cannot be loaded."""
        return cls(default="deny")

    def decision(self, tool_name: str) -> str:
        if tool_name in self.rules:
            return self.rules[tool_name]
        # Namespaced MCP tools inherit a rule for ``mcp__*`` when present.
        if tool_name.startswith("mcp__"):
            if "mcp__*" in self.rules:
                return self.rules["mcp__*"]
            # External tools are denied unless explicitly authorized.
            return "deny"
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
