"""Setup download and supply-chain verification."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher
import runtime_core


class TestSetupDownload(unittest.TestCase):
    def _catalog(self, digest: str, filename: str = "test.gguf") -> dict:
        return {
            "test": {
                "file": filename,
                "name": "Test Model",
                "profile": "general",
                "ctx": 4096,
                "hf_url": "https://example.com/test.gguf",
                "sha256": digest,
            }
        }

    def _run_setup(self, tmp: Path, catalog, download_side_effect):
        models = tmp / "models"
        bin_dir = tmp / "bin"
        os_key, arch_key = "linux", "x86_64"
        bin_dest = bin_dir / os_key / arch_key / runtime_core.server_exe_name(os_key)
        bin_dest.parent.mkdir(parents=True, exist_ok=True)
        bin_dest.write_bytes(b"binary")
        models.mkdir(parents=True, exist_ok=True)

        with (
            patch("launcher.MODEL_CATALOG", catalog),
            patch("launcher.MODELS", models),
            patch("launcher.BIN_DIR", bin_dir),
            patch("launcher._download_with_rate_limit", side_effect=download_side_effect),
            patch("builtins.input", return_value="1"),
            patch("launcher.ok"),
            patch("launcher.info"),
            patch("launcher.warn"),
            patch("launcher.err"),
        ):
            launcher.run_setup(os_key, arch_key)
        return models / catalog["test"]["file"]

    def test_existing_model_is_verified(self):
        payload = b"model-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        catalog = self._catalog(digest)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            models = tmp / "models"
            models.mkdir()
            (models / "test.gguf").write_bytes(payload)
            # download must not be called
            calls = []

            def fake_download(url, dest):
                calls.append((url, dest))
                raise AssertionError("download should not be called")

            dest = self._run_setup(tmp, catalog, fake_download)
            self.assertTrue(dest.exists())
            self.assertEqual(calls, [])

    def test_download_verifies_and_keeps_file(self):
        payload = b"model-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        catalog = self._catalog(digest)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            def fake_download(url, dest):
                dest.write_bytes(payload)

            dest = self._run_setup(tmp, catalog, fake_download)
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), payload)

    def test_download_mismatch_deletes_file_and_raises(self):
        payload = b"model-bytes"
        catalog = self._catalog("0" * 64)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)

            def fake_download(url, dest):
                dest.write_bytes(payload)

            with self.assertRaises(RuntimeError):
                self._run_setup(tmp, catalog, fake_download)
            self.assertFalse((tmp / "models" / "test.gguf").exists())


if __name__ == "__main__":
    unittest.main()
