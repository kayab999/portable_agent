#!/usr/bin/env python3
"""subagents.py — Markdown subagent definitions and restricted execution.

Agent files are plain Markdown with YAML frontmatter:

    ---
    name: reviewer
    description: Reviews a change for regressions
    tools: [read_file, list_dir, grep]
    model: bonsai-8b
    ---
    You are a careful reviewer...

Tool declarations are upper bounds: they are intersected with the global
allowlist and the power-tool gate, never added to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from skills import SkillError, parse_frontmatter

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_AGENT_BYTES = 256 * 1024


class SubagentError(ValueError):
    """Raised when an agent definition cannot be parsed."""


@dataclass(frozen=True)
class Subagent:
    name: str
    description: str
    body: str
    path: Path
    tools: tuple[str, ...] = ()
    model: str = ""

    @property
    def directory(self) -> Path:
        return self.path.parent


def _normalize_tools(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    raise SubagentError("tools must be a list or comma-separated string")


def subagent_from_file(path: Path, *, root: Path | None = None) -> Subagent:
    path = Path(path)
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise SubagentError(f"cannot resolve {path}: {exc}") from exc
    if root is not None and not resolved.is_relative_to(Path(root).resolve()):
        raise SubagentError(f"agent escapes its root: {path}")
    try:
        if resolved.stat().st_size > _MAX_AGENT_BYTES:
            raise SubagentError(f"agent file exceeds {_MAX_AGENT_BYTES} bytes")
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SubagentError(f"cannot read {path}: {exc}") from exc

    meta, body = parse_frontmatter(text)
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not _NAME_RE.match(name) or len(name) > 64:
        raise SubagentError("name must be lowercase kebab-case, max 64 characters")
    if not isinstance(description, str) or not description.strip():
        raise SubagentError("description is required")
    if len(description) > 1024:
        raise SubagentError("description exceeds 1024 characters")
    if not body.strip():
        raise SubagentError("agent body is empty")
    model = meta.get("model") or ""
    if not isinstance(model, str):
        raise SubagentError("model must be a string")
    return Subagent(
        name=name,
        description=description.strip(),
        body=body,
        path=resolved,
        tools=_normalize_tools(meta.get("tools")),
        model=model.strip(),
    )


def agent_dirs(project_root: Path, extra_roots=None) -> list[Path]:
    project_root = Path(project_root).resolve()
    candidates = [
        project_root / "agents",
        project_root / ".agents" / "agents",
        project_root / ".claude" / "agents",
    ]
    for extra in extra_roots or ():
        try:
            candidate = Path(extra).expanduser().resolve()
        except OSError:
            continue
        if candidate.is_dir():
            candidates.append(candidate)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


class SubagentIndex:
    def __init__(self, agents: dict[str, Subagent] | None = None, errors=None):
        self.agents = agents or {}
        self.errors = errors or []

    @classmethod
    def load(cls, project_root: Path, extra_roots=None) -> "SubagentIndex":
        agents: dict[str, Subagent] = {}
        errors: list[dict] = []
        for directory in agent_dirs(project_root, extra_roots):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                try:
                    agent = subagent_from_file(path, root=directory)
                except (SubagentError, SkillError) as exc:
                    errors.append({"path": str(path), "error": str(exc)})
                    continue
                if agent.name in agents:
                    errors.append(
                        {
                            "path": str(path),
                            "error": f"duplicate name (kept {agents[agent.name].path})",
                        }
                    )
                    continue
                agents[agent.name] = agent
        return cls(agents, errors)

    def __len__(self) -> int:
        return len(self.agents)

    def get(self, name: str) -> Subagent | None:
        return self.agents.get((name or "").strip())

    def list_public(self) -> list[dict]:
        return [
            {
                "name": agent.name,
                "description": agent.description,
                "tools": list(agent.tools),
                "model": agent.model,
            }
            for agent in sorted(self.agents.values(), key=lambda item: item.name)
        ]


def allowed_tools_for(agent: Subagent) -> frozenset[str]:
    """Intersect the agent's declaration with the global allowlist."""
    from tools import ALLOWED_TOOLS, POWER_TOOLS

    if agent.tools:
        allowed = {name for name in agent.tools if name in ALLOWED_TOOLS}
    else:
        allowed = set(ALLOWED_TOOLS) - set(POWER_TOOLS)
    allowed.update({"diagnose", "load_skill"})
    return frozenset(name for name in allowed if name in ALLOWED_TOOLS)


def run_subagent(
    agent: Subagent,
    prompt: str,
    model_client,
    *,
    model: str = "bonsai",
    toolbox=None,
    max_iterations: int = 3,
    hooks=None,
    permissions=None,
    persona_tools: set[str] | frozenset[str] | None = None,
) -> dict:
    """Run one subagent turn with a restricted toolset.

    The effective toolset is the intersection of three boundaries: the
    subagent declaration, the global allowlist/power gate, and (when provided)
    the active persona's allowlist. A subagent can never widen the current
    persona's permissions.
    """
    from agent_loop import TOOL_PROTOCOL_HINT, interactive_react
    from tools import get_toolbox

    if not prompt or not prompt.strip():
        return {"ok": False, "error": "prompt_required"}
    toolbox = toolbox or get_toolbox()
    effective_tools = allowed_tools_for(agent)
    if persona_tools is not None:
        effective_tools = frozenset(effective_tools & set(persona_tools))
    system = f"{agent.body}\n\n{TOOL_PROTOCOL_HINT}"
    messages = [{"role": "system", "content": system}]
    answer, _, results = interactive_react(
        prompt,
        model_client,
        messages,
        model=model or agent.model or "bonsai",
        toolbox=toolbox,
        persona_tools=effective_tools,
        hooks=hooks,
        permissions=permissions,
        max_iterations=max(1, min(int(max_iterations), 5)),
    )
    return {
        "ok": True,
        "subagent": agent.name,
        "answer": answer,
        "tool_results": results,
    }
