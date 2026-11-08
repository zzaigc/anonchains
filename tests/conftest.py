"""Shared offline fixtures. No network access is required by any test."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from anonchains.config.loader import ConfigManager, default_config_path, project_root


@pytest.fixture(scope="session")
def project_dir() -> Path:
    return project_root()


@pytest.fixture(scope="session")
def sources_file() -> Path:
    return default_config_path().parent / "sources.yaml"


@pytest.fixture
def config_copy(tmp_path: Path) -> ConfigManager:
    """A private copy of the shipped config so write-back tests never mutate it."""
    target = tmp_path / "anonchains.conf"
    shutil.copyfile(default_config_path(), target)
    return ConfigManager(target, tmp_path)


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "pools" / ".vault"
