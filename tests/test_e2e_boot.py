"""E2E real: launcher → llama-server → gateway → /v1 (y apagado limpio).

Se skippea automáticamente si falta el binario de plataforma o el .gguf
de prueba (tests/test_data/stories260K.gguf, ~1.1 MB).
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from session_auth import Vault

ROOT = Path(__file__).parent.parent
LAUNCHER = ROOT / "launcher.py"
BINARY = ROOT / "bin" / "linux" / "x86_64" / "llama-server"
MODEL = ROOT / "tests" / "test_data" / "stories260K.gguf"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _free_port_pair() -> tuple[int, int]:
    """Par (gateway, llm) con offset +100 libre también."""
    for _ in range(10):
        gateway = _free_port()
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", gateway + 100))
            s.close()
            return gateway, gateway + 100
        except OSError:
            continue
    raise RuntimeError("no free port pair")


@unittest.skipUnless(LAUNCHER.exists(), "launcher ausente")
@unittest.skipUnless(BINARY.exists(), "binario llama-server ausente para esta plataforma")
@unittest.skipUnless(MODEL.exists(), "tests/test_data/stories260K.gguf ausente")
@unittest.skipUnless(os.name == "posix", "solo POSIX (CI local/Linux)")
class TestE2EBoot(unittest.TestCase):
    def test_boot_health_completion_shutdown(self):
        gateway_port, llm_port = _free_port_pair()
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            env = dict(
                os.environ,
                BONSAI_DEBUG="1",
                BONSAI_CONFIG=str(cfg),
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(LAUNCHER),
                    "-m",
                    str(MODEL),
                    "--port",
                    str(gateway_port),
                    "--ngl",
                    "0",
                    "--no-browser",
                ],
                env=env,
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                # 1) llama-server interno arriba
                self.assertTrue(
                    self._wait_health(f"http://127.0.0.1:{llm_port}/health", 60),
                    "llama-server no respondió a tiempo",
                )
                # 2) gateway arriba
                self.assertTrue(
                    self._wait_health(f"http://127.0.0.1:{gateway_port}/health", 10),
                    "gateway no respondió a tiempo",
                )
                # 3) inferencia end-to-end vía proxy /v1
                payload = json.dumps(
                    {
                        "model": "stories260K",
                        "messages": [{"role": "user", "content": "hola"}],
                        "max_tokens": 8,
                        "temperature": 0,
                    }
                ).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{gateway_port}/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read())
                self.assertIn("choices", data)
                self.assertTrue(data["choices"][0]["message"]["content"].strip())
                # 4) shutdown limpio: SIGTERM y ambos procesos mueren
                proc.send_signal(signal.SIGTERM)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    self.fail("el launcher ignoró SIGTERM")

            # 5) el puerto del gateway quedó liberado
            self.assertFalse(
                self._port_open(gateway_port),
                "gateway sigue escuchando tras SIGTERM",
            )
            self.assertFalse(
                self._port_open(llm_port),
                "llama-server sigue escuchando tras SIGTERM",
            )

    @staticmethod
    def _http_json(url, method="GET", body=None, token=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return e.code, {"_raw": raw.decode("utf-8", errors="replace")}

    def test_production_boot_without_vault_is_locked(self):
        gateway_port, _llm_port = _free_port_pair()
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            env = dict(
                os.environ,
                BONSAI_DEBUG="0",
                BONSAI_INSECURE="0",
                BONSAI_CONFIG=str(cfg),
                BONSAI_VAULT_ROOT=str(Path(td) / "vault"),
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(LAUNCHER),
                    "-m",
                    str(MODEL),
                    "--port",
                    str(gateway_port),
                    "--ngl",
                    "0",
                    "--no-browser",
                ],
                env=env,
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                base = f"http://127.0.0.1:{gateway_port}"
                self.assertTrue(
                    self._wait_health(f"{base}/api/health", 60),
                    "gateway no respondió a tiempo",
                )
                status, data = self._http_json(f"{base}/api/health")
                self.assertEqual(status, 200)
                self.assertTrue(data.get("auth_required"))
                self.assertFalse(data.get("unlocked"))

                status, data = self._http_json(
                    f"{base}/api/session/unlock", method="POST", body={}
                )
                self.assertEqual(status, 403)
                self.assertNotIn("token", data)

                status, _ = self._http_json(f"{base}/v1/models")
                self.assertEqual(status, 401)

                status, _ = self._http_json(f"{base}/health")
                self.assertEqual(status, 200)
            finally:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_production_boot_with_vault_unlocks(self):
        gateway_port, _llm_port = _free_port_pair()
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config.json"
            vault_root = Path(td) / "vault"
            vault = Vault(vault_root)
            vault.provision("test password 123")
            env = dict(
                os.environ,
                BONSAI_DEBUG="0",
                BONSAI_INSECURE="0",
                BONSAI_CONFIG=str(cfg),
                BONSAI_VAULT_ROOT=str(vault_root),
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(LAUNCHER),
                    "-m",
                    str(MODEL),
                    "--port",
                    str(gateway_port),
                    "--ngl",
                    "0",
                    "--no-browser",
                ],
                env=env,
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                base = f"http://127.0.0.1:{gateway_port}"
                self.assertTrue(
                    self._wait_health(f"{base}/api/health", 60),
                    "gateway no respondió a tiempo",
                )
                status, data = self._http_json(
                    f"{base}/api/session/unlock",
                    method="POST",
                    body={"password": "test password 123"},
                )
                self.assertEqual(status, 200)
                token = data.get("token")
                self.assertTrue(token)

                status, data = self._http_json(
                    f"{base}/api/identity", token=token
                )
                self.assertEqual(status, 200)
                self.assertIn("needs_setup", data)

                status, _ = self._http_json(f"{base}/v1/models", token=token)
                self.assertEqual(status, 200)

                status, data = self._http_json(
                    f"{base}/api/session/unlock", method="POST", body={}
                )
                self.assertEqual(status, 200)
                self.assertTrue(data.get("token"))
            finally:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    @staticmethod
    def _wait_health(url: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if TestE2EBoot._health_ok(url):
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def _health_ok(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    @staticmethod
    def _port_open(port: int) -> bool:
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
