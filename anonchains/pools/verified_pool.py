"""Verified pool: validated nodes, scoring, rotation and crawl front-proxy feed.

Also implements the ``FrontProxyProvider`` protocol consumed by the crawler,
closing the feedback loop: validated HTTP/HTTPS nodes become the front proxy
for subsequent crawls. A round-robin cursor plus host exclusion prevents always
hitting the same node and avoids routing a request to its own source host.
"""

from __future__ import annotations

import random
import time
import threading
from typing import Any

from ..models.proxy_node import Protocol, ProxyNode
from .store import NodeStore

_FRONT_PROTOCOLS = (Protocol.HTTP.value, Protocol.HTTPS.value)


class VerifiedPool:
    def __init__(self, store: NodeStore) -> None:
        self.store = store
        self._cursor = 0
        self._lock = threading.Lock()

    # --- writes -------------------------------------------------------

    def upsert(self, nodes: list[ProxyNode]) -> int:
        return self.store.upsert_many(nodes)

    def demote(self, keys: list[str]) -> list[ProxyNode]:
        """Remove nodes and return them so the caller can put them back in the
        candidate pool (with failure metadata preserved)."""
        removed = [node for key in keys if (node := self.store.get(key)) is not None]
        self.store.delete(keys)
        return removed

    def remove(self, keys: list[str]) -> int:
        return self.store.delete(keys)

    # --- reads --------------------------------------------------------

    def best(
        self,
        limit: int = 10,
        *,
        protocols: tuple[str, ...] | None = None,
        exclude_hosts: set[str] | None = None,
    ) -> list[ProxyNode]:
        if limit <= 0:
            return []
        candidates = self.store.all(limit=max(limit * 4, 40), order_by="score DESC")
        result: list[ProxyNode] = []
        for node in candidates:
            if protocols and node.protocol.value not in protocols:
                continue
            if exclude_hosts and node.host in exclude_hosts:
                continue
            result.append(node)
            if len(result) >= limit:
                break
        return result

    def take_stale(self, limit: int, ttl_seconds: int) -> list[ProxyNode]:
        if limit <= 0:
            return []
        cutoff = time.time() - max(0, ttl_seconds)
        return self.store.all(limit=limit, stale_before=cutoff, order_by="last_checked_at ASC")

    def count(self) -> int:
        return self.store.count()

    def stats(self) -> dict[str, Any]:
        return self.store.stats()

    # --- front proxy feedback ----------------------------------------

    def pick(self, exclude_host: str | None = None) -> ProxyNode | None:
        """FrontProxyProvider implementation for the crawler."""
        return self.front_pick(exclude_host=exclude_host)

    def front_pick(self, exclude_host: str | None = None) -> ProxyNode | None:
        pool = self.store.all(limit=64, order_by="score DESC")
        usable = [
            node
            for node in pool
            if node.protocol.value in _FRONT_PROTOCOLS
            and node.fail_count < 3
            and (exclude_host is None or node.host != exclude_host)
        ]
        if not usable:
            return None
        # Bias towards the top of the pool without always using the same node.
        top = usable[: max(3, min(12, len(usable)))]
        with self._lock:
            index = self._cursor % len(top)
            self._cursor = (self._cursor + 1) % len(top)
        return top[index] if random.random() < 0.75 else random.choice(top)
