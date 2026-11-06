"""Import contracts enforced with the standard library."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_MODULES = {"agentportable", "linter", "execution_loop"}
STORAGE_MODULES = {"local_memory", "experience_store", "maintenance", "audit_worker"}
TRANSPORT_MODULES = {"gateway", "acp_server", "launcher"}
PRODUCTION_FILES = [
    path
    for path in ROOT.glob("*.py")
    if path.name not in {
        "agentportable.py",
        "linter.py",
        "execution_loop.py",
        "knowledge_db.py",
    }
]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


class TestImportContracts(unittest.TestCase):
    def test_production_does_not_import_legacy_modules(self):
        violations: list[str] = []
        for path in PRODUCTION_FILES:
            imported = _imported_modules(path)
            bad = imported & LEGACY_MODULES
            if bad:
                violations.append(f"{path.name}: {', '.join(sorted(bad))}")
        self.assertEqual(violations, [])

    def test_storage_does_not_import_transports(self):
        violations: list[str] = []
        for name in sorted(STORAGE_MODULES):
            path = ROOT / f"{name}.py"
            bad = _imported_modules(path) & TRANSPORT_MODULES
            if bad:
                violations.append(f"{name}.py: {', '.join(sorted(bad))}")
        self.assertEqual(violations, [])

    def test_acp_does_not_import_http_gateway(self):
        self.assertNotIn("gateway", _imported_modules(ROOT / "acp_server.py"))

    def test_legacy_modules_are_guarded(self):
        for name in ("agentportable.py", "linter.py", "execution_loop.py"):
            path = ROOT / name
            source = path.read_text(encoding="utf-8")
            self.assertIn("BONSAI_LEGACY_PATHS", source)


if __name__ == "__main__":
    unittest.main()
