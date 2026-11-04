#!/usr/bin/env python3
"""Broken RAG/persistence features are contained, not advertised."""
from __future__ import annotations

import inspect
import json
import shutil
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

import runtime_core
from agent_loop import agent_step
from gateway import GatewayState, start_gateway
from session_auth import SessionManager


class TestContainment(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = SessionManager()
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=False,
            is_unlocked=lambda: False,
            rag_enabled=False,
            persistence_enabled=False,
            host="127.0.0.1",
            port=0,
            sessions=self.sessions,
            auth_required=True,
        )
        self.server = start_gateway(self.state, background=True)
        self.port = self.server.server_address[1]
        self.token = self.sessions.issue()

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            parsed = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return resp.status, parsed

    def test_rag_post_reports_unavailable(self):
        status, data = self.req("POST", "/api/rag", {"action": "index"})
        self.assertEqual(status, 503)
        self.assertEqual(data.get("error"), "feature_unavailable")
        self.assertEqual(data.get("feature"), "rag")

    def test_drain_reports_unavailable(self):
        status, data = self.req("POST", "/api/experience/drain", {})
        self.assertEqual(status, 503)
        self.assertEqual(data.get("feature"), "persistence")

    def test_health_reports_disabled_features(self):
        status, data = self.req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("features"), {"rag": "disabled", "persistence": "disabled"})
        self.assertFalse(data.get("rag_enabled"))
        self.assertFalse(data.get("persistence_enabled"))

    def test_agent_step_persistence_is_opt_in(self):
        params = inspect.signature(agent_step).parameters
        self.assertFalse(params["persist"].default)
        self.assertFalse(params["drain_queue"].default)
        self.assertFalse(params["use_memory"].default)

    def test_config_defaults_disable_experimental_features(self):
        cfg = runtime_core.load_config(self.tmp / "missing.json")
        self.assertFalse(cfg["rag_enabled"])
        self.assertFalse(cfg["experience_persistence"])
        self.assertFalse(cfg["yubikey_auto"])


if __name__ == "__main__":
    unittest.main()
