"""Request-profile builder: header entropy, ordering and timing jitter.

Scope note: this reduces *client-side* fingerprint variance for legitimate
privacy-preserving collection of public lists. It does not defeat active
fingerprinting or provide anonymity by itself; see docs/07-stealth-levels.md.
"""

from __future__ import annotations

import asyncio
import importlib
import random
from dataclasses import dataclass, field
from typing import Any

from .levels import StealthPolicy, get_policy
from .ua_pool import consistent_headers, random_profile, random_referer

_curl_requests: Any = None
try:  # optional TLS fingerprint impersonation
    _curl_requests = importlib.import_module("curl_cffi.requests")
except ImportError:  # pragma: no cover - optional
    _curl_requests = None

HAS_CURL_CFFI: bool = _curl_requests is not None


@dataclass(frozen=True, slots=True)
class RequestProfile:
    headers: dict[str, str] = field(default_factory=dict)
    delay_seconds: float = 0.0
    impersonate: str | None = None
    user_agent: str = ""


def _impersonation_target(profile_family: str) -> str:
    return "firefox133" if profile_family == "firefox" else "chrome131"


class FingerprintEngine:
    """Stateless-per-call generator; safe to share across concurrent tasks."""

    def __init__(self, level: int | StealthPolicy = 2, *, seed: int | None = None) -> None:
        self.policy: StealthPolicy = level if isinstance(level, StealthPolicy) else get_policy(level)
        self._rng = random.Random(seed)
        self._counter = 0

    def build(self, *, extra_headers: dict[str, str] | None = None) -> RequestProfile:
        policy = self.policy
        browser = random_profile(policy.ua_pool_size, rng=self._rng)
        headers = consistent_headers(browser)
        if not policy.rotate_accept_language:
            headers["Accept-Language"] = "en-US,en;q=0.9"
        if policy.synthetic_referer:
            headers["Referer"] = random_referer(rng=self._rng)
        if extra_headers:
            headers.update(extra_headers)

        if policy.shuffle_header_order:
            # Keep User-Agent first: some servers reject requests where it is
            # buried, and moving it is not a meaningful entropy source.
            ua = headers.pop("User-Agent")
            items = list(headers.items())
            self._rng.shuffle(items)
            headers = {"User-Agent": ua, **dict(items)}

        delay = self._rng.uniform(policy.min_delay_ms, policy.max_delay_ms) / 1000.0
        impersonate = None
        if policy.tls_impersonation and HAS_CURL_CFFI:
            impersonate = _impersonation_target(browser.family)
        self._counter += 1
        return RequestProfile(headers, delay, impersonate, browser.user_agent)

    async def pause(self, profile: RequestProfile) -> None:
        if profile.delay_seconds > 0:
            await asyncio.sleep(profile.delay_seconds)

    def should_rotate_session(self) -> bool:
        limit = self.policy.session_rotation_requests
        return bool(limit and self._counter and self._counter % limit == 0)

    def dns_hardening_hints(self) -> tuple[str, ...]:
        """Operational guidance; actual enforcement lives in fetcher/session.py."""
        if not self.policy.avoid_dns_prefetch:
            return ()
        return (
            "Do not pre-resolve target hostnames locally when a front proxy is used.",
            "Prefer HTTP/HTTPS front proxies so DNS resolution happens proxy-side.",
            "Disable direct DNS fallbacks and verify exit-IP consistency in validator.",
        )
