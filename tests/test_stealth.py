from __future__ import annotations

import pytest

from anonchains.stealth import (
    LEVELS,
    FingerprintEngine,
    effective_concurrency,
    get_policy,
)
from anonchains.stealth.ua_pool import BrowserProfile, UA_TEMPLATES, consistent_headers, random_profile


def test_ua_pool_covers_major_browsers():
    families = {t[2] for t in UA_TEMPLATES}
    assert {"chrome", "edge", "firefox", "safari", "opera"} <= families
    platforms: set[str] = set()
    for ua, _w, _f, _m in UA_TEMPLATES:
        for token in ("Windows", "Macintosh", "X11", "Android", "iPhone"):
            if token in ua:
                platforms.add(token)
    assert {"Windows", "Macintosh", "X11"} <= platforms
    # Level-2 pool (first 12, ordered by weight) already spans all families.
    assert {t[2] for t in UA_TEMPLATES[:12]} >= {"chrome", "edge", "firefox", "safari", "opera"}


def test_headers_agree_with_user_agent_platform():
    for ua, _w, family, _major in UA_TEMPLATES:
        browser = BrowserProfile(ua, family, _major, "en-US", "en-US,en;q=0.9")
        headers = consistent_headers(browser)
        if family in {"chrome", "edge", "opera"}:
            platform = headers["Sec-CH-UA-Platform"].strip('"')
            expected = ("Android" if "Android" in ua else
                        "iOS" if "iPhone" in ua else
                        "Windows" if "Windows" in ua else
                        "macOS" if "Macintosh" in ua else "Linux")
            assert platform == expected, ua
            assert headers["Sec-CH-UA-Mobile"] == ("?1" if expected in {"Android", "iOS"} else "?0")
        else:
            assert "Sec-CH-UA" not in headers  # Safari/Firefox do not send client hints


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
