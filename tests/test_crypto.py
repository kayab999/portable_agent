import base64
import unittest

from crypto import (
    _keystream,
    _split_keys,
    decrypt,
    encrypt,
    encrypt_canary,
    verify_canary,
)


class TestCrypto(unittest.TestCase):
    def test_roundtrip_v1(self):
        key = b"0" * 32
        ct = encrypt("hola bonsai", key)
        self.assertEqual(decrypt(ct, key), "hola bonsai")

    def test_tamper_detected(self):
        key = b"1" * 32
        ct = encrypt("secret", key)
        # Flip a byte in the base64 payload
        raw = list(ct)
        raw[-2] = "A" if raw[-2] != "A" else "B"
        tampered = "".join(raw)
        with self.assertRaises(ValueError):
            decrypt(tampered, key)

    def test_canary(self):
        key = b"2" * 32
        can = encrypt_canary(key)
        self.assertTrue(verify_canary(can, key))
        self.assertFalse(verify_canary(can, b"3" * 32))

    def test_legacy_v0_rejected_by_default(self):
        key = b"4" * 32
        enc, _ = _split_keys(key)
        nonce = b"n" * 16
        pt = b"legacy-secret"
        ks = _keystream(enc, nonce, len(pt))
        ct = bytes(a ^ b for a, b in zip(pt, ks, strict=True))
        payload = base64.b64encode(nonce + ct).decode()
        with self.assertRaises(ValueError) as ctx:
            decrypt(payload, key)
        self.assertIn("Legacy", str(ctx.exception))

    def test_legacy_v0_opt_in(self):
        key = b"5" * 32
        enc, _ = _split_keys(key)
        nonce = b"m" * 16
        pt = b"migrate-me"
        ks = _keystream(enc, nonce, len(pt))
        ct = bytes(a ^ b for a, b in zip(pt, ks, strict=True))
        payload = base64.b64encode(nonce + ct).decode()
        self.assertEqual(decrypt(payload, key, allow_legacy=True), "migrate-me")


if __name__ == "__main__":
    unittest.main()
