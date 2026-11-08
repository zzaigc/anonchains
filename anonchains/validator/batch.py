"""Concurrent validation batch and node-state transition."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import AppConfig
from ..models.proxy_node import NATIVE_PROTOCOLS, Anonymity, ProxyNode
from ..utils.concurrency import bounded_map
from .anonymity import EchoChecker, EchoResult, compute_score
from .probes import ProbeResult, probe_node

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ValidationResult:
    node: ProxyNode
    ok: bool = False
    skipped: bool = False
    reason: str | None = None
    latency_ms: float | None = None
    anonymity: Anonymity = Anonymity.UNKNOWN
    exit_ip: str | None = None
    probe: ProbeResult | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node.summary(),
            "ok": self.ok,
            "skipped": self.skipped,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "anonymity": self.anonymity.value,
            "exit_ip": self.exit_ip,
        }


class Validator:
    """Validate many nodes in parallel with a shared echo session."""

    def __init__(self, config: AppConfig, *, echo_urls: tuple[str, ...] | None = None) -> None:
        self.config = config
        self.echo_urls = tuple(echo_urls or config.validator.echo_urls)
        self.timeout = config.validator.timeout_seconds
        self.concurrency = config.validator.concurrency

    async def validate_many(
        self,
        nodes: list[ProxyNode],
        *,
        limit: int | None = None,
        on_progress: Callable[[int, int, ValidationResult], None] | None = None,
    ) -> list[ValidationResult]:
        if not nodes:
            return []
        cap = max(1, limit or self.concurrency)
        total = len(nodes)
        done = 0
        log.info("validating %d nodes (concurrency=%d)", len(nodes), cap)
        started = time.monotonic()
        async with EchoChecker(self.echo_urls, self.timeout) as checker:

            async def worker(node: ProxyNode) -> ValidationResult:
                nonlocal done
                result = await self._one(checker, node)
                if on_progress is not None:
                    done += 1
                    try:
                        on_progress(done, total, result)
                    except Exception as exc:  # pragma: no cover - UI isolation
                        log.debug("validation progress callback failed: %s", type(exc).__name__)
                return result

            raw = await bounded_map(nodes, worker, cap)
        results: list[ValidationResult] = []
        for index, item in enumerate(raw):
            if isinstance(item, BaseException):
                results.append(ValidationResult(nodes[index], ok=False, reason=f"{type(item).__name__}: {item}"))
            else:
                results.append(item)
        passed = sum(1 for r in results if r.ok)
        log.info(
            "validation finished in %.1fs: %d/%d passed, %d external-only",
            time.monotonic() - started,
            passed,
            len(results),
            sum(1 for r in results if r.skipped),
        )
        return results

    async def _one(self, checker: EchoChecker, node: ProxyNode) -> ValidationResult:
        if node.protocol not in NATIVE_PROTOCOLS:
            # Python cannot speak vless/vmess/wireguard/... natively. Keep the
            # node usable for the external-core path but flag it clearly.
            return ValidationResult(
                node=node,
                ok=True,
                skipped=True,
                reason="local probe skipped; requires sing-box/xray",
                latency_ms=node.latency_ms,
                anonymity=node.anonymity,
            )

        echo: EchoResult = await checker.check(node)
        if echo.ok:
            return ValidationResult(
                node=node,
                ok=True,
                latency_ms=echo.latency_ms,
                anonymity=echo.anonymity,
                exit_ip=echo.exit_ip,
            )
        # Only failed nodes pay for a second connection (handshake diagnosis).
        probe = await probe_node(
            node,
            self.timeout,
            target_host=checker.echo_target[0],
            target_port=checker.echo_target[1],
        )
        return ValidationResult(
            node=node,
            ok=False,
            reason=echo.reason or probe.reason or "validation failed",
            probe=probe,
        )

    @staticmethod
    def apply(result: ValidationResult) -> ProxyNode:  # noqa: D102
        """Return the node with updated statistics and score.

        The ``min_anonymity`` / ``min_score`` admission policy is enforced by
        the scheduler, not here, so a node's measured grade is never rewritten
        to satisfy a threshold.
        """
        node = result.node
        node.last_checked_at = time.time()
        if result.skipped:
            node.params["validation"] = "external-core-required"
            node.score = compute_score(node.latency_ms, node.anonymity, 0, node.fail_count) * 0.5
            return node
        if result.ok:
            if result.anonymity is not Anonymity.UNKNOWN:
                node.anonymity = result.anonymity
            node.success_count += 1
            node.fail_count = 0
            node.latency_ms = result.latency_ms
            node.score = compute_score(
                result.latency_ms, node.anonymity, node.success_count, node.fail_count
            )
            node.params.pop("validation", None)
        else:
            node.fail_count += 1
            node.success_count = max(0, node.success_count - 1)
            node.score = max(0.0, node.score - 15.0)
        return node


def summarise_validation(results: list[ValidationResult]) -> dict[str, int]:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.ok and not r.skipped),
        "external_only": sum(1 for r in results if r.skipped),
        "failed": sum(1 for r in results if not r.ok),
    }
