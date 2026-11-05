#!/usr/bin/env python3
"""Fail-closed authentication and owner-vault provisioning."""
from __future__ import annotations

import json
import base64
import shutil
import stat
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from crypto import LEGACY_ENC_ITERATIONS, derive_key, encrypt_canary
from gateway import GatewayState, start_gateway
from session_auth import SessionManager, Vault, VaultError


class _GatewayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = Vault(self.tmp)
        self.sessions = SessionManager()
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=False,
            is_unlocked=lambda: False,
            vault=self.vault,
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

    def req(self, method, path, body=None, token=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
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
            parsed = {"_raw": raw.decode("utf-8", errors="replace")}
        return resp.status, parsed, dict(resp.getheaders())


class TestVault(_GatewayCase):
    def test_provision_creates_single_private_file(self):
        self.vault.provision("correct horse battery")
        self.assertTrue(self.vault.owner_lock.is_file())
        self.assertFalse(self.vault.salt_path.exists())
        self.assertFalse(self.vault.canary_path.exists())
        if hasattr(stat, "S_IMODE"):
            self.assertEqual(stat.S_IMODE(self.vault.owner_lock.stat().st_mode) & 0o077, 0)
        meta = self.vault.metadata()
        self.assertEqual(meta.get("version"), 2)
        self.assertEqual(meta.get("iterations"), 600_000)
        self.assertTrue(meta.get("salt"))
        self.assertTrue(meta.get("canary"))

    def test_legacy_vault_still_verifies_and_migrates(self):
        salt = b"L" * 16
        key, _ = derive_key("legacy password 123", salt, iterations=LEGACY_ENC_ITERATIONS)
        self.vault.data_dir.mkdir(parents=True, exist_ok=True)
        self.vault.salt_path.write_bytes(salt)
        self.vault.canary_path.write_text(encrypt_canary(key), encoding="ascii")
        self.vault.owner_lock.write_text(
            json.dumps({"version": 1, "marker": "bonsai-owner-vault"}),
            encoding="utf-8",
        )

        self.assertTrue(self.vault.verify("legacy password 123"))
        self.assertTrue(self.vault.owner_lock.is_file())
        self.assertFalse(self.vault.salt_path.exists())
        self.assertFalse(self.vault.canary_path.exists())
        self.assertEqual(self.vault.metadata().get("version"), 2)
        self.assertTrue(self.vault.verify("legacy password 123"))

    def test_corrupt_v2_does_not_fall_back_to_legacy_files(self):
        # Stale legacy files must not be honored when owner.lock claims v2.
        salt = b"K" * 16
        key, _ = derive_key("legacy password 123", salt, iterations=LEGACY_ENC_ITERATIONS)
        self.vault.data_dir.mkdir(parents=True, exist_ok=True)
        self.vault.salt_path.write_bytes(salt)
        self.vault.canary_path.write_text(encrypt_canary(key), encoding="ascii")
        self.vault.owner_lock.write_text(
            json.dumps(
                {
                    "version": 2,
                    "marker": "bonsai-owner-vault",
                    "iterations": 600_000,
                    "salt": base64.b64encode(b"x" * 16).decode(),
                    "canary": "not-a-valid-canary",
                }
            ),
            encoding="utf-8",
        )
        self.assertFalse(self.vault.verify("legacy password 123"))

    def test_short_password_rejected(self):
        with self.assertRaises(VaultError):
            self.vault.provision("short")

    def test_rotate_requires_old_or_force(self):
        self.vault.provision("first password 123")
        with self.assertRaises(VaultError):
            self.vault.provision("second password 456")
        self.vault.provision("second password 456", force=True)
        self.assertFalse(self.vault.verify("first password 123"))
        self.assertTrue(self.vault.verify("second password 456"))

    def test_verify_rejects_missing_and_wrong(self):
        self.assertFalse(self.vault.verify("anything at all"))
        self.vault.provision("correct horse battery")
        self.assertFalse(self.vault.verify("wrong password 123"))
        self.assertTrue(self.vault.verify("correct horse battery"))


class TestFailClosed(_GatewayCase):
    def test_no_vault_never_issues_token(self):
        status, data, _ = self.req("POST", "/api/session/unlock", {})
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "not_provisioned")
        self.assertNotIn("token", data)

    def test_empty_and_wrong_password_rejected(self):
        self.vault.provision("correct horse battery")
        status, data, _ = self.req("POST", "/api/session/unlock", {})
        self.assertEqual(status, 403)
        self.assertNotIn("token", data)
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {"password": "wrong password 123"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(data.get("error"), "invalid_credentials")

    def test_correct_password_unlocks_and_token_works(self):
        self.vault.provision("correct horse battery")
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {"password": "correct horse battery"}
        )
        self.assertEqual(status, 200)
        token = data.get("token")
        self.assertTrue(token)
        self.assertTrue(self.state.unlocked())

        status, data, _ = self.req("GET", "/api/identity", token=token)
        self.assertEqual(status, 200)
        self.assertIn("needs_setup", data)

        status, data, _ = self.req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("unlocked"))

    def test_unlock_after_password_does_not_mint_without_token(self):
        self.vault.provision("correct horse battery")
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {"password": "correct horse battery"}
        )
        self.assertEqual(status, 200)
        token = data.get("token")
        self.assertTrue(token)

        # E2: process unlock state alone must not mint tokens for local clients.
        status, data, _ = self.req("POST", "/api/session/unlock", {})
        self.assertEqual(status, 403)
        self.assertNotIn("token", data)

        # An already authenticated client may rotate/mint a new token.
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {}, token=token
        )
        self.assertEqual(status, 200)
        token2 = data.get("token")
        self.assertTrue(token2)
        self.assertNotEqual(token2, token)
        status, data, _ = self.req("GET", "/api/identity", token=token2)
        self.assertEqual(status, 200)
        self.assertIn("needs_setup", data)

    def test_process_unlock_alone_cannot_mint_token(self):
        self.state.mark_unlocked()
        self.assertTrue(self.state.unlocked())
        status, data, _ = self.req("POST", "/api/session/unlock", {})
        self.assertEqual(status, 403)
        self.assertNotIn("token", data)

        token = self.sessions.issue()
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {}, token=token
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("token"))

    def test_launcher_bootstrap_token_can_exchange_for_session(self):
        self.vault.provision("correct horse battery")
        bootstrap = self.state.issue_bootstrap_token(ttl=120)
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {}, token=bootstrap
        )
        self.assertEqual(status, 200)
        token = data.get("token")
        self.assertTrue(token)
        self.assertNotEqual(token, bootstrap)
        status, data, _ = self.req("GET", "/api/identity", token=token)
        self.assertEqual(status, 200)

    def test_unlock_rate_limit(self):
        self.vault.provision("correct horse battery")
        for _ in range(5):
            status, _, _ = self.req(
                "POST", "/api/session/unlock", {"password": "wrong password 123"}
            )
            self.assertEqual(status, 403)
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {"password": "correct horse battery"}
        )
        self.assertEqual(status, 429)

    def test_debug_bypass_is_explicit(self):
        state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=True,
            is_unlocked=lambda: False,
            vault=None,
            host="127.0.0.1",
            port=0,
            auth_required=False,
        )
        server = start_gateway(state, background=True)
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            data = json.dumps({}).encode()
            conn.request(
                "POST",
                "/api/session/unlock",
                body=data,
                headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertTrue(payload.get("token"))
        finally:
            server.shutdown()
            server.server_close()

    def test_insecure_mode_is_explicit(self):
        state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=False,
            is_unlocked=lambda: False,
            vault=None,
            insecure_mode=True,
            host="127.0.0.1",
            port=0,
            auth_required=True,
        )
        server = start_gateway(state, background=True)
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            data = json.dumps({}).encode()
            conn.request(
                "POST",
                "/api/session/unlock",
                body=data,
                headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertTrue(payload.get("token"))
        finally:
            server.shutdown()
            server.server_close()

    def test_verified_yubikey_state_requires_bootstrap_token(self):
        self.state.is_unlocked = lambda: True
        status, data, _ = self.req("POST", "/api/session/unlock", {})
        self.assertEqual(status, 403)
        self.assertNotIn("token", data)

        bootstrap = self.state.issue_bootstrap_token(ttl=120)
        status, data, _ = self.req(
            "POST", "/api/session/unlock", {}, token=bootstrap
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("token"))


if __name__ == "__main__":
    unittest.main()
