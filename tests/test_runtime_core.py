"""runtime_core — plataforma, binario, modelos y configuración compartidos."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_core


def _mk_gguf(directory: Path, name: str) -> Path:
    p = directory / name
    p.write_bytes(b"GGUF-fake")
    return p


class TestDetectPlatform(unittest.TestCase):
    def test_windows_x86(self):
        with patch.object(
            runtime_core.platform,
            "system",
            return_value="Windows",
        ), patch.object(
            runtime_core.platform, "machine", return_value="AMD64"
        ):
            self.assertEqual(runtime_core.detect_platform(), ("windows", "x86_64"))

    def test_mac_arm(self):
        with patch.object(
            runtime_core.platform, "system", return_value="Darwin"
        ), patch.object(
            runtime_core.platform, "machine", return_value="arm64"
        ):
            self.assertEqual(runtime_core.detect_platform(), ("macos", "arm64"))


class TestFindServerBinary(unittest.TestCase):
    def test_packaged_binary_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            exe = root / "linux" / "x86_64" / "llama-server"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"ELF")
            with patch.object(
                runtime_core.shutil, "which", return_value="/usr/bin/llama-server"
            ):
                found, is_fallback = runtime_core.find_server_binary(
                    root, "linux", "x86_64"
                )
        self.assertFalse(is_fallback)
        self.assertEqual(found.resolve(), exe.resolve())

    def test_system_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(
                runtime_core.shutil, "which", return_value="/usr/bin/llama-server"
            ):
                found, is_fallback = runtime_core.find_server_binary(
                    Path(td), "linux", "x86_64"
                )
        self.assertTrue(is_fallback)
        self.assertEqual(str(found), "/usr/bin/llama-server")

    def test_nothing_found_raises_lookup(self):
        with patch.object(runtime_core.shutil, "which", return_value=None):
            with self.assertRaises(LookupError):
                runtime_core.find_server_binary(Path("/no/bin/linux"), "linux", "x86_64")


class TestPickModelShared(unittest.TestCase):
    """Paridad del orden de resolución para un catálogo arbitrario."""

    CATALOG = {
        "big": {"file": "Big.gguf", "name": "Big", "ctx": 4096},
        "small": {"file": "Small.gguf", "name": "Small", "ctx": 2048},
    }

    def _pick(self, requested, models_dir):
        msgs = []
        return runtime_core.pick_model(
            requested,
            models_dir=models_dir,
            catalog=self.CATALOG,
            error=lambda m: (_ for _ in ()).throw(SystemExit(m)),
            warn=msgs.append,
            no_models_msg="no models",
        ), msgs

    def test_catalog_order_preference(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Big.gguf")
            _mk_gguf(tmp, "Small.gguf")
            (key, _, path), msgs = self._pick(None, tmp)
        self.assertEqual(key, "big")
        self.assertEqual(path.name, "Big.gguf")
        self.assertFalse(msgs)

    def test_unknown_request_warns_and_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _mk_gguf(tmp, "Big.gguf")
            (key, _, _), msgs = self._pick("gpt-42", tmp)
        self.assertEqual(key, "big")
        self.assertTrue(msgs[0].startswith("Modelo 'gpt-42'"))

    def test_no_models_message(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                self._pick(None, Path(td))


class TestConfig(unittest.TestCase):
    def test_config_file_env_override(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "other.json"
            with patch.dict("os.environ", {"BONSAI_CONFIG": str(cfg)}):
                self.assertEqual(runtime_core.config_file(Path("x/config.json")), cfg)

    def test_config_file_default(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual(
                runtime_core.config_file(Path("x/config.json")).as_posix(),
                "x/config.json",
            )

    def test_load_config_flags_ipv6(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "config.json"
            cfg_file.write_text(json.dumps({"host": "::1"}))
            cfg = runtime_core.load_config(cfg_file)
        self.assertEqual(cfg.get("_ipv6_requested"), "::1")
        self.assertIsNotNone(runtime_core.host_policy_error(cfg["_ipv6_requested"]))

    def test_host_policy_allows_ipv4_loopback(self):
        self.assertIsNone(runtime_core.host_policy_error("127.0.0.1"))
        self.assertIsNone(runtime_core.host_policy_error("localhost"))

    def test_load_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "config.json"
            runtime_core.save_config(cfg_file, {"port": 9999})
            loaded = runtime_core.load_config(cfg_file)
        self.assertEqual(loaded["port"], 9999)
        self.assertEqual(loaded["host"], "127.0.0.1")
        self.assertEqual(loaded["llm_port_offset"], 100)

    def test_host_clamped_without_lan_flag(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "config.json"
            runtime_core.save_config(cfg_file, {"host": "0.0.0.0"})
            with patch.dict("os.environ", {}, clear=False):
                loaded = runtime_core.load_config(cfg_file)
        self.assertTrue(loaded["_host_clamped"])
        self.assertEqual(loaded["host"], "127.0.0.1")

    def test_host_lan_allowed_with_flag(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "config.json"
            runtime_core.save_config(cfg_file, {"host": "0.0.0.0"})
            with patch.dict("os.environ", {"BONSAI_ALLOW_LAN": "1"}, clear=False):
                loaded = runtime_core.load_config(cfg_file)
        self.assertNotIn("_host_clamped", loaded)
        self.assertEqual(loaded["host"], "0.0.0.0")

    def test_corrupt_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "config.json"
            cfg_file.write_text("{not json")
            loaded = runtime_core.load_config(cfg_file)
        self.assertEqual(loaded["port"], 8080)


class TestServerBinaryDownload(unittest.TestCase):
    RELEASES = [
        {
            "tag_name": "prism-b12128",
            "assets": [
                {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "url": "x"},
                {
                    "name": "llama-prism-b12128-bin-ubuntu-x64.tar.gz",
                    "browser_download_url": "file:///fake/asset.tar.gz",
                },
                {
                    "name": "llama-prism-b12128-bin-win-cpu-x64.zip",
                    "browser_download_url": "file:///fake/asset-win.zip",
                },
            ],
        }
    ]

    def test_asset_suffix_mapping(self):
        self.assertEqual(
            runtime_core._asset_suffix("linux", "x86_64"), "ubuntu-x64"
        )
        self.assertEqual(
            runtime_core._asset_suffix("macos", "arm64"), "macos-arm64"
        )
        self.assertEqual(
            runtime_core._asset_suffix("windows", "x86_64"), "win-cpu-x64"
        )
        self.assertIsNone(runtime_core._asset_suffix("ios", "arm64"))

    def test_pick_release_asset(self):
        asset = runtime_core.pick_release_asset(self.RELEASES, "linux", "x86_64")
        self.assertEqual(asset["name"], "llama-prism-b12128-bin-ubuntu-x64.tar.gz")
        self.assertEqual(
            runtime_core.pick_release_asset(self.RELEASES, "windows", "x86_64")[
                "name"
            ],
            "llama-prism-b12128-bin-win-cpu-x64.zip",
        )
        self.assertIsNone(
            runtime_core.pick_release_asset(self.RELEASES, "macos", "arm64")
        )

    def test_pick_ignores_cudart_and_wrong_platform(self):
        repos = [
            {
                "assets": [
                    {"name": "cudart-llama-bin-ubuntu-x64.tar.gz"},
                    {"name": "llama-prism-b10831-bin-ubuntu-vulkan-x64.tar.gz"},
                    {"name": "llama-prism-b10831-bin-ubuntu-cuda-12.4-x64.tar.gz"},
                ]
            }
        ]
        self.assertIsNone(runtime_core.pick_release_asset(repos, "linux", "x86_64"))

    def test_download_requires_and_verifies_asset_digest(self):
        import hashlib

        payload = b"fake-llama-binary-archive"
        expected = hashlib.sha256(payload).hexdigest()
        release = [
            {
                "assets": [
                    {
                        "name": "llama-prism-x-bin-ubuntu-x64.tar.gz",
                        "browser_download_url": "https://example.com/asset",
                        "digest": f"sha256:{expected}",
                    }
                ]
            }
        ]

        def fake_download(url, dest, progress_cb=None):
            dest.write_bytes(payload)

        with (
            patch("runtime_core._http_get", return_value=__import__("json").dumps(release).encode()),
            patch("runtime_core._download_stream", side_effect=fake_download),
            patch("runtime_core._extract_archive") as extract,
            tempfile.TemporaryDirectory() as td,
        ):
            dest = runtime_core.download_server_binary(
                Path(td) / "bin", "linux", "x86_64"
            )
            self.assertEqual(dest, Path(td) / "bin" / "linux" / "x86_64" / "llama-server")
            extract.assert_called_once()

    def test_download_rejects_bad_asset_digest(self):
        payload = b"fake-llama-binary-archive"
        release = [
            {
                "assets": [
                    {
                        "name": "llama-prism-x-bin-ubuntu-x64.tar.gz",
                        "browser_download_url": "https://example.com/asset",
                        "digest": "sha256:" + "0" * 64,
                    }
                ]
            }
        ]

        def fake_download(url, dest, progress_cb=None):
            dest.write_bytes(payload)

        with (
            patch("runtime_core._http_get", return_value=__import__("json").dumps(release).encode()),
            patch("runtime_core._download_stream", side_effect=fake_download),
            patch("runtime_core._extract_archive") as extract,
            tempfile.TemporaryDirectory() as td,
        ):
            with self.assertRaises(RuntimeError):
                runtime_core.download_server_binary(Path(td) / "bin", "linux", "x86_64")
            extract.assert_not_called()

    def test_download_fails_without_digest_or_env(self):
        release = [
            {
                "assets": [
                    {
                        "name": "llama-prism-x-bin-ubuntu-x64.tar.gz",
                        "browser_download_url": "https://example.com/asset",
                    }
                ]
            }
        ]

        def fake_download(url, dest, progress_cb=None):
            dest.write_bytes(b"payload")

        with (
            patch("runtime_core._http_get", return_value=__import__("json").dumps(release).encode()),
            patch("runtime_core._download_stream", side_effect=fake_download),
            patch("runtime_core._extract_archive") as extract,
            tempfile.TemporaryDirectory() as td,
            patch.dict("os.environ", {}, clear=True),
        ):
            with self.assertRaises(RuntimeError):
                runtime_core.download_server_binary(Path(td) / "bin", "linux", "x86_64")
            extract.assert_not_called()


class TestFileSha256(unittest.TestCase):
    def test_file_sha256_streaming(self):
        import tempfile
        from pathlib import Path

        payload = b"abc" * 100000
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "payload.bin"
            path.write_bytes(payload)
            self.assertEqual(
                runtime_core.file_sha256(path), __import__("hashlib").sha256(payload).hexdigest()
            )

    def test_verify_file_sha256_ok_and_bad(self):
        import tempfile
        from pathlib import Path

        payload = b"payload-bytes"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "payload.bin"
            path.write_bytes(payload)
            expected = __import__("hashlib").sha256(payload).hexdigest()
            self.assertEqual(runtime_core.verify_file_sha256(path, expected), expected)
            with self.assertRaises(RuntimeError):
                runtime_core.verify_file_sha256(path, "0" * 64)
            with self.assertRaises(RuntimeError):
                runtime_core.verify_file_sha256(path, "not-a-hash")


class TestExtractArchive(unittest.TestCase):
    def _make_targz(self, td: Path) -> Path:
        import tarfile

        root = td / "llama-prism-test"
        root.mkdir()
        (root / "llama-server").write_bytes(b"\x7fELF-fake")
        (root / "libllama-server-impl.so").write_bytes(b"lib")
        arc = td / "asset.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(root, arcname="llama-prism-test")
        return arc

    def _make_zip(self, td: Path, entries: dict[str, bytes]) -> Path:
        import zipfile

        arc = td / "asset.zip"
        with zipfile.ZipFile(arc, "w") as zf:
            for name, body in entries.items():
                zf.writestr(name, body)
        return arc

    def test_targz_hoists_versioned_dir(self):
        with tempfile.TemporaryDirectory() as td:
            arc = self._make_targz(Path(td))
            out = Path(td) / "linux" / "x86_64"
            runtime_core._extract_archive(arc, "llama-server", out)
            self.assertEqual((out / "llama-server").read_bytes(), b"\x7fELF-fake")
            self.assertTrue((out / "libllama-server-impl.so").exists())
            if __import__("os").name != "nt":
                self.assertTrue((out / "llama-server").stat().st_mode & 0o111)

    def test_zip_hoists_versioned_dir(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            arc = self._make_zip(
                td,
                {
                    "llama-prism-win/llama-server.exe": b"MZ-fake",
                    "llama-prism-win/ll.dll": b"dll",
                },
            )
            out = td / "windows" / "x86_64"
            runtime_core._extract_archive(arc, "llama-server.exe", out)
            self.assertEqual((out / "llama-server.exe").read_bytes(), b"MZ-fake")
            self.assertTrue((out / "ll.dll").exists())

    def test_missing_exe_raises(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            arc = self._make_targz(td)
            out = Path(td) / "out"
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-cli", out)

    def test_zip_rejects_symlink(self):
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            arc = td / "bad.zip"
            info = zipfile.ZipInfo("link")
            info.external_attr = (0o120777 & 0xFFFF) << 16
            with zipfile.ZipFile(arc, "w") as zf:
                zf.writestr(info, "/etc/passwd")
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_zip_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            arc = self._make_zip(td, {"../escape.txt": b"bad"})
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", td / "out")
            self.assertFalse((td / "escape.txt").exists())

    def test_zip_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            arc = self._make_zip(td, {"/etc/escape": b"bad"})
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_zip_rejects_backslash_and_drive(self):
        for name in ("dir\\escape", "C:/escape", "C:escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                td = Path(td)
                arc = self._make_zip(td, {name: b"bad"})
                with self.assertRaises(RuntimeError):
                    runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_zip_rejects_reserved_names(self):
        for name in ("CON", "nul.txt", "COM1", "aux/file"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                td = Path(td)
                arc = self._make_zip(td, {name: b"bad"})
                with self.assertRaises(RuntimeError):
                    runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_zip_rejects_duplicate_and_casefold_collision(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            arc = self._make_zip(td, {"llama-server": b"one", "LLAMA-SERVER": b"two"})
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_zip_member_count_limit(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            runtime_core, "_ARCHIVE_MAX_MEMBERS", 2
        ):
            td = Path(td)
            arc = self._make_zip(
                td, {"llama-server": b"x", "a": b"1", "b": b"2"}
            )
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_zip_uncompressed_size_limit(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            runtime_core, "_ARCHIVE_MAX_UNCOMPRESSED_BYTES", 4
        ):
            td = Path(td)
            arc = self._make_zip(td, {"llama-server": b"12345"})
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_tar_rejects_symlink_and_hardlink(self):
        import tarfile

        for kind in ("sym", "link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                td = Path(td)
                arc = td / "asset.tar.gz"
                with tarfile.open(arc, "w:gz") as tf:
                    info = tarfile.TarInfo("llama-server")
                    info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                    info.linkname = "/etc/passwd"
                    tf.addfile(info)
                with self.assertRaises(RuntimeError):
                    runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_tar_rejects_device_and_traversal(self):
        import tarfile

        for name, kind in (("/dev/null", tarfile.CHRTYPE), ("../escape", tarfile.REGTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                td = Path(td)
                arc = td / "asset.tar.gz"
                with tarfile.open(arc, "w:gz") as tf:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    tf.addfile(info, __import__("io").BytesIO(b"x"))
                with self.assertRaises(RuntimeError):
                    runtime_core._extract_archive(arc, "llama-server", td / "out")

    def test_failed_extraction_leaves_no_stage_directory(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            out = td / "out"
            arc = self._make_zip(td, {"../escape": b"bad"})
            with self.assertRaises(RuntimeError):
                runtime_core._extract_archive(arc, "llama-server", out)
            leftovers = list(out.parent.glob(".bonsai-stage-*"))
            self.assertEqual(leftovers, [])



if __name__ == "__main__":
    unittest.main()
