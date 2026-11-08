"""Authenticated encryption envelope.

Blob layout (all lengths big-endian)::

    b"ANONC1" | algo_id(1) | nonce(12) | ciphertext + tag(16)

The header bytes are bound as AAD so an attacker cannot swap algorithms or
nonce without detection. AES-256-GCM is the default; ChaCha20-Poly1305 is
available as an alternative. Both are IETF-standard AEADs from ``cryptography``.

Note: ``cryptography`` exposes RFC 8439 ChaCha20-Poly1305 (96-bit nonce), not
the XChaCha20 extended-nonce variant. We name it accurately rather than
mislabelling it; see docs/08-security-and-vault.md.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Final

# Dynamic import keeps the module importable when the optional cryptography
# wheel is absent; callers get a precise CipherUnavailable instead.
_aead_mod: Any = None
_invalid_tag: type[BaseException] = Exception
try:
    _aead_mod = importlib.import_module("cryptography.hazmat.primitives.ciphers.aead")
    _invalid_tag = importlib.import_module("cryptography.exceptions").InvalidTag
except ImportError:  # pragma: no cover - depends on environment
    _aead_mod = None

HAS_CIPHER: bool = _aead_mod is not None

MAGIC: Final = b"ANONC1"
ALGO_AES_GCM: Final = "aes-256-gcm"
ALGO_CHACHA: Final = "chacha20-poly1305"
_ALGO_TO_ID = {ALGO_AES_GCM: 1, ALGO_CHACHA: 2}
_ID_TO_ALGO = {v: k for k, v in _ALGO_TO_ID.items()}
NONCE_LEN = 12
KEY_LEN = 32
HEADER_LEN = len(MAGIC) + 1 + NONCE_LEN


class CipherError(RuntimeError):
    pass


class CipherUnavailable(CipherError):
    pass


class AuthenticationFailed(CipherError):
    pass


def supported_algos() -> tuple[str, ...]:
    return (ALGO_AES_GCM, ALGO_CHACHA) if HAS_CIPHER else ()


def _aead(algo: str, key: bytes) -> Any:
    if _aead_mod is None:
        raise CipherUnavailable(
            "cryptography is missing; install requirements.txt to enable the Vault"
        )
    if len(key) != KEY_LEN:
        raise CipherError("encryption key must be 32 bytes")
    if algo == ALGO_AES_GCM:
        return _aead_mod.AESGCM(key)
    if algo == ALGO_CHACHA:
        return _aead_mod.ChaCha20Poly1305(key)
    raise CipherError(f"unsupported algorithm: {algo}")


def encrypt(
    key: bytes,
    plaintext: bytes,
    *,
    aad: bytes | None = None,
    algo: str = ALGO_AES_GCM,
) -> bytes:
    algo_id = _ALGO_TO_ID.get(algo)
    if algo_id is None:
        raise CipherError(f"unsupported algorithm: {algo}")
    nonce = os.urandom(NONCE_LEN)
    header = MAGIC + bytes([algo_id]) + nonce
    aead = _aead(algo, key)
    bound = header + (aad or b"")
    return header + aead.encrypt(nonce, plaintext, bound)


def decrypt(key: bytes, blob: bytes, *, aad: bytes | None = None) -> bytes:
    if len(blob) < HEADER_LEN + 16:
        raise CipherError("ciphertext too short or truncated")
    if blob[: len(MAGIC)] != MAGIC:
        raise CipherError("not an anonchains ciphertext")
    algo_id = blob[len(MAGIC)]
    algo = _ID_TO_ALGO.get(algo_id)
    if algo is None:
        raise CipherError(f"unknown algorithm id: {algo_id}")
    header = blob[:HEADER_LEN]
    nonce = blob[len(MAGIC) + 1 : HEADER_LEN]
    ciphertext = blob[HEADER_LEN:]
    aead = _aead(algo, key)
    try:
        return aead.decrypt(nonce, ciphertext, header + (aad or b""))
    except Exception as exc:
        if isinstance(exc, CipherError):
            raise
        if isinstance(exc, _invalid_tag):
            raise AuthenticationFailed(
                "ciphertext authentication failed (tampering or wrong key)"
            ) from exc
        raise CipherError(f"decryption failed: {type(exc).__name__}") from exc
