#!/usr/bin/env python3
"""secret_scan.py — dependency-free secret scanning for tracked source trees.

This is a high-confidence scanner, not an entropy oracle: it looks for
well-known credential prefixes and private-key headers. Findings never print
the full secret; only a short masked prefix is shown.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    "data",
    "dist",
    "htmlcov",
    "archive",
    "models",
    "workspace",
    "node_modules",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".gguf",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".pyc",
    ".sqlite",
    ".db",
}
PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    ),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
)
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|dummy|fake|redacted|sample|xxxx+|<[^>]+>|\.\.\.)"
)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "..."
    return f"{value[:4]}...{value[-2:]}"


def scan_text(text: str, source: str = "<memory>") -> list[dict]:
    """Return findings for one text blob, keeping only a masked match."""
    findings: list[dict] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if PLACEHOLDER.search(line):
            continue
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    {
                        "source": source,
                        "line": line_number,
                        "kind": kind,
                        "match": _mask(match.group(0)),
                    }
                )
    return findings


def iter_files(root: Path):
    """Yield ``(path, text)`` for text files, skipping binaries and state dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".git")
        ]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:8192]:
                continue
            yield path, data.decode("utf-8", errors="replace")


def scan_paths(root: Path) -> list[dict]:
    findings: list[dict] = []
    for path, text in iter_files(root):
        findings.extend(scan_text(text, str(path)))
    return findings


def scan_history(root: Path) -> tuple[list[dict], str | None]:
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "-p",
                "--all",
                "--no-color",
                "--no-merges",
                "-U0",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"git history scan unavailable: {exc}"
    if proc.returncode != 0:
        return [], f"git history scan unavailable: {proc.stderr.strip()[:200]}"
    return scan_text(proc.stdout, "git-history"), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="tree to scan")
    parser.add_argument(
        "--history", action="store_true", help="also scan `git log -p` history"
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    findings = scan_paths(root)
    if args.history:
        history_findings, note = scan_history(root)
        findings.extend(history_findings)
        if note:
            print(f"warning: {note}", file=sys.stderr)
    for finding in findings:
        print(
            f"{finding['source']}:{finding['line']}: "
            f"{finding['kind']} {finding['match']}"
        )
    if findings:
        print(f"secret scan FAILED: {len(findings)} finding(s)")
        return 1
    print(f"secret scan OK: no high-confidence secrets in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
