#!/usr/bin/env python3
"""Origin and Host validation for the local gateway."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from gateway import GatewayState, start_gateway
from session_auth import SessionManager, Vault


class TestOriginHost(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=False,
            is_unlocked=lambda: False,
            vault=Vault(self.tmp),
            host="127.0.0.1",
            port=0,
            sessions=SessionManager(),
            auth_required=True,
        )
        self.server = start_gateway(self.state, background=True)
        self.port = self.server.server_address[1]
        self.allowed = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        hdrs_out = dict(resp.getheaders())
        conn.close()
        try:
            parsed = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return resp.status, parsed, hdrs_out

    def test_foreign_origin_cannot_unlock(self):
        status, data, headers = self.req(
            "POST",
            "/api/session/unlock",
            {},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "forbidden_origin")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_allowed_origin_is_reflected_not_wildcard(self):
        status, _, headers = self.req(
            "POST",
            "/api/session/unlock",
            {},
            headers={"Origin": self.allowed},
        )
        self.assertEqual(status, 403)  # no vault, but origin is accepted
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.allowed)
        self.assertNotEqual(headers.get("Access-Control-Allow-Origin"), "*")

    def test_foreign_origin_preflight_rejected(self):
        status, data, headers = self.req(
            "OPTIONS",
            "/api/session/unlock",
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "forbidden_origin")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_allowed_origin_preflight(self):
        status, _, headers = self.req(
            "OPTIONS",
            "/api/session/unlock",
            headers={"Origin": self.allowed},
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.allowed)

    def test_ipv6_origin_not_allowed(self):
        self.assertNotIn(f"http://[::1]:{self.port}", self.state.allowed_origins)
        self.assertNotIn(f"[::1]:{self.port}", self.state.allowed_hosts)

    def test_bad_host_rejected(self):
        status, data, _ = self.req(
            "GET", "/api/health", headers={"Host": "evil.example:1234"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "invalid_host")

    def test_no_origin_allowed_for_non_browser_clients(self):
        status, data, _ = self.req("POST", "/api/session/unlock", {})
        self.assertEqual(status, 403)
        self.assertNotEqual(data.get("error"), "forbidden_origin")

    def test_no_wildcard_header_anywhere(self):
        for origin in (None, "http://evil.example", self.allowed):
            headers = {"Origin": origin} if origin else {}
            _, _, out = self.req("GET", "/api/health", headers=headers)
            self.assertNotEqual(out.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
