#!/usr/bin/env python3
"""Config validation: fail closed with actionable messages."""

import unittest

from config_schema import validate_config


class TestConfigSchema(unittest.TestCase):
    def test_defaults_are_valid(self):
        report = validate_config({})
        self.assertEqual(report["errors"], [])

    def test_bad_port(self):
        self.assertTrue(validate_config({"port": 70000})["errors"])

    def test_bad_rag_limits(self):
        self.assertTrue(validate_config({"rag_top_k": 99})["errors"])
        self.assertTrue(validate_config({"rag_max_chars": -1})["errors"])

    def test_boolean_types(self):
        self.assertTrue(validate_config({"rag_enabled": "yes"})["errors"])

    def test_unknown_key_warns(self):
        report = validate_config({"totally_new": 1})
        self.assertTrue(report["warnings"])
        self.assertEqual(report["errors"], [])

    def test_mcp_server_requires_command(self):
        report = validate_config({"mcp_servers": [{"name": "x"}]})
        self.assertTrue(report["errors"])

    def test_non_loopback_warns(self):
        report = validate_config({"host": "0.0.0.0"})
        self.assertTrue(report["warnings"])


if __name__ == "__main__":
    unittest.main()
