"""Descubrimiento y selección de modelos (.gguf) — catálogo + libre."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agentportable
import launcher


def _mk_gguf(directory: Path, name: str) -> Path:
    p = directory / name
    p.write_bytes(b"GGUF-fake-bytes")
    return p


class TestFindAvailableModels(unittest.TestCase):
    def _patched(self, tmp: Path):
        return patch.object(launcher, "MODELS", tmp)

    def test_catalog_model_matched_by_exact_file_name(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            with self._patched(tmp):
                available = launcher.find_available_models()
        self.assertEqual([k for k, _ in available], ["bonsai-8b"])

    def test_arbitrary_gguf_discovered_as_custom(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "BitNet-b1.58-3B.gguf")
            with self._patched(tmp):
                available = launcher.find_available_models()
        self.assertEqual(
            [(k, m["file"]) for k, m in available],
            [("custom:BitNet-b1.58-3B", "BitNet-b1.58-3B.gguf")],
        )

    def test_custom_entry_has_default_ctx(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "tiny-model.gguf")
            with self._patched(tmp):
                available = launcher.find_available_models()
        self.assertEqual(available[0][1]["ctx"], launcher.DEFAULT_MODEL_CTX)

    def test_no_duplicate_for_catalog_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            with self._patched(tmp):
                available = launcher.find_available_models()
        self.assertEqual(len([k for k, _ in available if k == "custom:Bonsai-8B"]), 0)

    def test_catalog_not_exposed_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with self._patched(tmp):
                available = launcher.find_available_models()
        self.assertEqual(available, [])

    def test_agentportable_discovery_matches(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            _mk_gguf(tmp, "gemma-test.gguf")
            with patch.object(agentportable, "MODELS", tmp):
                keys = [k for k, _ in agentportable.find_available_models()]
        self.assertIn("bonsai-8b", keys)
        self.assertIn("custom:gemma-test", keys)


class TestPickModel(unittest.TestCase):
    def test_empty_available_exits(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(launcher, "MODELS", Path(td)):
                with self.assertRaises(SystemExit):
                    launcher.pick_model(None)

    def test_none_prefers_most_capable(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-4B.gguf")
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            zz = _mk_gguf(tmp, "zz-small.gguf")
            with patch.object(launcher, "MODELS", tmp):
                key, meta, path = launcher.pick_model(None)
        self.assertEqual(key, "bonsai-8b")
        self.assertEqual(path.resolve(), (tmp / "Bonsai-8B.gguf").resolve())
        self.assertNotEqual(path.resolve(), zz.resolve())

    def test_request_by_arbitrary_file_name(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            target = _mk_gguf(tmp, "BitNet-b1.58.gguf")
            with patch.object(launcher, "MODELS", tmp):
                key, meta, path = launcher.pick_model("BitNet-b1.58.gguf")
        self.assertEqual(key, "custom:BitNet-b1.58")
        self.assertEqual(path.resolve(), target.resolve())

    def test_request_by_explicit_path_outside_models(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            with tempfile.TemporaryDirectory() as td2:
                outside = _mk_gguf(Path(td2), "usb-model.gguf")
                with patch.object(launcher, "MODELS", tmp):
                    key, meta, path = launcher.pick_model(str(outside))
        self.assertEqual(key, "custom:usb-model")
        self.assertEqual(path.resolve(), outside.resolve().resolve())

    def test_request_by_custom_key(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = _mk_gguf(tmp, "my-model.gguf")
            with patch.object(launcher, "MODELS", tmp):
                key, _, path = launcher.pick_model("custom:my-model")
        self.assertEqual(key, "custom:my-model")
        self.assertEqual(path.resolve(), target.resolve())

    def test_catalog_key_case_insensitive(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            with patch.object(launcher, "MODELS", tmp):
                key, _, _ = launcher.pick_model("BONSAI-8B")
        self.assertEqual(key, "bonsai-8b")

    def test_unknown_request_falls_back_to_first(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-8B.gguf")
            with patch.object(launcher, "MODELS", tmp):
                key, _, _ = launcher.pick_model("gpt-5")
        self.assertEqual(key, "bonsai-8b")

    def test_explicit_path_wins_when_models_empty(self):
        with tempfile.TemporaryDirectory() as td2:
            outside = _mk_gguf(Path(td2), "usb-only.gguf")
            with tempfile.TemporaryDirectory() as td:
                with patch.object(launcher, "MODELS", Path(td)):
                    key, meta, path = launcher.pick_model(str(outside))
        self.assertEqual(key, "custom:usb-only")
        self.assertEqual(path.resolve(), outside.resolve())
        self.assertEqual(meta["file"], "usb-only.gguf")

    def test_nonexistent_gguf_path_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Bonsai-4B.gguf")
            with patch.object(launcher, "MODELS", tmp):
                key, _, path = launcher.pick_model("/no/such/model.gguf")
        self.assertEqual(key, "bonsai-4b")
        self.assertEqual(path.name, "Bonsai-4B.gguf")


if __name__ == "__main__":
    unittest.main()
