"""Memory-hard key derivation.

Argon2id (RFC 9106) is used when ``argon2-cffi`` is installed. Otherwise the
standard-library ``hashlib.scrypt`` provides an equivalent class of
memory-hard KDF. The chosen algorithm is recorded in every vault header so a
vault created on one machine stays readable on another.
"""

from __future__ import annotations

import hashlib
import importlib
import os
from dataclasses import dataclass
from typing import Any

# Resolved dynamically: argon2-cffi is optional, and static analysers must not
# assume the module exists in a minimal install.
_argon2: Any
try:
    _argon2 = importlib.import_module("argon2.low_level")
except ImportError:  # pragma: no cover - depends on environment
    _argon2 = None

HAS_ARGON2: bool = _argon2 is not None

KDF_ARGON2ID = "argon2id"
KDF_SCRYPT = "scrypt"
KDF_CHOICES = (KDF_ARGON2ID, KDF_SCRYPT)


class KDFError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KDFParams:
    name: str = KDF_SCRYPT
    time_cost: int = 3
    memory_cost: int = 65536  # KiB, Argon2id only
    parallelism: int = 4
    key_len: int = 32
    scrypt_n: int = 1 << 15
    scrypt_r: int = 8
    scrypt_p: int = 1

    def to_dict(self) -> dict[str, int | str]:
        return {
            "name": self.name,
            "time_cost": self.time_cost,
            "memory_cost": self.memory_cost,
            "parallelism": self.parallelism,
            "key_len": self.key_len,
            "scrypt_n": self.scrypt_n,
            "scrypt_r": self.scrypt_r,
            "scrypt_p": self.scrypt_p,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KDFParams:
        try:
            params = cls(
                name=str(data.get("name", KDF_SCRYPT)),
                time_cost=int(data.get("time_cost", 3)),
                memory_cost=int(data.get("memory_cost", 65536)),
                parallelism=int(data.get("parallelism", 4)),
                key_len=int(data.get("key_len", 32)),
                scrypt_n=int(data.get("scrypt_n", 1 << 15)),
                scrypt_r=int(data.get("scrypt_r", 8)),
                scrypt_p=int(data.get("scrypt_p", 1)),
            )
        except (TypeError, ValueError) as exc:
            raise KDFError(f"corrupt KDF parameters: {exc}") from exc
        if params.name not in KDF_CHOICES:
            raise KDFError(f"unsupported KDF: {params.name}")
        if not 16 <= params.key_len <= 64:
            raise KDFError("key_len must be in 16..64")
        if params.scrypt_n < (1 << 10) or params.scrypt_n > (1 << 20):
            raise KDFError("scrypt_n out of safe range")
        if params.time_cost < 1 or params.memory_cost < 1024 or params.parallelism < 1:
            raise KDFError("Argon2id parameters too weak or invalid")
        return params


def available_kdfs() -> tuple[str, ...]:
    return (KDF_ARGON2ID, KDF_SCRYPT) if HAS_ARGON2 else (KDF_SCRYPT,)


def resolve_kdf(preference: str) -> str:
    """Map ``auto`` (or an unavailable explicit choice) to a usable KDF."""
    if preference == KDF_ARGON2ID:
        if not HAS_ARGON2:
            raise KDFError(
                "argon2id requested but argon2-cffi is missing; install requirements-optional.txt or set vault.kdf = scrypt"
            )
        return KDF_ARGON2ID
    if preference == KDF_SCRYPT:
        return KDF_SCRYPT
    return KDF_ARGON2ID if HAS_ARGON2 else KDF_SCRYPT


def generate_salt(length: int = 16) -> bytes:
    if length < 16:
        raise KDFError("salt must be at least 16 bytes")
    return os.urandom(length)


def derive_key(passphrase: str, salt: bytes, params: KDFParams) -> bytes:
    """Derive ``params.key_len`` bytes. Never logs the passphrase or result."""
    if len(salt) < 16:
        raise KDFError("salt too short")
    secret = passphrase.encode("utf-8")
    try:
        if params.name == KDF_ARGON2ID:
            if _argon2 is None:
                raise KDFError("argon2-cffi unavailable")
            return bytes(
                _argon2.hash_secret_raw(
                    secret=secret,
                    salt=salt,
                    time_cost=params.time_cost,
                    memory_cost=params.memory_cost,
                    parallelism=params.parallelism,
                    hash_len=params.key_len,
                    type=_argon2.Type.ID,
                )
            )
        return hashlib.scrypt(
            secret,
            salt=salt,
            n=params.scrypt_n,
            r=params.scrypt_r,
            p=params.scrypt_p,
            dklen=params.key_len,
            maxmem=max(64 << 20, 128 * params.scrypt_n * params.scrypt_r * 2),
        )
    except (ValueError, MemoryError) as exc:
        raise KDFError(f"key derivation failed: {exc}") from exc
