from __future__ import annotations

from anonchains.fetcher import Crawler, FetchResult, summarise
from anonchains.parser import SourceSpec
from anonchains.sources.registry import SourceRegistry
from anonchains.stealth import get_policy


def test_builtin_registry_covers_all_protocol_families(sources_file, config_copy):
    registry = SourceRegistry.load(sources_file)
    stats = registry.stats()
    assert stats["total"] >= 25
    assert stats["enabled"] >= 15
    assert all(s.url.startswith("https://") for s in registry.all_specs)

    hints = {s.protocol_hint for s in registry.all_specs}
    for protocol in ("vless", "vmess", "wireguard", "tuic", "hysteria2", "ss", "trojan", "socks5"):
        assert protocol in hints, f"missing built-in source for {protocol}"

    kinds = {s.kind for s in registry.all_specs}
    assert {"plain", "json", "subscription"} <= kinds


def test_registry_toggle_and_custom_upsert(sources_file):
    registry = SourceRegistry.load(sources_file)
    name = registry.all_specs[0].name
    spec = registry.by_name(name)
    assert spec is not None
    before = spec.enabled
    registry.toggle(name, not before)
    updated = registry.by_name(name)
    assert updated is not None and updated.enabled is not before

    custom = SourceSpec(name="unit-custom", url="https://example.test/a.txt", kind="plain")
    registry.upsert(custom)
    assert registry.by_name("unit-custom") is custom
    assert registry.stats()["total"] >= 26


def test_crawler_construction_and_summary(config_copy, sources_file, tmp_path):
    registry = SourceRegistry.load(sources_file)
    crawler = Crawler(
        config_copy.config,
        registry,
        etag_path=tmp_path / ".etags.json",
    )
    assert crawler.stealth_level == config_copy.config.crawler.stealth_level
    crawler.set_stealth_level(3)
    assert crawler.stealth_level == 3
    assert crawler.engine.policy is get_policy(3)

    results = [
        FetchResult(registry.all_specs[0], ok=True, nodes=[]),
        FetchResult(registry.all_specs[1], ok=False, error="timeout"),
        FetchResult(registry.all_specs[2], ok=True, not_modified=True),
        FetchResult(registry.all_specs[3], blocked=True, error="robots.txt"),
    ]
    summary = summarise(results)
    assert summary == {"sources": 4, "ok": 2, "failed": 1, "blocked": 1, "unchanged": 1, "nodes": 0}
