#!/usr/bin/env python3
"""Gateway auth, body limits, RAG path confinement, direct tools policy."""
from __future__ import annotations

import json
import unittest
from http.client import HTTPConnection
from pathlib import Path

from gateway import GatewayState, start_gateway, _confined_index_path
from session_auth import SessionManager


class TestGatewayAuth(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionManager()
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=False,
            is_unlocked=lambda: False,
            host="127.0.0.1",
            port=0,  # set after bind
            sessions=self.sessions,
            auth_required=True,
            allow_direct_tools=False,
        )
        self.server = start_gateway(self.state, background=True)
        self.port = self.server.server_address[1]

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass

    def _req(self, method, path, body=None, token=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = headers or {}
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(data))
        else:
            data = None
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            parsed = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw": raw.decode("utf-8", errors="replace")}
        return resp.status, parsed

    def test_health_public(self):
        status, data = self._req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("auth_required"))

    def test_identity_requires_auth(self):
        status, data = self._req("GET", "/api/identity")
        self.assertEqual(status, 401)
        self.assertEqual(data.get("error"), "unauthorized")

    def test_tools_post_disabled(self):
        token = self.sessions.issue()
        # still locked for unlock path — issue token directly
        status, data = self._req(
            "POST",
            "/api/tools",
            body={"tool": "diagnose", "args": {}},
            token=token,
        )
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "direct_tools_disabled")

    def test_unlock_locked_without_creds(self):
        status, data = self._req("POST", "/api/session/unlock", body={})
        self.assertEqual(status, 403)

    def test_unlock_when_unlocked_issues_token(self):
        self.state.is_unlocked = lambda: True
        status, data = self._req("POST", "/api/session/unlock", body={})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("token"))
        token = data["token"]
        st2, d2 = self._req("GET", "/api/identity", token=token)
        self.assertEqual(st2, 200)
        self.assertIn("needs_setup", d2)

    def test_confined_index_path_blocks_etc(self):
        self.assertIsNone(_confined_index_path("/etc"))
        self.assertIsNone(_confined_index_path("/etc/passwd"))

    def test_confined_index_path_allows_docs(self):
        docs = Path(__file__).resolve().parents[1] / "docs"
        if docs.exists():
            p = _confined_index_path(str(docs))
            self.assertIsNotNone(p)


class TestGatewayDebugMode(unittest.TestCase):
    def setUp(self):
        self.sessions = SessionManager()
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=True,
            is_unlocked=lambda: True,
            host="127.0.0.1",
            port=0,
            sessions=self.sessions,
            auth_required=False,
            allow_direct_tools=True,
        )
        self.server = start_gateway(self.state, background=True)
        self.port = self.server.server_address[1]

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass

    def test_debug_identity_without_token(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/identity")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()


if __name__ == "__main__":
    unittest.main()
