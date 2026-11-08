"""Candidate pool: raw, deduplicated, not-yet-proven nodes."""

from __future__ import annotations

import time
from typing import Any

from ..models.proxy_node import ProxyNode
from .store import NodeStore


class CandidatePool:
    def __init__(self, store: NodeStore) -> None:
        self.store = store

    def add(self, nodes: list[ProxyNode]) -> int:
        """Ingest parsed nodes. Deduplication is handled by the primary key."""
        return self.store.upsert_many(nodes)

    def take_stale(self, limit: int, ttl_seconds: int) -> list[ProxyNode]:
        """Nodes needing (re)validation: never checked or TTL expired."""
        if limit <= 0:
            return []
        cutoff = time.time() - max(0, ttl_seconds)
        return self.store.all(
            limit=limit,
            stale_before=cutoff,
            order_by="last_checked_at ASC",
        )

    def take_unchecked(self, limit: int) -> list[ProxyNode]:
        return self.take_stale(limit, 0)

    def remove(self, keys: list[str]) -> int:
        return self.store.delete(keys)

    def count(self) -> int:
        return self.store.count()

    def stats(self) -> dict[str, Any]:
        return self.store.stats()

    def sample(self, limit: int = 20, *, protocol: str | None = None) -> list[ProxyNode]:
        return self.store.all(limit=limit, protocol=protocol)
