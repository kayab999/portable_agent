#!/usr/bin/env python3
"""config_schema.py — validation for the JSON config file.

The loader is intentionally tolerant (unknown keys are kept), but operators
get actionable errors for values that would silently weaken security or break
startup. This module performs no I/O.
"""

from __future__ import annotations

KNOWN_KEYS = frozenset(
    {
        "port",
        "host",
        "ngl",
        "threads",
        "open_browser",
        "last_model",
        "rag_enabled",
        "rag_max_chars",
        "rag_top_k",
        "experience_persistence",
        "agents_md_enabled",
        "skills_enabled",
        "mcp_enabled",
        "mcp_servers",
        "hooks",
        "tool_permissions",
        "plugins_enabled",
        "plugin_paths",
        "plugins_enable_mcp",
        "subagents_enabled",
        "yubikey_auto",
        "llm_port_offset",
        "max_iterations",
        "knowledge_db_enabled",
        "knowledge_context_enabled",
        "agent_runs_enabled",
        "agent_tracing_enabled",
    }
)

_BOOL_KEYS = (
    "open_browser",
    "rag_enabled",
    "experience_persistence",
    "agents_md_enabled",
    "skills_enabled",
    "mcp_enabled",
    "plugins_enabled",
    "plugins_enable_mcp",
    "subagents_enabled",
    "yubikey_auto",
    "knowledge_db_enabled",
    "knowledge_context_enabled",
    "agent_runs_enabled",
    "agent_tracing_enabled",
)


def validate_config(config: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config, dict):
        return {"errors": ["config must be a JSON object"], "warnings": []}

    for key in sorted(set(config) - KNOWN_KEYS):
        if not key.startswith("_"):
            warnings.append(f"unknown config key: {key}")

    try:
        port = int(config.get("port", 8080))
        if not 1 <= port <= 65535:
            errors.append("port must be between 1 and 65535")
    except (TypeError, ValueError):
        errors.append("port must be an integer")

    host = str(config.get("host", "127.0.0.1"))
    if host not in ("127.0.0.1", "localhost"):
        warnings.append(
            f"host {host!r} is non-loopback; it is clamped unless BONSAI_ALLOW_LAN=1"
        )

    for key in _BOOL_KEYS:
        if key in config and not isinstance(config[key], bool):
            errors.append(f"{key} must be a boolean")

    try:
        max_chars = int(config.get("rag_max_chars", 6000))
        if not 0 <= max_chars <= 1_000_000:
            errors.append("rag_max_chars must be between 0 and 1000000")
    except (TypeError, ValueError):
        errors.append("rag_max_chars must be an integer")

    try:
        top_k = int(config.get("rag_top_k", 3))
        if not 1 <= top_k <= 20:
            errors.append("rag_top_k must be between 1 and 20")
    except (TypeError, ValueError):
        errors.append("rag_top_k must be an integer")

    max_iter = config.get("max_iterations", 5)
    try:
        if not 1 <= int(max_iter) <= 8:
            errors.append("max_iterations must be between 1 and 8")
    except (TypeError, ValueError):
        errors.append("max_iterations must be an integer")

    for key in ("mcp_servers", "plugin_paths"):
        value = config.get(key, [])
        if not isinstance(value, list):
            errors.append(f"{key} must be a list")

    for key in ("hooks", "tool_permissions"):
        value = config.get(key, {})
        if not isinstance(value, dict):
            errors.append(f"{key} must be an object")

    for index, server in enumerate(config.get("mcp_servers") or []):
        if not isinstance(server, dict):
            errors.append(f"mcp_servers[{index}] must be an object")
            continue
        if not server.get("command"):
            errors.append(f"mcp_servers[{index}].command is required")
        if "tools_allow" in server:
            tools_allow = server.get("tools_allow")
            if not isinstance(tools_allow, list) or not all(
                isinstance(tool, str) and tool.strip() for tool in tools_allow
            ):
                errors.append(
                    f"mcp_servers[{index}].tools_allow must be a list of "
                    "non-empty strings"
                )
        if "allow_all_tools" in server and not isinstance(
            server.get("allow_all_tools"), bool
        ):
            errors.append(
                f"mcp_servers[{index}].allow_all_tools must be a boolean"
            )

    return {"errors": errors, "warnings": warnings}
