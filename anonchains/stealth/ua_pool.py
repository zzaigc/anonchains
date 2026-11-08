"""Weighted real-browser User-Agent pool and coherent header templates.

The weights roughly follow desktop browser market share. Versions should be
refreshed periodically; see docs/07-stealth-levels.md. The pool is immutable
and shared, so no per-request allocation cost.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# (User-Agent, weight, family, major_version)
UA_TEMPLATES: tuple[tuple[str, int, str, int], ...] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        26, "chrome", 131,
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        16, "chrome", 130,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        12, "chrome", 131,
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        8, "chrome", 130,
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        10, "firefox", 133,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
        5, "firefox", 132,
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
        8, "safari", 18,
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/131.0.0.0 Safari/537.36",
        9, "edge", 131,
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        4, "chrome", 129,
    ),
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
        2, "safari", 18,
    ),
)

_LOCALES = (
    ("en-US,en;q=0.9", "en-US"),
    ("en-GB,en;q=0.9", "en-GB"),
    ("zh-CN,zh;q=0.9,en;q=0.8", "zh-CN"),
    ("de-DE,de;q=0.9,en;q=0.8", "de-DE"),
    ("ja-JP,ja;q=0.9,en;q=0.7", "ja-JP"),
    ("fr-FR,fr;q=0.9,en;q=0.8", "fr-FR"),
)

_REFERERS = (
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://github.com/",
)


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    user_agent: str
    family: str
    major: int
    locale: str
    accept_language: str


def pool(size: int | None = None) -> tuple[tuple[str, int, str, int], ...]:
    """Return the first ``size`` templates (already ordered by weight)."""
    if size is None or size >= len(UA_TEMPLATES):
        return UA_TEMPLATES
    return UA_TEMPLATES[: max(1, size)]


def random_profile(pool_size: int | None = None, *, rng: random.Random | None = None) -> BrowserProfile:
    """Pick a weighted UA and a matching locale."""
    chooser = rng or random
    templates = pool(pool_size)
    weights = [t[1] for t in templates]
    ua, _, family, major = chooser.choices(templates, weights=weights, k=1)[0]
    accept_language, locale = chooser.choice(_LOCALES)
    return BrowserProfile(ua, family, major, locale, accept_language)


def consistent_headers(profile: BrowserProfile) -> dict[str, str]:
    """Build headers that agree with the chosen UA (no contradictory hints)."""
    headers = {
        "User-Agent": profile.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": profile.accept_language,
        # aiohttp auto-decompresses gzip/deflate; advertising br without the
        # optional brotli wheel would return compressed bytes we cannot parse.
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if profile.family in {"chrome", "edge"}:
        headers["Sec-Fetch-Dest"] = "document"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Site"] = "none"
        headers["Sec-Fetch-User"] = "?1"
        brand = "Microsoft Edge" if profile.family == "edge" else "Google Chrome"
        headers["Sec-CH-UA"] = (
            f'"{brand}";v="{profile.major}", "Chromium";v="{profile.major}", "Not_A Brand";v="24"'
        )
        headers["Sec-CH-UA-Mobile"] = "?0"
        headers["Sec-CH-UA-Platform"] = '"Windows"'
    elif profile.family == "firefox":
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        headers["Upgrade-Insecure-Requests"] = "1"
    return headers


def random_referer(*, rng: random.Random | None = None) -> str:
    return (rng or random).choice(_REFERERS)
