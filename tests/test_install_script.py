"""Offline tests for scripts/install_singbox.py (pure helpers only)."""

from __future__ import annotations

import importlib.util
import tarfile
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_singbox.py"


@pytest.fixture(scope="module")
def installer():
    spec = importlib.util.spec_from_file_location("install_singbox", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_tag(installer):
    assert installer.normalize_tag("1.14.4") == "v1.14.4"
    assert installer.normalize_tag(" v1.14.4 ") == "v1.14.4"
    assert installer.normalize_tag("") == ""


def test_detect_platform_arch(installer):
    assert installer.detect_platform() in {"windows", "linux", "darwin"}
    assert installer.detect_arch() in {"amd64", "arm64", "386", "armv7", "armv6", "riscv64"}


def test_asset_info(installer):
    name, url = installer.asset_info("v1.14.4", "windows", "amd64")
    assert name == "sing-box-1.14.4-windows-amd64.zip"
    assert url.endswith("/sing-box-1.14.4-windows-amd64.zip")
    name, url = installer.asset_info("v1.14.4", "linux", "arm64")
    assert name == "sing-box-1.14.4-linux-arm64.tar.gz"


def test_archive_integrity(installer, tmp_path):
    payload = tmp_path / "bin"
    payload.write_bytes(b"fake-binary")

    good_zip = tmp_path / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as zf:
        zf.write(payload, "sing-box.exe")
    assert installer.archive_integrity(good_zip, expected=0) is True
    assert installer.archive_integrity(good_zip, expected=10**9) is False  # size gate

    good_tar = tmp_path / "good.tar.gz"
    with tarfile.open(good_tar, "w:gz") as tf:
        tf.add(payload, arcname="sing-box")
    assert installer.archive_integrity(good_tar, expected=0) is True

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not-a-zip")
    assert installer.archive_integrity(bad, expected=0) is False
    assert installer.archive_integrity(tmp_path / "missing.zip", expected=0) is False


def test_install_script_exists_and_is_ignored():
    assert _SCRIPT.is_file()
    gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
    text = gitignore.read_text(encoding="utf-8")
    assert "vendor/" in text  # installed binaries must never be committed
