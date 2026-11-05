import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock, patch

import tools
from tools import Toolbox, _PinnedHTTPSConnection, _ssrf_block_reason


class _FakeResponse:
    def __init__(self, status=200, reason="OK", headers=None, body=b"ok"):
        self.status = status
        self.reason = reason
        self._headers = headers or {"Content-Type": "text/plain"}
        self._body = body

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def getheaders(self):
        return list(self._headers.items())


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class TestSSRF(unittest.TestCase):
    def setUp(self):
        self._old_power = os.environ.get("BONSAI_POWER_TOOLS")
        os.environ["BONSAI_POWER_TOOLS"] = "1"
        self.toolbox = Toolbox()

    def tearDown(self):
        if self._old_power is None:
            os.environ.pop("BONSAI_POWER_TOOLS", None)
        else:
            os.environ["BONSAI_POWER_TOOLS"] = self._old_power

    def _server(self):
        server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_literal_addresses_blocked(self):
        blocked = [
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://[::]/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertIsNotNone(_ssrf_block_reason(url))

    def test_localhost_and_trailing_dot_blocked(self):
        self.assertIsNotNone(_ssrf_block_reason("http://localhost/x"))
        self.assertIsNotNone(_ssrf_block_reason("http://localhost./x"))

    def test_public_literals_allowed(self):
        self.assertIsNone(_ssrf_block_reason("http://8.8.8.8/"))
        self.assertIsNone(_ssrf_block_reason("http://[2606:4700:4700::1111]/"))

    def test_cgnat_and_non_global_addresses_blocked(self):
        blocked = [
            "http://100.64.0.1/",
            "http://100.100.100.200/",
            "http://100.127.255.254/",
            "http://198.18.0.1/",
            "http://192.0.2.1/",
            "http://[2001:db8::1]/",
            "http://[::ffff:100.64.0.1]/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertIsNotNone(_ssrf_block_reason(url))

    def test_invalid_port_and_scheme_blocked(self):
        self.assertIsNotNone(_ssrf_block_reason("http://example.com:99999/"))
        self.assertIsNotNone(_ssrf_block_reason("file:///etc/passwd"))

    def test_dns_failure_fails_closed(self):
        with patch("tools.socket.getaddrinfo", side_effect=socket.gaierror("nx")):
            self.assertIsNotNone(_ssrf_block_reason("http://does-not-exist.invalid/"))

    def test_mixed_public_private_dns_answers_blocked(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]
        with patch("tools.socket.getaddrinfo", return_value=answers):
            self.assertIsNotNone(_ssrf_block_reason("http://example.com/"))

    def test_public_ok(self):
        # May still block if DNS maps oddly; example.com should resolve public
        reason = _ssrf_block_reason("https://example.com/")
        self.assertTrue(
            reason is None
            or "resolver" in reason.lower()
            or "resolv" in (reason or "").lower()
            or "No se pudo" in (reason or "")
        )

    def test_direct_prohibited_target_receives_zero_requests(self):
        server = self._server()
        result = self.toolbox.fetch_url(f"http://127.0.0.1:{server.server_port}/")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "url_bloqueada")
        self.assertEqual(server.requests, [])

    def test_redirect_to_private_target_receives_zero_requests(self):
        server = self._server()
        target = f"http://127.0.0.1:{server.server_port}/private"
        real_fetch_once = tools._fetch_once

        def fake_fetch(url, timeout):
            if url == "http://public.example/start":
                return {
                    "status": 302,
                    "reason": "Found",
                    "headers": {"location": target},
                    "body": b"",
                }
            return real_fetch_once(url, timeout)

        with patch("tools._fetch_once", side_effect=fake_fetch):
            result = self.toolbox.fetch_url("http://public.example/start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "url_bloqueada")
        self.assertEqual(server.requests, [])

    def test_connection_uses_validated_address_and_original_host(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]
        response = _FakeResponse(body=b"hello")
        conn = Mock()
        conn.getresponse.return_value = response
        with (
            patch("tools.socket.getaddrinfo", return_value=answers) as dns,
            patch("tools.http.client.HTTPConnection", return_value=conn) as factory,
        ):
            result = self.toolbox.fetch_url("http://example.com/")
        self.assertTrue(result["ok"], result)
        self.assertEqual(dns.call_count, 1)
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[0], "93.184.216.34")
        headers = conn.request.call_args.kwargs["headers"]
        self.assertEqual(headers["Host"], "example.com")

    def test_response_body_is_capped(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]
        response = _FakeResponse(
            body=b"A" * (tools._SSRF_MAX_RESPONSE_BYTES + 4096)
        )
        conn = Mock()
        conn.getresponse.return_value = response
        with (
            patch("tools.socket.getaddrinfo", return_value=answers),
            patch("tools.http.client.HTTPConnection", return_value=conn),
        ):
            result = self.toolbox.fetch_url("http://example.com/")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["size"], tools._SSRF_MAX_RESPONSE_BYTES)
        self.assertEqual(len(result["content"]), 10000)
        self.assertEqual(len(result["preview"]), 500)

    def test_dns_rebinding_cannot_change_connection_address(self):
        public = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]
        private = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ]
        conn = Mock()
        conn.getresponse.return_value = _FakeResponse()
        with (
            patch("tools.socket.getaddrinfo", side_effect=[public, private]) as dns,
            patch("tools.http.client.HTTPConnection", return_value=conn) as factory,
        ):
            result = self.toolbox.fetch_url("http://example.com/")
        self.assertTrue(result["ok"], result)
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(factory.call_args.args[0], "93.184.216.34")

    def test_relative_redirects_are_followed_after_validation(self):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            if url == "http://public.example/a/b":
                return {
                    "status": 302,
                    "reason": "Found",
                    "headers": {"location": "/next"},
                    "body": b"",
                }
            if url == "http://public.example/next":
                return {
                    "status": 200,
                    "reason": "OK",
                    "headers": {"Content-Type": "text/plain"},
                    "body": b"done",
                }
            raise AssertionError(url)

        with patch("tools._fetch_once", side_effect=fake_fetch):
            result = self.toolbox.fetch_url("http://public.example/a/b")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["url"], "http://public.example/next")
        self.assertEqual(result["content"], "done")
        self.assertEqual(calls, ["http://public.example/a/b", "http://public.example/next"])

    def test_redirect_to_rebinding_hostname_blocked(self):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            scheme, host, port, target = tools._parse_fetch_url(url)
            tools._resolve_validated_addresses(host, port)
            if url == "http://public.example/start":
                return {
                    "status": 302,
                    "reason": "Found",
                    "headers": {"location": "http://rebind.example/private"},
                    "body": b"",
                }
            raise AssertionError(url)

        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]
        private = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
        ]

        with (
            patch("tools._fetch_once", side_effect=fake_fetch),
            patch("tools.socket.getaddrinfo", side_effect=[answers, private]),
        ):
            result = self.toolbox.fetch_url("http://public.example/start")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "url_bloqueada")
        self.assertEqual(len(calls), 2)

    def test_redirect_loop_rejected(self):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            location = "/loop" if url.endswith("/start") else "/start"
            return {
                "status": 302,
                "reason": "Found",
                "headers": {"location": location},
                "body": b"",
            }

        with patch("tools._fetch_once", side_effect=fake_fetch):
            result = self.toolbox.fetch_url("http://public.example/start")
        self.assertFalse(result["ok"])
        self.assertIn("Bucle", result["message"])
        self.assertEqual(len(calls), 2)

    def test_redirect_limit_enforced(self):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            return {
                "status": 302,
                "reason": "Found",
                "headers": {"location": f"/r{len(calls)}"},
                "body": b"",
            }

        with patch("tools._fetch_once", side_effect=fake_fetch):
            result = self.toolbox.fetch_url("http://public.example/start")
        self.assertFalse(result["ok"])
        self.assertIn("Límite de redirects", result["message"])
        self.assertEqual(len(calls), tools._SSRF_MAX_REDIRECTS + 1)

    def test_https_to_http_downgrade_rejected(self):
        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            return {
                "status": 302,
                "reason": "Found",
                "headers": {"location": "http://public.example/insecure"},
                "body": b"",
            }

        with patch("tools._fetch_once", side_effect=fake_fetch):
            result = self.toolbox.fetch_url("https://public.example/start")
        self.assertFalse(result["ok"])
        self.assertIn("Downgrade", result["message"])
        self.assertEqual(len(calls), 1)

    def test_unsupported_redirect_scheme_rejected(self):
        def fake_fetch(url, timeout):
            return {
                "status": 302,
                "reason": "Found",
                "headers": {"location": "file:///etc/passwd"},
                "body": b"",
            }

        with patch("tools._fetch_once", side_effect=fake_fetch):
            result = self.toolbox.fetch_url("http://public.example/start")
        self.assertFalse(result["ok"])
        self.assertIn("Solo http/https", result["message"])

    def test_environment_proxy_is_not_used(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]
        conn = Mock()
        conn.getresponse.return_value = _FakeResponse()
        old_http = os.environ.get("http_proxy")
        old_https = os.environ.get("https_proxy")
        os.environ["http_proxy"] = "http://127.0.0.1:9"
        os.environ["https_proxy"] = "http://127.0.0.1:9"
        try:
            with (
                patch("tools.socket.getaddrinfo", return_value=answers),
                patch("tools.http.client.HTTPConnection", return_value=conn) as factory,
            ):
                result = self.toolbox.fetch_url("http://example.com/")
        finally:
            if old_http is None:
                os.environ.pop("http_proxy", None)
            else:
                os.environ["http_proxy"] = old_http
            if old_https is None:
                os.environ.pop("https_proxy", None)
            else:
                os.environ["https_proxy"] = old_https
        self.assertTrue(result["ok"], result)
        self.assertEqual(factory.call_args.args[0], "93.184.216.34")

    def test_zero_timeout_budget(self):
        result = self.toolbox.fetch_url("http://example.com/", timeout=0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "timeout")

    def test_redirect_chain_shares_timeout_budget(self):
        def fake_fetch(url, timeout):
            return {
                "status": 302,
                "reason": "Found",
                "headers": {"location": "/next"},
                "body": b"",
            }

        with (
            patch("tools.time.monotonic", side_effect=[0.0, 0.0, 100.0]),
            patch("tools._fetch_once", side_effect=fake_fetch) as fetch,
        ):
            result = self.toolbox.fetch_url("http://public.example/", timeout=5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(fetch.call_count, 1)

    def test_https_sni_uses_original_hostname(self):
        conn = _PinnedHTTPSConnection(
            "93.184.216.34", 443, tls_hostname="example.com", timeout=1
        )
        self.assertTrue(conn._context.check_hostname)
        sentinel = object()
        conn.sock = sentinel
        with (
            patch("tools.http.client.HTTPConnection.connect"),
            patch.object(conn._context, "wrap_socket") as wrap,
        ):
            conn.connect()
        wrap.assert_called_once_with(sentinel, server_hostname="example.com")


if __name__ == "__main__":
    unittest.main()
