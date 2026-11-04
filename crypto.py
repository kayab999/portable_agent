#!/usr/bin/env python3
"""
crypto.py — Zero-dependency vault crypto for Bonsai Portable.

Construction:
  - Key derivation: PBKDF2-HMAC-SHA256
  - Cipher: HMAC-SHA256 CTR stream + XOR
  - Integrity: HMAC-SHA256 over version||nonce||ciphertext (Encrypt-then-MAC)

Wire format (v1, base64):
  version(1) || nonce(16) || ciphertext || mac(32)
Legacy format (v0, no MAC) is rejected by default and only decryptable with
``allow_legacy=True`` for one-time migration:
  nonce(16) || ciphertext
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import os
import struct

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
# OWASP 2023+ guidance for PBKDF2-HMAC-SHA256 is 600k iterations. The legacy
# value is kept only to read vaults created by rc.3 and older; those vaults are
# re-encrypted at the current cost on the first successful unlock.
ENC_ITERATIONS = 600_000
AUTH_ITERATIONS = 600_000
LEGACY_ENC_ITERATIONS = 100_000
MIN_ENC_ITERATIONS = 100_000
MAX_ENC_ITERATIONS = 2_000_000
NONCE_LEN = 16
MAC_LEN = 32
VERSION_V1 = 1
CANARY_PLAIN = "BONSAI_VAULT_OK"


def derive_key(
    password: str,
    salt: bytes | None = None,
    *,
    iterations: int = ENC_ITERATIONS,
) -> tuple[bytes, bytes]:
    """Derive a 256-bit encryption key from password via PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = os.urandom(NONCE_LEN)
    if not MIN_ENC_ITERATIONS <= int(iterations) <= MAX_ENC_ITERATIONS:
        raise ValueError("PBKDF2 iterations outside the accepted range")
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations), dklen=32
    )
    return key, salt


def _split_keys(key: bytes) -> tuple[bytes, bytes]:
    """Derive enc key and mac key from master key (HKDF-like expand)."""
    enc = _hmac.new(key, b"bonsai-enc-v1", "sha256").digest()
    mac = _hmac.new(key, b"bonsai-mac-v1", "sha256").digest()
    return enc, mac


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        block = _hmac.new(key, nonce + struct.pack(">Q", counter), "sha256").digest()
        out += block
        counter += 1
    return out[:length]


def encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt string. Returns base64(version || nonce || ct || mac)."""
    enc_key, mac_key = _split_keys(key)
    nonce = os.urandom(NONCE_LEN)
    pt_bytes = plaintext.encode("utf-8")
    ks = _keystream(enc_key, nonce, len(pt_bytes))
    ciphertext = bytes(a ^ b for a, b in zip(pt_bytes, ks, strict=True))
    header = bytes([VERSION_V1]) + nonce + ciphertext
    mac = _hmac.new(mac_key, header, "sha256").digest()
    return base64.b64encode(header + mac).decode("ascii")


def decrypt(
    ciphertext_b64: str,
    key: bytes,
    *,
    allow_legacy: bool = False,
) -> str:
    """Decrypt base64 payload. Raises ValueError on MAC/key/format failure.

    Legacy v0 (no MAC) is rejected by default. Pass allow_legacy=True only for
    one-time migration of pre-RC vaults.
    """
    try:
        data = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise ValueError("Invalid base64 data") from exc

    # v1: version || nonce || ct || mac
    if len(data) >= 1 + NONCE_LEN + MAC_LEN and data[0] == VERSION_V1:
        enc_key, mac_key = _split_keys(key)
        body, mac = data[:-MAC_LEN], data[-MAC_LEN:]
        expected = _hmac.new(mac_key, body, "sha256").digest()
        if not _hmac.compare_digest(mac, expected):
            raise ValueError("Integrity check failed — wrong key or tampered data")
        nonce = body[1 : 1 + NONCE_LEN]
        ciphertext = body[1 + NONCE_LEN :]
        ks = _keystream(enc_key, nonce, len(ciphertext))
        pt_bytes = bytes(a ^ b for a, b in zip(ciphertext, ks, strict=True))
        try:
            return pt_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Decryption failed — wrong key or corrupted data") from exc

    # Legacy v0: nonce || ciphertext (no MAC) — opt-in only
    if not allow_legacy:
        raise ValueError(
            "Legacy ciphertext (v0, no MAC) rejected. "
            "Re-encrypt with v1 or pass allow_legacy=True for migration."
        )
    if len(data) < NONCE_LEN:
        raise ValueError("Ciphertext too short")
    nonce = data[:NONCE_LEN]
    ciphertext = data[NONCE_LEN:]
    enc_key, _ = _split_keys(key)
    for trial_key in (enc_key, key):
        ks = _keystream(trial_key, nonce, len(ciphertext))
        pt_bytes = bytes(a ^ b for a, b in zip(ciphertext, ks, strict=True))
        try:
            return pt_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
    raise ValueError("Decryption failed — wrong key or corrupted data")


def encrypt_canary(key: bytes) -> str:
    return encrypt(CANARY_PLAIN, key)


def verify_canary(encrypted_canary: str, key: bytes) -> bool:
    try:
        return decrypt(encrypted_canary, key) == CANARY_PLAIN
    except ValueError:
        return False


def hash_password(
    password: str,
    salt: bytes | None = None,
    *,
    iterations: int = AUTH_ITERATIONS,
) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(NONCE_LEN)
    h = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations), dklen=32
    )
    return base64.b64encode(h).decode("ascii"), base64.b64encode(salt).decode("ascii")


def verify_password(
    password: str,
    stored_hash: str,
    stored_salt: str,
    *,
    iterations: int = AUTH_ITERATIONS,
) -> bool:
    try:
        salt = base64.b64decode(stored_salt)
    except Exception:
        return False
    h = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations), dklen=32
    )
    computed = base64.b64encode(h).decode("ascii")
    return _hmac.compare_digest(computed, stored_hash)
