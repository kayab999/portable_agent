#!/usr/bin/env python3
"""
Red-team runner — executes real adversarial unit tests.

Claims that require OS-level isolation (seccomp, skill signing) are NOT
implemented in this stdlib runtime; this suite covers path jail, the opt-in
non-isolated python_exec pre-filter, command whitelist, and gateway auth only.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromName("tests.test_sandbox_escape"))
    suite.addTests(loader.loadTestsFromName("tests.test_gateway_auth"))
    suite.addTests(loader.loadTestsFromName("tests.test_persona_tools"))
    suite.addTests(loader.loadTestsFromName("tests.test_tools_sandbox"))
    suite.addTests(loader.loadTestsFromName("tests.test_ssrf"))
    suite.addTests(loader.loadTestsFromName("tests.test_crypto"))
    suite.addTests(
        loader.loadTestsFromName("audit_suite.red_team.test_tools_security")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
