"""Encrypted vault for local static secrets.

Design: envelope encryption. A random 32-byte data encryption key (DEK)
encrypts all pool secrets and API tokens. The DEK itself is wrapped by a key
encryption key (KEK) derived from the user passphrase with a memory-hard KDF.
Changing the passphrase therefore only re-wraps the DEK -- existing pool rows
stay decryptable, which makes key rotation O(1) instead of O(pool size).

File layout::

    b"ANONVLT1" | header_len(4) | header_json | wrapped_len(4) | wrapped_dek
                | verifier_len(4) | verifier

``MAGIC + header_json`` is bound as AAD to both the wrapped DEK and the
verifier, so header tampering is detected on unlock.

Degradation: when the Vault is disabled, uninitialised or locked,
``seal_dict`` returns ``None`` and callers MUST skip persisting sensitive
fields. Secrets are never silently written in plaintext.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any

from ..utils.paths import display_path
from . import cipher, kdf
from .redaction import constant_time_eq, wipe

log = logging.getLogger(__name__)

MAGIC = b"ANONVLT1"
VERIFIER_PLAINTEXT = b"anonchains-vault-verifier-v1"
VERSION = 1


class VaultState(str, Enum):
    UNINITIALISED = "uninitialised"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class VaultError(RuntimeError):
    pass


class VaultLocked(VaultError):
    pass


class VaultAuthFailed(VaultError):
    pass


def _u32(value: int) -> bytes:
    return value.to_bytes(4, "big")


def _read_u32(blob: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(blob):
        raise VaultError("vault file truncated")
    return int.from_bytes(blob[offset : offset + 4], "big"), offset + 4


class Vault:
    """Thread-safe-enough (single-process) vault with a key TTL cache."""

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        kdf_preference: str = "auto",
        algo: str = cipher.ALGO_AES_GCM,
        key_ttl_seconds: int = 900,
        base_dir: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.base_dir = Path(base_dir) if base_dir else None
        self.enabled = enabled
        self.kdf_preference = kdf_preference
        self.algo = algo
        self.key_ttl_seconds = max(0, int(key_ttl_seconds))
        self._dek: bytearray | None = None
        self._unlocked_at: float = 0.0
        self._header: dict[str, Any] | None = None

    # --- state --------------------------------------------------------

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def state(self) -> VaultState:
        if not self.enabled:
            return VaultState.DISABLED
        if not cipher.HAS_CIPHER:
            return VaultState.UNAVAILABLE
        if not self.exists:
            return VaultState.UNINITIALISED
        if self._dek is None:
            return VaultState.LOCKED
        if self.key_ttl_seconds and time.monotonic() - self._unlocked_at > self.key_ttl_seconds:
            return VaultState.LOCKED
        return VaultState.UNLOCKED

    @property
    def is_unlocked(self) -> bool:
        return self.state == VaultState.UNLOCKED

    def status(self) -> dict[str, Any]:
        state = self.state
        header = self._header or {}
        return {
            "state": state.value,
            "exists": self.exists,
            "path": display_path(self.path, self.base_dir),
            "kdf": header.get("name", kdf.resolve_kdf(self.kdf_preference)),
            "cipher": header.get("cipher", self.algo),
            "created_at": header.get("created_at"),
            "key_ttl_seconds": self.key_ttl_seconds,
            "unlocked_for_seconds": (
                round(time.monotonic() - self._unlocked_at, 1) if self.is_unlocked else 0.0
            ),
            "available_kdfs": list(kdf.available_kdfs()),
        }

    # --- lifecycle ----------------------------------------------------

    def initialize(self, passphrase: str) -> None:
        """Create a brand-new vault. Overwriting an existing one is refused."""
        if not self.enabled:
            raise VaultError("vault is disabled by configuration")
        if not cipher.HAS_CIPHER:
            raise VaultError(
                "cryptography is missing; run: python -m pip install -r requirements.txt"
            )
        if self.exists:
            raise VaultError("vault already exists; use rotate_key() instead")
        if len(passphrase) < 8:
            raise VaultError("passphrase must be at least 8 characters")
        name = kdf.resolve_kdf(self.kdf_preference)
        params = kdf.KDFParams(name=name)
        salt = kdf.generate_salt()
        dek = os.urandom(cipher.KEY_LEN)
        header = self._make_header(params, salt)
        header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        context = MAGIC + header_bytes
        kek = kdf.derive_key(passphrase, salt, params)
        try:
            wrapped = cipher.encrypt(kek, dek, aad=context, algo=self.algo)
            verifier = cipher.encrypt(dek, VERIFIER_PLAINTEXT, aad=context, algo=self.algo)
        finally:
            wipe(bytearray(kek))
        self._write(context, wrapped, verifier)
        self._dek = bytearray(dek)
        self._header = header
        self._unlocked_at = time.monotonic()
        log.info("vault created at %s (kdf=%s, cipher=%s)",
                 display_path(self.path, self.base_dir), name, self.algo)

    def unlock(self, passphrase: str) -> None:
        """Derive the KEK, unwrap the DEK and verify the vault header."""
        if not self.enabled:
            raise VaultLocked("vault is disabled by configuration")
        if not self.exists:
            raise VaultLocked("vault is not initialised")
        context, wrapped, verifier = self._read()
        header = json.loads(context[len(MAGIC) :])
        params = kdf.KDFParams.from_dict(header)
        try:
            salt = bytes.fromhex(header["salt"])
        except (KeyError, ValueError) as exc:
            raise VaultError("vault header has an invalid salt") from exc
        kek = kdf.derive_key(passphrase, salt, params)
        dek: bytes | None = None
        try:
            try:
                dek = cipher.decrypt(kek, wrapped, aad=context)
            except cipher.CipherError as exc:
                raise VaultAuthFailed("wrong passphrase or corrupt vault") from exc
            try:
                plain = cipher.decrypt(dek, verifier, aad=context)
            except cipher.CipherError as exc:
                raise VaultError("vault verifier failed") from exc
            if not constant_time_eq(plain, VERIFIER_PLAINTEXT):
                raise VaultError("vault verifier mismatch")
        finally:
            wipe(bytearray(kek))
        assert dek is not None
        self.algo = header.get("cipher", self.algo)
        self._dek = bytearray(dek)
        self._header = header
        self._unlocked_at = time.monotonic()
        log.info("vault unlocked (kdf=%s)", params.name)

    def lock(self) -> None:
        wipe(self._dek)
        self._dek = None
        self._unlocked_at = 0.0

    def rotate_key(self, new_passphrase: str) -> None:
        """Change the passphrase without re-encrypting any stored data."""
        self._ensure_fresh()
        if len(new_passphrase) < 8:
            raise VaultError("passphrase must be at least 8 characters")
        old_header = self._header or {}
        name = kdf.resolve_kdf(self.kdf_preference)
        params = kdf.KDFParams(name=name)
        salt = kdf.generate_salt()
        header = self._make_header(params, salt)
        header["created_at"] = old_header.get("created_at", header["created_at"])
        header["rotated_at"] = time.time()
        header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        context = MAGIC + header_bytes
        kek = kdf.derive_key(new_passphrase, salt, params)
        try:
            wrapped = cipher.encrypt(kek, bytes(self._dek or b""), aad=context, algo=self.algo)
            verifier = cipher.encrypt(
                bytes(self._dek or b""), VERIFIER_PLAINTEXT, aad=context, algo=self.algo
            )
        finally:
            wipe(bytearray(kek))
        self._write(context, wrapped, verifier)
        self._header = header
        self._unlocked_at = time.monotonic()
        log.info("vault passphrase rotated (data key unchanged)")

    # --- data operations ---------------------------------------------

    def _ensure_fresh(self) -> None:
        state = self.state
        if state is VaultState.UNLOCKED:
            return
        if state is VaultState.LOCKED and self._dek is not None:
            self.lock()  # TTL expired -> drop the cached key
        raise VaultLocked(f"vault is {state.value}; unlock it first")

    def encrypt(self, plaintext: bytes, *, aad: bytes | None = None) -> bytes:
        self._ensure_fresh()
        return cipher.encrypt(bytes(self._dek or b""), plaintext, aad=aad, algo=self.algo)

    def decrypt(self, blob: bytes, *, aad: bytes | None = None) -> bytes:
        self._ensure_fresh()
        return cipher.decrypt(bytes(self._dek or b""), blob, aad=aad)

    def seal_dict(self, value: dict[str, Any], *, aad: bytes | None = None) -> str | None:
        """Encrypt a mapping into a DB-friendly string; ``None`` if unavailable."""
        if not self.is_unlocked:
            return None
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        blob = self.encrypt(payload, aad=aad)
        return base64.urlsafe_b64encode(blob).decode("ascii")

    def open_dict(self, token: str | None, *, aad: bytes | None = None) -> dict[str, Any] | None:
        """Inverse of :meth:`seal_dict`. Returns ``None`` on any failure."""
        if not token or not self.is_unlocked:
            return None
        try:
            plain = self.decrypt(base64.urlsafe_b64decode(token.encode("ascii")), aad=aad)
            data = json.loads(plain)
            return data if isinstance(data, dict) else None
        except (ValueError, cipher.CipherError, VaultError, UnicodeError) as exc:
            log.warning("could not open sealed field: %s", type(exc).__name__)
            return None

    # --- file I/O -----------------------------------------------------

    def _make_header(self, params: kdf.KDFParams, salt: bytes) -> dict[str, Any]:
        header: dict[str, Any] = {
            "version": VERSION,
            "created_at": time.time(),
            "cipher": self.algo,
            "salt": salt.hex(),
        }
        header.update(params.to_dict())
        return header

    def _write(self, context: bytes, wrapped: bytes, verifier: bytes) -> None:
        header_bytes = context[len(MAGIC) :]
        blob = (
            MAGIC
            + _u32(len(header_bytes))
            + header_bytes
            + _u32(len(wrapped))
            + wrapped
            + _u32(len(verifier))
            + verifier
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_bytes(blob)
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:  # pragma: no cover - best effort
                    pass

    def _read(self) -> tuple[bytes, bytes, bytes]:
        try:
            blob = self.path.read_bytes()
        except OSError as exc:
            raise VaultError(f"cannot read vault: {exc}") from exc
        if not blob.startswith(MAGIC):
            raise VaultError("not an anonchains vault file")
        offset = len(MAGIC)
        header_len, offset = _read_u32(blob, offset)
        header_bytes = blob[offset : offset + header_len]
        offset += header_len
        if len(header_bytes) != header_len:
            raise VaultError("vault header truncated")
        wrapped_len, offset = _read_u32(blob, offset)
        wrapped = blob[offset : offset + wrapped_len]
        offset += wrapped_len
        if len(wrapped) != wrapped_len:
            raise VaultError("wrapped key truncated")
        verifier_len, offset = _read_u32(blob, offset)
        verifier = blob[offset : offset + verifier_len]
        if len(verifier) != verifier_len:
            raise VaultError("verifier truncated")
        context = MAGIC + header_bytes
        try:
            header = json.loads(header_bytes)
        except (ValueError, UnicodeError) as exc:
            raise VaultError("vault header is not valid JSON") from exc
        if header.get("version") != VERSION:
            raise VaultError(f"unsupported vault version: {header.get('version')}")
        return context, wrapped, verifier
