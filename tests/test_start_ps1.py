#!/usr/bin/env python3
"""Windows launcher must be valid PowerShell 5.1 syntax."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "start.ps1"


class TestStartPs1(unittest.TestCase):
    def test_no_invalid_null_conditional_operator(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("?.Source", text)
        self.assertNotIn("127.0.0.1:8081", text)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_powershell_parse(self):
        command = (
            "$ErrorActionPreference='Stop'; "
            "$null = [scriptblock]::Create((Get-Content -Raw start.ps1)); "
            "'PARSE_OK'"
        )
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PARSE_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
