"""SBOM generation tests (stdlib only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_sbom  # noqa: E402


class TestSbom(unittest.TestCase):
    def test_sbom_is_cyclonedx_and_deterministic(self):
        first = generate_sbom.build_sbom(ROOT)
        second = generate_sbom.build_sbom(ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first["bomFormat"], "CycloneDX")
        self.assertEqual(first["specVersion"], "1.5")
        self.assertEqual(first["metadata"]["component"]["name"], "bonsai-portable")

    def test_runtime_has_no_third_party_components(self):
        components = generate_sbom.build_sbom(ROOT)["components"]
        self.assertTrue(all(item.get("scope") == "build" for item in components))
        names = {item["name"] for item in components}
        self.assertIn("coverage", names)
        self.assertIn("ruff", names)


if __name__ == "__main__":
    unittest.main()
