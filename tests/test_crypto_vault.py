from __future__ import annotations

import pytest

from anonchains.crypto.cipher import HAS_CIPHER, AuthenticationFailed, encrypt, decrypt
from anonchains.crypto.kdf import KDFParams, derive_key, resolve_kdf
from anonchains.crypto.vault import (
    Vault,
    VaultAuthFailed,
    VaultLocked,
    VaultState,
)

pytestmark = pytest.mark.skipif(not HAS_CIPHER, reason="cryptography not installed")


def test_kdf_is_deterministic_and_length_correct():
    params = KDFParams(name=resolve_kdf("auto"))
    salt = b"0" * 16
    first = derive_key("correct horse battery staple", salt, params)
    second = derive_key("correct horse battery staple", salt, params)
    assert first == second and len(first) == params.key_len
    assert derive_key("different passphrase", salt, params) != first


def test_cipher_roundtrip_and_aad_binding():
    key = b"k" * 32
    blob = encrypt(key, b"top secret", aad=b"row-42")
    assert decrypt(key, blob, aad=b"row-42") == b"top secret"
    with pytest.raises(Exception):
        decrypt(key, blob, aad=b"other-row")
    corrupted = bytearray(blob)
    corrupted[-1] ^= 0x01
    with pytest.raises(AuthenticationFailed):
        decrypt(key, bytes(corrupted), aad=b"row-42")


def test_vault_lifecycle(vault_path):
    vault = Vault(vault_path)
    assert vault.state is VaultState.UNINITIALISED
    vault.initialize("a-strong-passphrase")
    assert vault.is_unlocked
    token = vault.seal_dict({"password": "hunter2"})
    assert token and vault.open_dict(token) == {"password": "hunter2"}
    vault.lock()
    assert vault.state is VaultState.LOCKED
    assert vault.seal_dict({"x": 1}) is None
    assert vault.open_dict(token) is None
    with pytest.raises(VaultLocked):
        vault.encrypt(b"data")
    vault.unlock("a-strong-passphrase")
    assert vault.open_dict(token) == {"password": "hunter2"}


def test_vault_wrong_passphrase(vault_path):
    vault = Vault(vault_path)
    vault.initialize("first-passphrase")
    vault.lock()
    with pytest.raises(VaultAuthFailed):
        vault.unlock("wrong-passphrase")
    assert not vault.is_unlocked


def test_key_rotation_keeps_existing_ciphertext(vault_path):
    vault = Vault(vault_path)
    vault.initialize("old-passphrase")
    token = vault.seal_dict({"token": "abc"})
    vault.rotate_key("new-passphrase")
    assert vault.open_dict(token) == {"token": "abc"}
    vault.lock()
    vault.unlock("new-passphrase")
    assert vault.open_dict(token) == {"token": "abc"}


def test_disabled_vault_degrades_safely(vault_path):
    vault = Vault(vault_path, enabled=False)
    assert vault.state is VaultState.DISABLED
    assert vault.seal_dict({"secret": 1}) is None
    with pytest.raises(VaultLocked):
        vault.unlock("anything")
