"""SQLite persistence for both pools, with Vault-sealed sensitive columns.

Design notes
------------
* WAL + a single guarded connection: crawl/validate are asyncio tasks but pool
  writes go through one short critical section, avoiding SQLite lock churn.
* ``sealed`` holds ``{"credential": ..., "raw": ...}`` encrypted by the Vault
  and bound to the node's stable key as AAD. When the Vault is locked or
  disabled the column is left NULL -- secrets are NEVER written in plaintext.
* All non-sensitive columns stay queryable/indexable, so filtering and scoring
  never require decrypting the whole pool.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from ..crypto.vault import Vault
from ..models.proxy_node import Anonymity, Protocol, ProxyNode
from ..utils.concurrency import chunks
from ..utils.paths import display_path

log = logging.getLogger(__name__)

_COLUMNS = (
    "key", "protocol", "host", "port", "sealed", "params", "latency_ms",
    "anonymity", "country", "score", "success_count", "fail_count",
    "last_checked_at", "first_seen_at", "source", "created_at", "updated_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    key             TEXT PRIMARY KEY,
    protocol        TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL,
    sealed          TEXT,
    params          TEXT NOT NULL DEFAULT '{}',
    latency_ms      REAL,
    anonymity       TEXT NOT NULL DEFAULT 'unknown',
    country         TEXT,
    score           REAL NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    last_checked_at REAL,
    first_seen_at   REAL NOT NULL,
    source          TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_score    ON nodes(score DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_checked  ON nodes(last_checked_at);
CREATE INDEX IF NOT EXISTS idx_nodes_protocol ON nodes(protocol);
CREATE INDEX IF NOT EXISTS idx_nodes_host     ON nodes(host);
"""

_UPSERT = f"""
INSERT INTO nodes ({','.join(_COLUMNS)})
VALUES ({','.join('?' for _ in _COLUMNS)})
ON CONFLICT(key) DO UPDATE SET
    protocol        = excluded.protocol,
    host            = excluded.host,
    port            = excluded.port,
    sealed          = COALESCE(excluded.sealed, nodes.sealed),
    params          = excluded.params,
    latency_ms      = COALESCE(excluded.latency_ms, nodes.latency_ms),
    anonymity       = CASE WHEN excluded.anonymity != 'unknown'
                           THEN excluded.anonymity ELSE nodes.anonymity END,
    country         = COALESCE(excluded.country, nodes.country),
    score           = excluded.score,
    success_count   = excluded.success_count,
    fail_count      = excluded.fail_count,
    last_checked_at = COALESCE(excluded.last_checked_at, nodes.last_checked_at),
    source          = COALESCE(excluded.source, nodes.source),
    updated_at      = excluded.updated_at
"""


class NodeStore:
    """Thread-safe SQLite store shared by the candidate and verified pools."""

    def __init__(
        self,
        path: Path,
        vault: Vault | None = None,
        *,
        batch_size: int = 500,
        base_dir: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.base_dir = Path(base_dir) if base_dir else None
        self.vault = vault
        self.batch_size = max(1, batch_size)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init()

    # --- lifecycle ----------------------------------------------------

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
        log.debug("pool store opened: %s", display_path(self.path, self.base_dir))

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("pool store is closed")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def sealed_secrets(self) -> bool:
        return bool(self.vault and self.vault.is_unlocked)

    # --- serialisation ------------------------------------------------

    def _seal(self, node: ProxyNode) -> str | None:
        if self.vault is None or not self.vault.is_unlocked:
            if node.credential or node.raw:
                log.debug("Vault unavailable: sensitive fields for %s not persisted", node.summary())
            return None
        payload = {"credential": node.credential, "raw": node.raw}
        return self.vault.seal_dict(payload, aad=node.stable_key().encode())

    def _open(self, key: str, sealed: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if not sealed or self.vault is None:
            return None, None
        data = self.vault.open_dict(sealed, aad=key.encode())
        if not data:
            return None, None
        credential = data.get("credential")
        raw = data.get("raw")
        return (credential if isinstance(credential, dict) else None), (
            raw if isinstance(raw, str) else None
        )

    def _node_to_row(self, node: ProxyNode) -> tuple[Any, ...]:
        now = time.time()
        key = node.stable_key()
        return (
            key,
            node.protocol.value,
            node.host,
            node.port,
            self._seal(node),
            json.dumps(node.params, ensure_ascii=False, default=str),
            node.latency_ms,
            node.anonymity.value,
            node.country,
            node.score,
            node.success_count,
            node.fail_count,
            node.last_checked_at,
            node.first_seen_at,
            node.source,
            now,
            now,
        )

    @staticmethod
    def _row_to_node(row: sqlite3.Row, opened: tuple[dict[str, Any] | None, str | None]) -> ProxyNode:
        try:
            params = json.loads(row["params"])
        except (ValueError, TypeError):
            params = {}
        credential, raw = opened
        return ProxyNode(
            protocol=Protocol(row["protocol"]),
            host=row["host"],
            port=int(row["port"]),
            credential=credential,
            params=params if isinstance(params, dict) else {},
            raw=raw,
            latency_ms=row["latency_ms"],
            anonymity=Anonymity(row["anonymity"]),
            country=row["country"],
            score=float(row["score"]),
            success_count=int(row["success_count"]),
            fail_count=int(row["fail_count"]),
            last_checked_at=row["last_checked_at"],
            source=row["source"],
            first_seen_at=float(row["first_seen_at"]),
        )

    def _hydrate(self, rows: list[sqlite3.Row]) -> list[ProxyNode]:
        return [self._row_to_node(row, self._open(row["key"], row["sealed"])) for row in rows]

    # --- writes -------------------------------------------------------

    def upsert_many(self, nodes: Iterable[ProxyNode]) -> int:
        """Batch upsert. Returns the number of rows submitted."""
        rows = [self._node_to_row(node) for node in nodes]
        if not rows:
            return 0
        with self._lock, self._db:
            for batch in chunks(rows, self.batch_size):
                self._db.executemany(_UPSERT, batch)
        return len(rows)

    def delete(self, keys: Iterable[str]) -> int:
        unique = list(dict.fromkeys(keys))
        if not unique:
            return 0
        removed = 0
        with self._lock, self._db:
            for batch in chunks(unique, 400):
                placeholders = ",".join("?" for _ in batch)
                cursor = self._db.execute(
                    f"DELETE FROM nodes WHERE key IN ({placeholders})", tuple(batch)
                )
                removed += cursor.rowcount
        return removed

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM nodes")

    # --- reads --------------------------------------------------------

    def get(self, key: str) -> ProxyNode | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM nodes WHERE key = ?", (key,)).fetchone()  # type: ignore[union-attr]
        return self._hydrate([row])[0] if row else None

    def all(
        self,
        *,
        limit: int | None = None,
        protocol: str | None = None,
        min_score: float | None = None,
        stale_before: float | None = None,
        order_by: str = "score DESC",
    ) -> list[ProxyNode]:
        clauses: list[str] = []
        values: list[Any] = []
        if protocol:
            clauses.append("protocol = ?")
            values.append(protocol)
        if min_score is not None:
            clauses.append("score >= ?")
            values.append(min_score)
        if stale_before is not None:
            clauses.append("(last_checked_at IS NULL OR last_checked_at <= ?)")
            values.append(stale_before)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        allowed_order = {"score DESC", "latency_ms ASC", "last_checked_at ASC", "first_seen_at ASC"}
        order = order_by if order_by in allowed_order else "score DESC"
        sql = f"SELECT * FROM nodes{where} ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
            values.append(max(0, int(limit)))
        with self._lock:
            rows = self._db.execute(sql, tuple(values)).fetchall()  # type: ignore[union-attr]
        return self._hydrate(list(rows))

    def keys(self) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT key FROM nodes").fetchall()  # type: ignore[union-attr]
        return {row["key"] for row in rows}

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])  # type: ignore[union-attr]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = int(self._db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])  # type: ignore[union-attr]
            by_protocol = {
                row[0]: row[1]
                for row in self._db.execute(  # type: ignore[union-attr]
                    "SELECT protocol, COUNT(*) FROM nodes GROUP BY protocol ORDER BY COUNT(*) DESC"
                )
            }
            by_anonymity = {
                row[0]: row[1]
                for row in self._db.execute(  # type: ignore[union-attr]
                    "SELECT anonymity, COUNT(*) FROM nodes GROUP BY anonymity"
                )
            }
            avg = self._db.execute("SELECT AVG(score), AVG(latency_ms) FROM nodes").fetchone()  # type: ignore[union-attr]
            sealed = int(
                self._db.execute(  # type: ignore[union-attr]
                    "SELECT COUNT(*) FROM nodes WHERE sealed IS NOT NULL"
                ).fetchone()[0]
            )
        return {
            "total": total,
            "by_protocol": by_protocol,
            "by_anonymity": by_anonymity,
            "avg_score": round(float(avg[0] or 0), 2),
            "avg_latency_ms": round(float(avg[1] or 0), 1),
            "sealed": sealed,
            "path": display_path(self.path, self.base_dir),
        }
