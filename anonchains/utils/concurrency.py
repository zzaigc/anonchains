"""Bounded-concurrency helpers shared by the fetcher, validator and scheduler."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def chunks(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    """Yield slices of at most ``size`` items without copying the sequence."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def jittered(delay: float, ratio: float = 0.3) -> float:
    """Apply symmetric multiplicative jitter, keeping the mean unchanged."""
    if delay <= 0:
        return 0.0
    return max(0.0, delay * (1.0 + random.uniform(-ratio, ratio)))


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with full jitter, capped."""
    return jittered(min(cap, base * (2**max(0, attempt))), 0.25)


async def bounded_map(
    items: Iterable[T],
    worker: Callable[[T], Awaitable[R]],
    limit: int,
    *,
    return_exceptions: bool = True,
) -> list[R | BaseException]:
    """Run ``worker`` over ``items`` with an ``asyncio.Semaphore``.

    Always preserves input order. With ``return_exceptions=True`` individual
    failures are returned as values instead of cancelling the whole batch,
    which keeps a single dead proxy from aborting a validation sweep.
    """
    if limit <= 0:
        raise ValueError("concurrency limit must be positive")
    semaphore = asyncio.Semaphore(limit)

    async def run(item: T) -> R:
        async with semaphore:
            return await worker(item)

    tasks = [asyncio.ensure_future(run(item)) for item in items]
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks, return_exceptions=return_exceptions))


class RateLimiter:
    """Async token-bucket limiter, usable as ``async with limiter:``."""

    def __init__(self, rate_per_second: float, burst: int | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate_per_second)
        self.capacity = float(burst or max(1, int(rate_per_second)))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            await asyncio.sleep(max(0.001, wait))

    async def __aenter__(self) -> RateLimiter:
        await self.acquire()
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def cancel_all(tasks: Iterable[asyncio.Task[Any]]) -> None:
    """Cancel and drain a set of background tasks without leaking warnings."""
    pending = [t for t in tasks if not t.done()]
    for task in pending:
        _ = task.cancel()
    if pending:
        _ = await asyncio.gather(*pending, return_exceptions=True)
