"""Encryption primitives.

``Vault`` lives in :mod:`anonchains.crypto.vault` and is deliberately NOT
re-exported here: importing it at package-init time would create a package
cycle (``vault`` imports ``cipher``/``kdf`` through the package). Import it
directly::

    from anonchains.crypto.vault import Vault
"""

from .cipher import (
    ALGO_AES_GCM,
    ALGO_CHACHA,
    HAS_CIPHER,
    AuthenticationFailed,
    CipherError,
    CipherUnavailable,
    decrypt,
    encrypt,
)
from .kdf import HAS_ARGON2, KDFParams, derive_key, resolve_kdf
from .redaction import MASK, redact, redact_mapping, wipe

__all__ = [
    "encrypt",
    "decrypt",
    "CipherError",
    "CipherUnavailable",
    "AuthenticationFailed",
    "ALGO_AES_GCM",
    "ALGO_CHACHA",
    "HAS_CIPHER",
    "HAS_ARGON2",
    "KDFParams",
    "derive_key",
    "resolve_kdf",
    "redact",
    "redact_mapping",
    "wipe",
    "MASK",
]
