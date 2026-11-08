"""Periodic closed loop: crawl -> candidate pool -> validate -> verified pool.

The scheduler owns no state machines of its own; it orchestrates the existing
components and publishes a :class:`CycleReport` after every cycle so the CLI
and REST gateway can display progress.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import AppConfig
from ..fetcher.crawler import Crawler, summarise
from ..models.proxy_node import Anonymity, ProxyNode
from ..pools.candidate_pool import CandidatePool
from ..pools.verified_pool import VerifiedPool
from ..validator.batch import Validator, ValidationResult, summarise_validation

log = logging.getLogger(__name__)

_ANON_RANK = {
    Anonymity.UNKNOWN: 0,
    Anonymity.TRANSPARENT: 1,
    Anonymity.ANONYMOUS: 2,
    Anonymity.ELITE: 3,
}


@dataclass(slots=True)
class CycleReport:
    started_at: float = field(default_factory=time.time)  # type: ignore[arg-type]
    finished_at: float | None = None
    crawl: dict[str, Any] = field(default_factory=dict)  # type: ignore[arg-type]
    validation: dict[str, int] = field(default_factory=dict)  # type: ignore[arg-type]
    promoted: int = 0
    demoted: int = 0
    removed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round((self.finished_at or time.time()) - self.started_at, 2),
            "crawl": self.crawl,
            "validation": self.validation,
            "promoted": self.promoted,
            "demoted": self.demoted,
            "removed": self.removed,
        }


class Scheduler:
    def __init__(
        self,
        config: AppConfig,
        *,
        crawler: Crawler,
        validator: Validator,
        candidate_pool: CandidatePool,
        verified_pool: VerifiedPool,
        rotate_callback: Callable[[], Awaitable[None]] | None = None,
        on_event: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config
        self.crawler = crawler
        self.validator = validator
        self.candidate_pool = candidate_pool
        self.verified_pool = verified_pool
        self.rotate_callback = rotate_callback
        self.on_event = on_event
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self.last_report: CycleReport | None = None

    def _emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self.on_event is None:
            return
        try:
            outcome = self.on_event(event, payload or {})
            if asyncio.iscoroutine(outcome):
                asyncio.get_running_loop().create_task(outcome)
        except Exception as exc:  # pragma: no cover - observer isolation
            log.debug("scheduler event callback failed: %s", type(exc).__name__)

    # --- one full cycle -----------------------------------------------

    async def run_once(
        self,
        *,
        skip_crawl: bool = False,
        on_progress: Callable[[int, int, ValidationResult], None] | None = None,
    ) -> CycleReport:
        report = CycleReport()
        self._emit("cycle.started", {"at": report.started_at})

        if not skip_crawl:
            def on_result(result: Any) -> None:
                if result.nodes:
                    self.candidate_pool.add(result.nodes)

            results = await self.crawler.run(on_result=on_result)
            report.crawl = summarise(results)
            self._emit("crawl.finished", report.crawl)

        batch = self._collect_stale()
        log.info("revalidation batch: %d nodes", len(batch))
        validation: list[ValidationResult] = await self.validator.validate_many(
            batch, on_progress=on_progress
        )
        report.validation = summarise_validation(validation)
        report.promoted, report.demoted, report.removed = self._apply_results(validation)

        report.finished_at = time.time()
        self.last_report = report
        self._emit("cycle.finished", report.as_dict())
        return report

    def _collect_stale(self) -> list[ProxyNode]:
        cap = max(1, self.config.validator.concurrency * 4)
        ttl = self.config.validator.ttl_seconds
        candidate = self.candidate_pool.take_stale(cap, ttl)
        verified = self.verified_pool.take_stale(cap // 2, ttl)
        merged: dict[str, ProxyNode] = {node.stable_key(): node for node in candidate}
        for node in verified:
            merged.setdefault(node.stable_key(), node)
        return list(merged.values())

    def _apply_results(self, results: list[ValidationResult]) -> tuple[int, int, int]:
        min_rank = _ANON_RANK[Anonymity(self.config.validator.min_anonymity)]
        min_score = self.config.validator.min_score
        max_fail = self.config.validator.max_consecutive_failures
        promote: list[ProxyNode] = []
        back_to_candidate: list[ProxyNode] = []
        drop_keys: list[str] = []
        demote_keys: list[str] = []

        for result in results:
            node = Validator.apply(result)
            key = node.stable_key()
            if not result.ok:
                if node.fail_count >= max_fail:
                    drop_keys.append(key)
                else:
                    back_to_candidate.append(node)
                    demote_keys.append(key)
                continue
            if node.anonymity_rank >= min_rank and node.score >= min_score:
                promote.append(node)
            else:
                back_to_candidate.append(node)

        promoted = self.verified_pool.upsert(promote)
        # Remove failed verified-pool members and re-insert them into the
        # candidate pool with their failure counters intact.
        if demote_keys:
            self.verified_pool.remove(demote_keys)
        if back_to_candidate:
            self.candidate_pool.add(back_to_candidate)
        removed = 0
        if drop_keys:
            removed = self.verified_pool.remove(drop_keys) + self.candidate_pool.remove(drop_keys)
        return promoted, len(demote_keys), removed

    # --- background loops ---------------------------------------------

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop.clear()
        self._tasks.append(asyncio.create_task(self._cycle_loop(), name="anonchains-cycle"))
        if self.config.chain.rotation_seconds > 0 and self.rotate_callback is not None:
            self._tasks.append(
                asyncio.create_task(self._rotation_loop(), name="anonchains-rotation")
            )
        log.info(
            "scheduler started (cycle=%ds, rotation=%ds)",
            self.config.validator.interval_seconds,
            self.config.chain.rotation_seconds,
        )

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("scheduler stopped")

    async def _cycle_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - keep the loop alive
                log.warning("scheduler cycle failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=max(30, self.config.validator.interval_seconds)
                )
                return
            except asyncio.TimeoutError:
                continue

    async def _rotation_loop(self) -> None:
        period = max(1, self.config.chain.rotation_seconds)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=period)
                return
            except asyncio.TimeoutError:
                pass
            if self.rotate_callback is not None:
                try:
                    await self.rotate_callback()
                    self._emit("chain.rotated", {})
                except Exception as exc:  # pragma: no cover
                    log.warning("rotation callback failed: %s", type(exc).__name__)
