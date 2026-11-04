#!/usr/bin/env python3
"""agents_md.py — Load AGENTS.md project instructions.

AGENTS.md is a plain-Markdown convention (no formal schema). Files are read
from the repository root down to the current directory; later files override
earlier ones. Content is *project context*, never security policy, and is
always capped so a hostile repository cannot exhaust the model context.
"""

from __future__ import annotations

from pathlib import Path

MAX_AGENTS_BYTES = 32 * 1024
_FILENAME = "AGENTS.md"


def _chain(root: Path, cwd: Path) -> list[Path]:
    """Return directories from *root* down to *cwd* (inclusive)."""
    root = root.resolve()
    cwd = cwd.resolve()
    if not cwd.is_relative_to(root):
        cwd = root
    chain: list[Path] = []
    cur = cwd
    while True:
        chain.append(cur)
        if cur == root or cur.parent == cur:
            break
        cur = cur.parent
    chain.reverse()
    return chain


def discover_agents_files(root: Path, cwd: Path | None = None) -> list[Path]:
    """Find readable AGENTS.md files between *root* and *cwd*."""
    root = Path(root).resolve()
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    found: list[Path] = []
    for directory in _chain(root, cwd):
        candidate = directory / _FILENAME
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        # Never follow a symlink out of the project root.
        if not resolved.is_relative_to(root):
            continue
        found.append(candidate)
    return found


def load_agents_md(
    root: Path,
    cwd: Path | None = None,
    *,
    max_bytes: int = MAX_AGENTS_BYTES,
) -> str:
    """Concatenate AGENTS.md files, root first, capped at *max_bytes*."""
    root = Path(root).resolve()
    chunks: list[str] = []
    used = 0
    for path in discover_agents_files(root, cwd):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text.strip()
        if not text:
            continue
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:
            label = path.name
        header = f"### {label}"
        remaining = max_bytes - used - len(header) - 1
        if remaining <= 0:
            break
        body = text[:remaining]
        chunks.append(f"{header}\n{body}")
        used += len(header) + len(body) + 1
        if len(body) < len(text):
            chunks.append("_(truncated: AGENTS.md budget reached)_")
            break
    return "\n\n".join(chunks)
