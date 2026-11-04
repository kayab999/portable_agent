#!/usr/bin/env python3
"""Static web UI contract: assets are served, CSP has no inline scripts."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from gateway import GatewayState, start_gateway


class TestWebStatic(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=True,
            is_unlocked=lambda: True,
            host="127.0.0.1",
            port=0,
            auth_required=False,
            db_path=self.tmp / "memory.db",
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

    def _get(self, path):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        conn.close()
        return response.status, headers, body

    def test_index_references_external_assets(self):
        status, _, body = self._get("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('href="/styles.css"', html)
        self.assertIn('src="/app.js"', html)
        self.assertNotIn("<style>", html)
        self.assertNotIn("<script>", html)
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", html))

    def test_assets_are_served(self):
        status, headers, body = self._get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("addEventListener", body.decode("utf-8"))
        status, headers, body = self._get("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])

    def test_csp_forbids_inline_script(self):
        status, headers, _ = self._get("/")
        self.assertEqual(status, 200)
        csp = headers["Content-Security-Policy"]
        script_policy = [part for part in csp.split(";") if "script-src" in part][0]
        self.assertNotIn("unsafe-inline", script_policy)
        self.assertNotIn("unsafe-eval", script_policy)

    def test_no_inline_handlers_in_app_js(self):
        _, _, body = self._get("/app.js")
        js = body.decode("utf-8")
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", js))


if __name__ == "__main__":
    unittest.main()
