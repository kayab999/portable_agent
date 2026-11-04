#!/usr/bin/env python3
"""Doctor diagnostics: required checks and JSON contract."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from doctor import run_checks


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for relative in ("launcher.py", "gateway.py", "web/index.html", "web/app.js"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_required_checks_pass(self):
        report = run_checks(self.root)
        self.assertTrue(report["ok"])
        names = {item["name"] for item in report["checks"]}
        self.assertIn("python", names)
        self.assertIn("data-writable", names)
        self.assertIn("rag-db", names)

    def test_missing_required_file_fails(self):
        (self.root / "gateway.py").unlink()
        report = run_checks(self.root)
        self.assertFalse(report["ok"])
        missing = [
            item for item in report["checks"] if item["name"] == "file:gateway.py"
        ][0]
        self.assertFalse(missing["ok"])

    def test_port_check_reports_availability(self):
        sock_report = run_checks(self.root, port=0)
        self.assertTrue(
            any(item["name"] == "port" for item in sock_report["checks"])
        )


if __name__ == "__main__":
    unittest.main()
