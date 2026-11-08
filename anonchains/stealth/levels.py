"""Centralised stealth-level policy table.

All stealth knobs live here so the CLI can switch levels with a single number
and no scattered ``if level == ...`` branches elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StealthPolicy:
    level: int
    name: str
    description: str
    ua_pool_size: int
    shuffle_header_order: bool
    rotate_accept_language: bool
    synthetic_referer: bool
    min_delay_ms: int
    max_delay_ms: int
    concurrency_cap: int
    retries: int
    session_rotation_requests: int
    tls_impersonation: bool
    avoid_dns_prefetch: bool


LEVELS: dict[int, StealthPolicy] = {
    1: StealthPolicy(
        level=1,
        name="Low",
        description="单一固定 UA，几乎无延迟，最大吞吐；适合本地/离线测试",
        ua_pool_size=1,
        shuffle_header_order=False,
        rotate_accept_language=False,
        synthetic_referer=False,
        min_delay_ms=0,
        max_delay_ms=0,
        concurrency_cap=64,
        retries=1,
        session_rotation_requests=0,
        tls_impersonation=False,
        avoid_dns_prefetch=False,
    ),
    2: StealthPolicy(
        level=2,
        name="Balanced",
        description="默认档：加权 UA 池、一致性请求头、轻微抖动与限速",
        ua_pool_size=12,
        shuffle_header_order=True,
        rotate_accept_language=True,
        synthetic_referer=True,
        min_delay_ms=80,
        max_delay_ms=400,
        concurrency_cap=32,
        retries=2,
        session_rotation_requests=200,
        tls_impersonation=False,
        avoid_dns_prefetch=True,
    ),
    3: StealthPolicy(
        level=3,
        name="Cautious",
        description="大 UA 池、低并发、明显时序抖动、更频繁的连接重建",
        ua_pool_size=40,
        shuffle_header_order=True,
        rotate_accept_language=True,
        synthetic_referer=True,
        min_delay_ms=400,
        max_delay_ms=1600,
        concurrency_cap=8,
        retries=3,
        session_rotation_requests=40,
        tls_impersonation=False,
        avoid_dns_prefetch=True,
    ),
    4: StealthPolicy(
        level=4,
        name="Maximum",
        description="最低并发、最长抖动、TLS 指纹伪装（需 curl_cffi）、每少数请求换连接",
        ua_pool_size=80,
        shuffle_header_order=True,
        rotate_accept_language=True,
        synthetic_referer=True,
        min_delay_ms=1200,
        max_delay_ms=5000,
        concurrency_cap=3,
        retries=3,
        session_rotation_requests=8,
        tls_impersonation=True,
        avoid_dns_prefetch=True,
    ),
}


def get_policy(level: int) -> StealthPolicy:
    try:
        return LEVELS[int(level)]
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"stealth level must be one of {sorted(LEVELS)}") from exc


def choices() -> list[tuple[int, str, str]]:
    """CLI-friendly ``(number, name, description)`` rows."""
    return [(p.level, p.name, p.description) for p in LEVELS.values()]


def effective_concurrency(configured: int, policy: StealthPolicy) -> int:
    """Never exceed the policy cap even if the user configured a huge value."""
    return max(1, min(int(configured), policy.concurrency_cap))
