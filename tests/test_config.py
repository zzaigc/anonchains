from __future__ import annotations

import pytest

from anonchains.config.schema import ConfigError


def test_defaults_load(config_copy):
    cfg = config_copy.load()
    assert cfg.chain.hops == 5
    assert cfg.chain.mode == "strict"
    assert cfg.gateway.host == "127.0.0.1"
    assert cfg.gateway.enabled is False
    assert cfg.crawler.stealth_level in (1, 2, 3, 4)


def test_set_value_preserves_comments(config_copy):
    original = config_copy.path.read_text(encoding="utf-8")
    cfg = config_copy.set_value("chain", "hops", 9)
    assert cfg.chain.hops == 9
    updated = config_copy.path.read_text(encoding="utf-8")
    assert "; Number of hops" in updated
    assert "hops = 9" in updated
    assert original != updated


def test_set_value_is_idempotent(config_copy):
    before = config_copy.path.read_text(encoding="utf-8")
    cfg = config_copy.set_value("gateway", "enabled", False)
    assert cfg.gateway.enabled is False
    assert config_copy.path.read_text(encoding="utf-8") == before


def test_set_value_rejects_unknown_option(config_copy):
    with pytest.raises(ConfigError, match="not found"):
        config_copy.set_value("gateway", "does_not_exist", "true")


def test_invalid_write_is_rejected_and_rolled_back(config_copy):
    before = config_copy.path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        config_copy.set_value("crawler", "stealth_level", 99)
    assert config_copy.path.read_text(encoding="utf-8") == before
    assert config_copy.config.crawler.stealth_level == 2


def test_env_override(monkeypatch, config_copy):
    monkeypatch.setenv("ANONCHAINS_CHAIN_HOPS", "11")
    cfg = config_copy.load(force=True)
    assert cfg.chain.hops == 11


def test_loopback_only_gateway(config_copy):
    with pytest.raises(ConfigError):
        config_copy.config.with_overrides(gateway={"host": "0.0.0.0"})


def test_no_hard_hop_limit(config_copy):
    cfg = config_copy.config.with_overrides(chain={"hops": 500})
    assert cfg.chain.hops == 500
    assert cfg.effective_connect_timeout() <= 120.0
