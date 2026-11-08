"""N-hop chain construction with no hard hop limit."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..models.proxy_node import EXTERNAL_PROTOCOLS, NATIVE_PROTOCOLS, ProxyNode

log = logging.getLogger(__name__)

CHAIN_MODES = ("strict", "dynamic", "random", "round_robin")
_SOFT_RESOURCE_WARNING = 16


class ChainBuildError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChainPolicy:
    hops: int = 5
    mode: str = "strict"
    rotation_seconds: int = 0
    prefer_protocols: tuple[str, ...] = ()
    allow_reuse: bool = False


@dataclass(frozen=True, slots=True)
class Chain:
    nodes: tuple[ProxyNode, ...]
    policy: ChainPolicy
    created_at: float = field(default_factory=time.time)

    @property
    def hops(self) -> int:
        return len(self.nodes)

    @property
    def requires_external_core(self) -> bool:
        return any(node.protocol in EXTERNAL_PROTOCOLS for node in self.nodes)

    @property
    def native_only(self) -> bool:
        return all(node.protocol in NATIVE_PROTOCOLS for node in self.nodes)

    def summaries(self) -> list[str]:
        """Credential-free hop descriptions, safe for CLI/API/logs."""
        return [node.summary() for node in self.nodes]

    def as_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        return {
            "hops": self.hops,
            "mode": self.policy.mode,
            "created_at": self.created_at,
            "requires_external_core": self.requires_external_core,
            "native_only": self.native_only,
            "nodes": [n.to_dict(include_secrets=include_secrets) for n in self.nodes],
        }


class ChainBuilder:
    """Select and order nodes from the verified pool into a chain."""

    def __init__(self, policy: ChainPolicy | None = None) -> None:
        self.policy = policy or ChainPolicy()
        self._cursor = 0

    def update_policy(self, policy: ChainPolicy) -> None:
        self.policy = policy

    @staticmethod
    def from_config(config: Any) -> ChainBuilder:
        chain = config.chain
        return ChainBuilder(
            ChainPolicy(
                hops=chain.hops,
                mode=chain.mode,
                rotation_seconds=chain.rotation_seconds,
                prefer_protocols=tuple(chain.prefer_protocols),
                allow_reuse=chain.allow_reuse,
            )
        )

    def build(self, nodes: list[ProxyNode], *, shuffle: bool = False) -> Chain:
        policy = self.policy
        if policy.hops < 1:
            raise ChainBuildError("chain.hops must be >= 1")
        if policy.hops > _SOFT_RESOURCE_WARNING:
            log.warning(
                "chain with %d hops: latency and timeout grow roughly linearly", policy.hops
            )

        pool = self._prepare_pool(nodes)
        if not pool:
            raise ChainBuildError("no usable nodes available for a chain")

        wanted = policy.hops
        if len(pool) < wanted:
            if policy.mode == "strict" and not policy.allow_reuse:
                raise ChainBuildError(
                    f"strict mode needs {wanted} distinct nodes, pool has {len(pool)}"
                )
            if policy.allow_reuse:
                pool = [pool[i % len(pool)] for i in range(wanted)]
                log.warning("chain reuses nodes to reach %d hops", wanted)
            else:
                log.info("chain degraded to %d hops (pool has %d)", len(pool), len(pool))
                wanted = len(pool)

        selected = self._select(pool, wanted, shuffle)
        return Chain(nodes=tuple(selected), policy=policy)

    def _prepare_pool(self, nodes: list[ProxyNode]) -> list[ProxyNode]:
        preferred = {p.lower() for p in self.policy.prefer_protocols}
        unique: dict[str, ProxyNode] = {node.stable_key(): node for node in nodes}
        pool = list(unique.values())
        # Prefer nodes the Python cascade can forward natively, then by score.
        pool.sort(
            key=lambda n: (
                0 if preferred and n.protocol.value in preferred else 1,
                0 if n.protocol in NATIVE_PROTOCOLS else 1,
                -n.score,
                n.latency_ms if n.latency_ms is not None else 10_000,
            )
        )
        return pool

    def _select(self, pool: list[ProxyNode], wanted: int, shuffle: bool) -> list[ProxyNode]:
        mode = self.policy.mode
        if mode == "round_robin":
            start = self._cursor % len(pool)
            self._cursor = (self._cursor + wanted) % len(pool)
            return [pool[(start + i) % len(pool)] for i in range(wanted)]
        if mode == "random" or shuffle:
            import random

            return random.sample(pool, min(wanted, len(pool)))
        # strict / dynamic: deterministic best-first selection.
        return pool[:wanted]
