"""Offline tests for the bandwidth / buffering optimisations."""

from __future__ import annotations

import asyncio
import random

import pytest

from anonchains.config.schema import ConfigError
from anonchains.fetcher.session import FetchSession
from anonchains.parser.base import SourceSpec
from anonchains.stealth import FingerprintEngine
from anonchains.stealth.ua_pool import consistent_headers, random_profile


def test_response_cap_follows_config(config_copy):
    cfg = config_copy.config.with_overrides(crawler={"max_response_mb": 0.5})
    assert FetchSession(cfg, FingerprintEngine(1))._max_bytes() == 512 * 1024


def test_response_cap_must_be_positive(config_copy):
    with pytest.raises(ConfigError):
        config_copy.config.with_overrides(crawler={"max_response_mb": 0})


def test_accept_encoding_stays_decompressible():
    profile = random_profile(1, rng=random.Random(7))
    encoding = consistent_headers(profile)["Accept-Encoding"]
    assert "gzip" in encoding and "deflate" in encoding and "br" not in encoding


def test_duplicate_inflight_downloads_are_coalesced(config_copy, monkeypatch):
    calls: list[str] = []

    async def fake_download(self, spec, headers, proxy, etag, impersonate):
        calls.append(spec.url)
        await asyncio.sleep(0.01)
        return (200, b"1.2.3.4:8080", None, False, None)

    monkeypatch.setattr(FetchSession, "_download", fake_download)
    session = FetchSession(config_copy.config, FingerprintEngine(1))
    spec = SourceSpec(name="dup", url="https://example.test/list.txt", kind="plain")

    async def scenario():
        async with session:
            return await asyncio.gather(*[session.fetch(spec) for _ in range(5)])

    results = asyncio.run(scenario())
    assert len(calls) == 1
    assert session._dedup_hits == 4
    assert all(r.status == 200 and r.payload == b"1.2.3.4:8080" for r in results)
