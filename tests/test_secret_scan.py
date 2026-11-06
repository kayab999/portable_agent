"""Secret scanner regression tests (stdlib only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import secret_scan  # noqa: E402


class TestSecretScan(unittest.TestCase):
    def test_detects_and_masks_github_token(self):
        token = "ghp_" + "A" * 36
        findings = secret_scan.scan_text(f'token = "{token}"')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "github-token")
        self.assertNotIn(token, findings[0]["match"])
        self.assertTrue(findings[0]["match"].startswith("ghp_"))

    def test_placeholder_values_are_ignored(self):
        findings = secret_scan.scan_text('example = "ghp_' + "A" * 36 + '"')
        self.assertEqual(findings, [])

    def test_private_key_header_is_detected(self):
        header = "-----BEGIN " + "RSA PRIVATE KEY-----"
        findings = secret_scan.scan_text(header)
        self.assertEqual(findings[0]["kind"], "private-key")

    def test_repository_tree_is_clean(self):
        self.assertEqual(secret_scan.scan_paths(ROOT), [])


if __name__ == "__main__":
    unittest.main()
