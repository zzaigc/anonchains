"""Chain rotation state machine with O(1) round-robin pointer."""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from ..models.proxy_node import ProxyNode
from .builder import Chain, ChainBuildError, ChainBuilder, ChainPolicy

log = logging.getLogger(__name__)

PoolProvider = Callable[[], list[ProxyNode]]


class ChainRotator:
    """Holds the active chain and rebuilds it according to the configured mode."""

    def __init__(self, builder: ChainBuilder, pool_provider: PoolProvider) -> None:
        self.builder = builder
        self.pool_provider = pool_provider
        self._chain: Chain | None = None

    @property
    def current(self) -> Chain | None:
        return self._chain

    def set_policy(self, policy: ChainPolicy) -> None:
        self.builder.update_policy(policy)
        self._chain = None

    def pool_snapshot(self) -> list[ProxyNode]:
        try:
            return self.pool_provider()
        except Exception as exc:  # pragma: no cover - provider isolation
            log.warning("pool snapshot failed: %s", type(exc).__name__)
            return []

    def rotate(self) -> Chain:
        """Build a fresh chain. strict/dynamic reuse healthy hops when possible."""
        mode = self.builder.policy.mode
        pool = self.pool_snapshot()
        alive = {node.stable_key() for node in pool}

        if mode == "dynamic" and self._chain is not None:
            kept = [node for node in self._chain.nodes if node.stable_key() in alive]
            missing = self.builder.policy.hops - len(kept)
            if missing > 0:
                known = {node.stable_key() for node in kept}
                replacements = [n for n in pool if n.stable_key() not in known]
                kept.extend(replacements[:missing])
            if kept:
                self._chain = self.builder.build(kept)
                return self._chain

        self._chain = self.builder.build(pool, shuffle=(mode == "random"))
        return self._chain

    def next_chain(self) -> Chain:
        """O(1) advance for round_robin; other modes simply rotate."""
        if self._chain is None:
            return self.rotate()
        if self.builder.policy.mode == "round_robin":
            return self.rotate()
        return self._chain

    def status(self) -> dict[str, Any]:
        chain = self._chain
        return {
            "active": chain is not None,
            "hops": chain.hops if chain else 0,
            "mode": self.builder.policy.mode,
            "rotation_seconds": self.builder.policy.rotation_seconds,
            "requires_external_core": bool(chain and chain.requires_external_core),
            "hops_detail": chain.summaries() if chain else [],
        }

    def ensure(self) -> Chain:
        if self._chain is None:
            return self.rotate()
        return self._chain

    def invalidate_dead(self) -> bool:
        """Return True if the active chain lost a hop and needs rebuilding."""
        if self._chain is None:
            return True
        alive = {node.stable_key() for node in self.pool_snapshot()}
        return any(node.stable_key() not in alive for node in self._chain.nodes)
