#!/usr/bin/env python3
"""/v1 requires a bearer token in production; health stays public."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from gateway import GatewayState, start_gateway
from session_auth import SessionManager


class TestV1Auth(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sessions = SessionManager()
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=False,
            is_unlocked=lambda: False,
            host="127.0.0.1",
            port=0,
            sessions=self.sessions,
            auth_required=True,
        )
        self.server = start_gateway(self.state, background=True)
        self.port = self.server.server_address[1]

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None, token=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(data))
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            parsed = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return resp.status, parsed

    def test_v1_requires_token(self):
        status, data = self.req(
            "POST",
            "/v1/chat/completions",
            {"model": "x", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(status, 401)
        self.assertEqual(data.get("error"), "unauthorized")
        status, _ = self.req("GET", "/v1/models")
        self.assertEqual(status, 401)

    def test_v1_with_token_passes_auth(self):
        token = self.sessions.issue()
        status, _ = self.req("GET", "/v1/models", token=token)
        self.assertNotEqual(status, 401)  # dead LLM -> 502

    def test_health_is_public(self):
        status, _ = self.req("GET", "/health")
        self.assertNotEqual(status, 401)  # proxied; dead LLM -> 502
        status, data = self.req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))


if __name__ == "__main__":
    unittest.main()
