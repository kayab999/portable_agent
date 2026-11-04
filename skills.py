#!/usr/bin/env python3
"""skills.py — Agent Skills (SKILL.md) loader for Bonsai Portable.

Implements the agentskills.io directory format without external dependencies:

    skill-name/
    ├── SKILL.md          # YAML frontmatter + Markdown
    ├── scripts/          # optional (never executed by this module)
    ├── references/       # optional
    └── assets/           # optional

Only metadata and Markdown are loaded. Skill scripts are not executed; the
loader merely makes their directory available to the existing read-only tool
sandbox. A malformed skill is skipped and reported, never partially applied.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_SKILL_FILE_BYTES = 512 * 1024
MAX_INDEX_CHARS = 6000
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DEFAULT_PROJECT_DIRS = (
    "skills",
    ".agents/skills",
    ".claude/skills",
    ".opencode/skills",
    ".gemini/skills",
)


class SkillError(ValueError):
    """Raised when a SKILL.md cannot be parsed or validated."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    license: str = ""
    compatibility: str = ""
    metadata: dict = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        return self.path.parent

    def index_line(self) -> str:
        description = " ".join(self.description.split())
        if len(description) > 200:
            description = description[:197] + "..."
        return f"- {self.name}: {description}"


# ─── Minimal YAML subset ─────────────────────────────────────────────────────


def _scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    return raw


def _split_inline_list(raw: str) -> list:
    inner = raw[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    current: list[str] = []
    quote = ""
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    items.append("".join(current).strip())
    return [_scalar(item) for item in items if item]


def _parse_block(block: list[str], *, depth: int = 0) -> object:
    if depth > 4:
        raise SkillError("frontmatter nesting too deep")
    nonblank = [line for line in block if line.strip() and not line.lstrip().startswith("#")]
    if not nonblank:
        return ""
    if nonblank[0].lstrip().startswith("- "):
        items = []
        for line in nonblank:
            stripped = line.lstrip()
            if not stripped.startswith("- "):
                raise SkillError("mixed list/mapping in frontmatter block")
            items.append(_scalar(stripped[2:]))
        return items
    indent = len(nonblank[0]) - len(nonblank[0].lstrip())
    lines = [line[indent:] if line.strip() else "" for line in nonblank]
    return _parse_mapping(lines, depth=depth + 1)


def _parse_mapping(lines: list[str], *, depth: int = 0) -> dict:
    result: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in (" ", "\t"):
            raise SkillError("unexpected indentation in frontmatter")
        key, sep, raw = line.partition(":")
        if not sep or not key.strip():
            raise SkillError(f"invalid frontmatter line: {line!r}")
        key = key.strip()
        raw = raw.strip()
        if raw in ("|", ">"):
            block, i = _gather_block(lines, i + 1)
            joined = (
                "\n".join(part.strip() for part in block).strip()
                if raw == "|"
                else " ".join(part.strip() for part in block).strip()
            )
            result[key] = joined
        elif raw:
            if raw.startswith("{") and raw.endswith("}"):
                raise SkillError("flow mappings are not supported")
            if raw.startswith("[") and raw.endswith("]"):
                result[key] = _split_inline_list(raw)
            else:
                result[key] = _scalar(raw)
            i += 1
        else:
            block, i = _gather_block(lines, i + 1)
            result[key] = _parse_block(block, depth=depth)
    return result


def _gather_block(lines: list[str], start: int) -> tuple[list[str], int]:
    block: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        block.append(line)
        i += 1
    while block and not block[-1].strip():
        block.pop()
    return block, i


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse ``---`` frontmatter using a strict YAML subset."""
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        raise SkillError("unterminated YAML frontmatter")
    meta = _parse_mapping(lines[1:end])
    body = "\n".join(lines[end + 1 :]).strip()
    return meta, body


def _normalize_tools(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise SkillError("allowed-tools must be a list or a comma-separated string")


def skill_from_file(path: Path, *, root: Path | None = None) -> Skill:
    path = Path(path)
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise SkillError(f"cannot resolve {path}: {exc}") from exc
    if root is not None:
        root = Path(root).resolve()
        if not resolved.is_relative_to(root):
            raise SkillError(f"skill escapes its root: {path}")
    try:
        if resolved.stat().st_size > MAX_SKILL_FILE_BYTES:
            raise SkillError(f"SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes")
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"cannot read {path}: {exc}") from exc

    meta, body = parse_frontmatter(text)
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not _NAME_RE.match(name) or len(name) > 64:
        raise SkillError("name must be lowercase kebab-case, max 64 characters")
    if not isinstance(description, str) or not description.strip():
        raise SkillError("description is required")
    if len(description) > 1024:
        raise SkillError("description exceeds 1024 characters")
    license_value = meta.get("license") or ""
    compatibility = meta.get("compatibility") or ""
    if not isinstance(license_value, str):
        raise SkillError("license must be a string")
    if not isinstance(compatibility, str):
        raise SkillError("compatibility must be a string")
    if len(compatibility) > 500:
        raise SkillError("compatibility exceeds 500 characters")
    metadata = meta.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise SkillError("metadata must be a mapping")

    return Skill(
        name=name,
        description=description.strip(),
        body=body,
        path=resolved,
        license=license_value.strip(),
        compatibility=compatibility.strip(),
        metadata=metadata,
        allowed_tools=_normalize_tools(meta.get("allowed-tools")),
    )


# ─── Discovery ───────────────────────────────────────────────────────────────


def discovery_roots(
    project_root: Path,
    home: Path | None = None,
    extra_roots: list[Path] | tuple[Path, ...] | None = None,
) -> list[Path]:
    project_root = Path(project_root).resolve()
    home = Path(home).expanduser() if home is not None else Path.home()
    roots = [project_root / rel for rel in _DEFAULT_PROJECT_DIRS]
    for extra in extra_roots or ():
        try:
            candidate = Path(extra).expanduser().resolve()
        except OSError:
            continue
        if candidate.is_dir():
            roots.append(candidate)
    roots.append(home / ".agents" / "skills")
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


class SkillIndex:
    """Loaded skills plus collision diagnostics."""

    def __init__(
        self,
        skills: dict[str, Skill] | None = None,
        conflicts: list[dict] | None = None,
    ):
        self.skills = skills or {}
        self.conflicts = conflicts or []

    @classmethod
    def load(
        cls,
        project_root: Path,
        home: Path | None = None,
        extra_roots: list[Path] | tuple[Path, ...] | None = None,
    ) -> "SkillIndex":
        skills: dict[str, Skill] = {}
        conflicts: list[dict] = []
        for root in discovery_roots(project_root, home, extra_roots):
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir())
            except OSError:
                continue
            for child in entries:
                skill_file = child / "SKILL.md"
                if not skill_file.is_file():
                    continue
                try:
                    skill = skill_from_file(skill_file, root=root)
                except SkillError as exc:
                    conflicts.append(
                        {"name": child.name, "path": str(skill_file), "error": str(exc)}
                    )
                    continue
                if skill.name in skills:
                    conflicts.append(
                        {
                            "name": skill.name,
                            "path": str(skill_file),
                            "error": f"duplicate name (kept {skills[skill.name].path})",
                        }
                    )
                    continue
                skills[skill.name] = skill
        return cls(skills, conflicts)

    def __len__(self) -> int:
        return len(self.skills)

    def get(self, name: str) -> Skill | None:
        return self.skills.get((name or "").strip())

    def index_text(self, max_chars: int = MAX_INDEX_CHARS) -> str:
        """Compact name+description index for the system prompt."""
        lines: list[str] = []
        used = 0
        for name in sorted(self.skills):
            line = self.skills[name].index_line()
            if used + len(line) + 1 > max_chars:
                lines.append("- ... additional skills omitted ...")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def search(self, query: str, top_k: int = 5) -> list[Skill]:
        """Rank skills by naive token overlap (stdlib BM25-lite)."""
        tokens = set(re.findall(r"[a-z0-9]+", (query or "").lower()))
        if not tokens:
            return []
        scored: list[tuple[float, str]] = []
        for name, skill in self.skills.items():
            haystack = set(
                re.findall(
                    r"[a-z0-9]+",
                    f"{name} {skill.description} {skill.body}".lower(),
                )
            )
            overlap = len(tokens & haystack)
            if overlap:
                scored.append((overlap / (len(haystack) ** 0.5 or 1), name))
        scored.sort(reverse=True)
        return [self.skills[name] for _, name in scored[:top_k]]


_CACHE: dict[tuple[str, str, tuple[str, ...]], SkillIndex] = {}
_EXTRA_ROOTS: tuple[Path, ...] = ()


def configure_skill_roots(paths: list[Path] | tuple[Path, ...] | None) -> None:
    """Register plugin-provided skill roots for all future lookups."""
    global _EXTRA_ROOTS
    roots: list[Path] = []
    for path in paths or ():
        try:
            candidate = Path(path).expanduser().resolve()
        except OSError:
            continue
        if candidate.is_dir():
            roots.append(candidate)
    _EXTRA_ROOTS = tuple(dict.fromkeys(roots))
    _CACHE.clear()


def get_skill_index(
    project_root: Path | None = None,
    home: Path | None = None,
    *,
    refresh: bool = False,
    extra_roots: list[Path] | tuple[Path, ...] | None = None,
) -> SkillIndex:
    project_root = Path(project_root or Path(__file__).parent).resolve()
    home_path = Path(home).expanduser() if home is not None else Path.home()
    roots = tuple(extra_roots) if extra_roots is not None else _EXTRA_ROOTS
    key = (str(project_root), str(home_path), tuple(str(path) for path in roots))
    if refresh or key not in _CACHE:
        _CACHE[key] = SkillIndex.load(project_root, home_path, roots)
    return _CACHE[key]


def skills_enabled() -> bool:
    return os.environ.get("BONSAI_SKILLS", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def load_skill_body(name: str, project_root: Path | None = None) -> Skill | None:
    if not skills_enabled():
        return None
    return get_skill_index(project_root).get(name)
