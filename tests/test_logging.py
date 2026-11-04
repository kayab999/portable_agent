#!/usr/bin/env python3
"""Structured logging redacts secrets before they reach disk."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from bonsai_logging import configure, redact


class TestLogging(unittest.TestCase):
    def test_redact_tokens_and_keys(self):
        text = "Authorization: Bearer abc123 api_key=deadbeef sk-supersecretvalue"
        cleaned = redact(text)
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("deadbeef", cleaned)
        self.assertNotIn("supersecretvalue", cleaned)
        self.assertIn("***", cleaned)

    def test_json_log_file(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            target = tmp / "bonsai.log"
            logger = configure(log_file=target)
            logger.info("token=%s", "sk-abcdefghijkl")
            for handler in logger.handlers:
                handler.flush()
            payload = json.loads(target.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["level"], "INFO")
            self.assertNotIn("abcdefghijkl", payload["message"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
