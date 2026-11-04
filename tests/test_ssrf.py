import unittest

from tools import _ssrf_block_reason


class TestSSRF(unittest.TestCase):
    def test_localhost_blocked(self):
        self.assertIsNotNone(_ssrf_block_reason("http://127.0.0.1/"))
        self.assertIsNotNone(_ssrf_block_reason("http://localhost/x"))

    def test_metadata_blocked(self):
        self.assertIsNotNone(
            _ssrf_block_reason("http://169.254.169.254/latest/meta-data/")
        )

    def test_public_ok(self):
        # May still block if DNS maps oddly; example.com should resolve public
        reason = _ssrf_block_reason("https://example.com/")
        # Allow None (ok) only — if offline DNS fails, that's a block reason string
        self.assertTrue(reason is None or "resolver" in reason.lower() or "resolv" in (reason or "").lower() or "No se pudo" in (reason or ""))


if __name__ == "__main__":
    unittest.main()
