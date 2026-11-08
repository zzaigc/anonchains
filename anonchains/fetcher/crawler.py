"""Concurrent, rate-limited crawl pipeline with incremental (ETag) fetching."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config.schema import AppConfig
from ..models.proxy_node import ProxyNode
from ..parser.base import SourceSpec
from ..parser.detector import parse_payload
from ..sources.registry import SourceRegistry
from ..stealth.fingerprint import FingerprintEngine
from ..stealth.levels import effective_concurrency, get_policy
from ..utils.concurrency import RateLimiter, backoff_delay, bounded_map
from .session import FetchResponse, FetchSession, FrontProxyProvider

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchResult:
    spec: SourceSpec
    ok: bool = False
    nodes: list[ProxyNode] = field(default_factory=list)
    status: int = 0
    error: str | None = None
    elapsed_ms: float = 0.0
    not_modified: bool = False
    blocked: bool = False
    front_used: str | None = None
    parser: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.spec.name,
            "ok": self.ok,
            "nodes": len(self.nodes),
            "status": self.status,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "not_modified": self.not_modified,
            "blocked": self.blocked,
            "front_used": self.front_used,
        }


class Crawler:
    """Fetch + parse a batch of sources concurrently.

    ``on_result`` is invoked as each source completes, letting the CLI stream
    progress (and the pool writer persist nodes) without waiting for the slow
    source in the batch.
    """

    def __init__(
        self,
        config: AppConfig,
        registry: SourceRegistry,
        *,
        engine: FingerprintEngine | None = None,
        front: FrontProxyProvider | None = None,
        etag_path: Path | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.engine = engine or FingerprintEngine(config.crawler.stealth_level)
        self.front = front
        self.etag_path = Path(etag_path or config.pools.candidate_dir.parent / ".etags.json")
        self._etags: dict[str, str] = self._load_etags()

    def _load_etags(self) -> dict[str, str]:
        try:
            data = json.loads(self.etag_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_etags(self) -> None:
        try:
            self.etag_path.parent.mkdir(parents=True, exist_ok=True)
            self.etag_path.write_text(json.dumps(self._etags, indent=2), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - filesystem dependent
            log.debug("could not persist ETag cache: %s", exc)

    @property
    def stealth_level(self) -> int:
        return self.engine.policy.level

    def set_stealth_level(self, level: int) -> None:
        self.engine = FingerprintEngine(get_policy(level))

    async def run(
        self,
        specs: list[SourceSpec] | None = None,
        *,
        on_result: Callable[[FetchResult], Any] | None = None,
    ) -> list[FetchResult]:
        targets = specs if specs is not None else self.registry.enabled_specs
        if not targets:
            log.warning("no enabled sources to crawl")
            return []
        limiter = RateLimiter(self.config.crawler.rate_limit_per_second)
        limit = effective_concurrency(self.config.crawler.concurrency, self.engine.policy)
        log.info(
            "crawl started: %d sources, concurrency=%d, stealth=%d, rate=%.1f/s",
            len(targets), limit, self.engine.policy.level, self.config.crawler.rate_limit_per_second,
        )
        started = time.monotonic()
        async with FetchSession(self.config, self.engine, self.front) as session:

            async def worker(spec: SourceSpec) -> FetchResult:
                async with limiter:
                    result = await self._one(session, spec)
                if on_result is not None:
                    try:
                        outcome = on_result(result)
                        if asyncio.iscoroutine(outcome):
                            await outcome
                    except Exception as exc:  # pragma: no cover - callback isolation
                        log.warning("crawl result callback failed: %s", type(exc).__name__)
                return result

            raw = await bounded_map(targets, worker, limit)
            results: list[FetchResult] = []
            for index, item in enumerate(raw):
                if isinstance(item, BaseException):
                    results.append(
                        FetchResult(targets[index], ok=False, error=f"{type(item).__name__}: {item}")
                    )
                else:
                    results.append(item)
        self._save_etags()
        total = sum(len(r.nodes) for r in results)
        log.info(
            "crawl finished in %.1fs: %d/%d sources ok, %d nodes",
            time.monotonic() - started,
            sum(1 for r in results if r.ok),
            len(results),
            total,
        )
        return results

    async def _one(self, session: FetchSession, spec: SourceSpec) -> FetchResult:
        retries = max(0, self.config.crawler.retries)
        last_error: str | None = None
        for attempt in range(retries + 1):
            response: FetchResponse = await session.fetch(spec, etag=self._etags.get(spec.name))
            if response.blocked:
                return FetchResult(
                    spec, ok=False, blocked=True, error=response.error, elapsed_ms=response.elapsed_ms
                )
            if response.error is not None:
                last_error = response.error
                if attempt < retries:
                    await asyncio.sleep(backoff_delay(attempt))
                    continue
                return FetchResult(
                    spec,
                    ok=False,
                    status=response.status,
                    error=last_error,
                    elapsed_ms=response.elapsed_ms,
                    front_used=response.front_used,
                )
            if response.not_modified:
                return FetchResult(
                    spec, ok=True, not_modified=True, status=304,
                    elapsed_ms=response.elapsed_ms, front_used=response.front_used,
                )
            if response.etag:
                self._etags[spec.name] = response.etag
            try:
                # Parsing large lists is CPU-bound; keep the event loop free.
                nodes = await asyncio.to_thread(parse_payload, response.payload, spec)
            except Exception as exc:
                return FetchResult(
                    spec, ok=False, error=f"parse failed: {type(exc).__name__}: {exc}",
                    status=response.status, elapsed_ms=response.elapsed_ms,
                )
            return FetchResult(
                spec,
                ok=True,
                nodes=nodes,
                status=response.status,
                elapsed_ms=response.elapsed_ms,
                front_used=response.front_used,
            )
        return FetchResult(spec, ok=False, error=last_error or "unknown crawl failure")


def summarise(results: list[FetchResult]) -> dict[str, Any]:
    return {
        "sources": len(results),
        "ok": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok and not r.blocked),
        "blocked": sum(1 for r in results if r.blocked),
        "unchanged": sum(1 for r in results if r.not_modified),
        "nodes": sum(len(r.nodes) for r in results),
    }
