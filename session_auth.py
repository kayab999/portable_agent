#!/usr/bin/env python3
"""
session_auth.py — In-memory session tokens for Bonsai Portable gateway.

Tokens live only in process RAM (never written to disk).
DEBUG mode can bypass auth at the gateway layer; this module still issues tokens.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from crypto import (
    ENC_ITERATIONS,
    LEGACY_ENC_ITERATIONS,
    MAX_ENC_ITERATIONS,
    MIN_ENC_ITERATIONS,
    derive_key,
    encrypt_canary,
    verify_canary,
)


class VaultError(ValueError):
    """Raised when a vault cannot be provisioned or read."""


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write bytes durably, then atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


class Vault:
    """Password vault stored as one atomically replaced ``owner.lock`` file.

    v2 format (single file, mode 0600)::

        {
          "version": 2,
          "marker": "bonsai-owner-vault",
          "kdf": "pbkdf2-hmac-sha256",
          "iterations": 600000,
          "salt": "<base64>",
          "canary": "<base64 v1 ciphertext>",
          "created_at": "..."
        }

    Legacy rc.3 vaults (``owner.lock`` marker + ``data/vault_salt.bin`` +
    ``data/vault_canary.txt``) can still be verified. A successful legacy
    unlock best-effort migrates them to v2 at the current KDF cost.
    """

    VERSION = 2
    MARKER = "bonsai-owner-vault"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.owner_lock = self.root / "owner.lock"
        # Legacy paths kept for reading/migrating rc.3 vaults.
        self.salt_path = self.data_dir / "vault_salt.bin"
        self.canary_path = self.data_dir / "vault_canary.txt"

    # ── metadata helpers ──────────────────────────────────────────────────

    def _read_metadata(self) -> dict | None:
        try:
            raw = self.owner_lock.read_text(encoding="utf-8")
            meta = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            return None
        return meta if isinstance(meta, dict) else None

    @classmethod
    def _is_v2(cls, meta: dict | None) -> bool:
        return bool(
            meta
            and meta.get("version") == cls.VERSION
            and meta.get("marker") == cls.MARKER
            and isinstance(meta.get("salt"), str)
            and isinstance(meta.get("canary"), str)
        )

    def exists(self) -> bool:
        """True for a valid v2 vault or for the legacy three-file layout."""
        if not self.owner_lock.is_file():
            return False
        meta = self._read_metadata()
        if self._is_v2(meta):
            return True
        # Legacy marker + separate salt/canary files.
        return bool(
            meta
            and self.salt_path.is_file()
            and self.canary_path.is_file()
            and self.salt_path.stat().st_size > 0
            and self.canary_path.stat().st_size > 0
        )

    def metadata(self) -> dict:
        """Return a copy of the on-disk metadata (empty when unreadable)."""
        meta = self._read_metadata()
        return dict(meta) if meta else {}

    # ── provisioning ─────────────────────────────────────────────────────

    def _v2_document(self, password: str) -> dict:
        salt = os.urandom(16)
        key, _ = derive_key(password, salt)
        canary = encrypt_canary(key)
        return {
            "version": self.VERSION,
            "marker": self.MARKER,
            "kdf": "pbkdf2-hmac-sha256",
            "iterations": ENC_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "canary": canary,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _write_v2(self, password: str) -> None:
        document = self._v2_document(password)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        # One temp file + fsync + os.replace: rotation is a single atomic step.
        _atomic_write(
            self.owner_lock,
            json.dumps(document, indent=2).encode("utf-8"),
        )
        self._remove_legacy()

    def _remove_legacy(self) -> None:
        for path in (self.salt_path, self.canary_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def provision(self, password: str, *, force: bool = False) -> None:
        """Create or rotate the vault. Requires force to overwrite in place."""
        password = (password or "").strip()
        if len(password) < 12:
            raise VaultError("password must be at least 12 characters")
        if self.exists() and not force:
            raise VaultError("vault already exists; provide the current password to rotate")
        self._write_v2(password)

    # ── verification ─────────────────────────────────────────────────────

    def _verify_legacy(self, password: str) -> bool:
        try:
            salt = self.salt_path.read_bytes()
            canary = self.canary_path.read_text(encoding="ascii").strip()
            key, _ = derive_key(
                password, salt, iterations=LEGACY_ENC_ITERATIONS
            )
            if not verify_canary(canary, key):
                return False
        except (OSError, ValueError, UnicodeError):
            return False
        # Best-effort migration; a failed write never blocks a valid unlock.
        try:
            self._write_v2(password)
        except OSError:
            pass
        return True

    def verify(self, password: str) -> bool:
        if not password or not self.owner_lock.is_file():
            return False
        meta = self._read_metadata()
        if self._is_v2(meta):
            # Never fall back to legacy files when v2 fields are present:
            # that would allow a downgrade to the weaker legacy format.
            try:
                salt = base64.b64decode(meta["salt"])
                iterations = int(meta.get("iterations", ENC_ITERATIONS))
                if not MIN_ENC_ITERATIONS <= iterations <= MAX_ENC_ITERATIONS:
                    return False
                key, _ = derive_key(password, salt, iterations=iterations)
                return verify_canary(meta["canary"], key)
            except (ValueError, TypeError, KeyError):
                return False
        if meta is None:
            # Unreadable owner.lock: fail closed, no legacy fallback.
            return False
        return self._verify_legacy(password)


class SessionManager:
    """Issue and verify opaque bearer tokens."""

    def __init__(self, ttl_seconds: int = 86_400):
        self._ttl = max(60, int(ttl_seconds))
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()
        self._bootstrap_token: str | None = None

    def issue(self, *, ttl: int | None = None) -> str:
        token = secrets.token_urlsafe(32)
        expiry = time.time() + (ttl if ttl is not None else self._ttl)
        with self._lock:
            self._tokens[token] = expiry
            self._bootstrap_token = token
        return token

    def revoke(self, token: str) -> bool:
        with self._lock:
            return self._tokens.pop(token, None) is not None

    def revoke_all(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._bootstrap_token = None

    def verify(self, token: Optional[str]) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            exp = self._tokens.get(token)
            if exp is None:
                return False
            if now > exp:
                del self._tokens[token]
                return False
            return True

    def active_count(self) -> int:
        now = time.time()
        with self._lock:
            dead = [t for t, exp in self._tokens.items() if now > exp]
            for t in dead:
                del self._tokens[t]
            return len(self._tokens)

    @property
    def latest_token(self) -> str | None:
        """Most recently issued token (for launcher→UI bootstrap)."""
        with self._lock:
            return self._bootstrap_token


def extract_bearer(authorization: str | None, x_token: str | None = None) -> str | None:
    """Parse Authorization: Bearer <token> or X-Bonsai-Token."""
    if x_token and x_token.strip():
        return x_token.strip()
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None
