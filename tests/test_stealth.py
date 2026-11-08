from __future__ import annotations

import pytest

from anonchains.stealth import (
    LEVELS,
    FingerprintEngine,
    effective_concurrency,
    get_policy,
)
from anonchains.stealth.ua_pool import UA_TEMPLATES, random_profile


def test_all_levels_exist_and_are_ordered():
    assert sorted(LEVELS) == [1, 2, 3, 4]
    assert get_policy(1).concurrency_cap > get_policy(4).concurrency_cap
    assert get_policy(1).max_delay_ms < get_policy(4).max_delay_ms
    with pytest.raises(ValueError):
        get_policy(9)


def test_effective_concurrency_is_capped():
    assert effective_concurrency(200, get_policy(4)) == get_policy(4).concurrency_cap
    assert effective_concurrency(2, get_policy(1)) == 2
    assert effective_concurrency(0, get_policy(2)) == 1


def test_header_consistency_and_ua_first():
    engine = FingerprintEngine(2, seed=7)
    for _ in range(30):
        profile = engine.build()
        headers = profile.headers
        assert list(headers)[0] == "User-Agent"
        assert headers["User-Agent"] == profile.user_agent
        assert "Accept-Language" in headers
        assert "Sec-Fetch" not in "".join(headers) or "Sec-CH-UA" in headers


def test_low_level_uses_single_ua_high_level_varies():
    low_engine = FingerprintEngine(1, seed=1)
    high_engine = FingerprintEngine(4, seed=1)
    low = {low_engine.build().user_agent for _ in range(10)}
    high = {high_engine.build().user_agent for _ in range(40)}
    assert len(low) == 1
    assert len(high) > 1
    assert len(high) <= len(UA_TEMPLATES)


def test_weighted_profile_returns_coherent_locale():
    profile = random_profile()
    assert profile.user_agent
    assert profile.major > 0
    assert profile.locale in profile.accept_language or "en" in profile.accept_language


def test_session_rotation_and_dns_hints():
    engine = FingerprintEngine(4)
    for _ in range(engine.policy.session_rotation_requests):
        engine.build()
    assert engine.should_rotate_session()
    assert engine.dns_hardening_hints()
