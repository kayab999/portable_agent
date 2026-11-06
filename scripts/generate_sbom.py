#!/usr/bin/env python3
"""generate_sbom.py — deterministic CycloneDX 1.5 SBOM without dependencies.

The runtime has no third-party dependencies. Development tools declared in
requirements-dev.txt are listed as build-only components; exact versions are
recorded only when the requirement is pinned with ``==``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)\s*(?:==|>=|<=|~=|!=|>|<)?\s*([^,;\s]*)?"
)


def project_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def parse_requirements(path: Path) -> list[dict]:
    components: list[dict] = []
    if not path.exists():
        return components
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = REQUIREMENT.match(line)
        if not match:
            continue
        name, version = match.group(1), (match.group(2) or "").strip()
        component: dict = {
            "type": "library",
            "name": name,
            "scope": "build",
        }
        if version and "==" in line:
            component["version"] = version
        components.append(component)
    return sorted(components, key=lambda item: item["name"].lower())


def build_sbom(root: Path = ROOT) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "bonsai-portable",
                "version": project_version(root),
            }
        },
        "components": parse_requirements(root / "requirements-dev.txt"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(ROOT / "dist" / "sbom.json"),
        help="output path (default: dist/sbom.json)",
    )
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
