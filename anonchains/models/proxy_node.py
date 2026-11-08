"""Unified domain model shared by every layer of anonchains.

A :class:`ProxyNode` travels through the whole pipeline:
source -> parser -> candidate pool -> validator -> verified pool -> chain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..utils.hashing import canonical_value, stable_digest
from ..utils.net import normalize_host


class Protocol(str, Enum):
    """Protocols anonchains can ingest, store, validate and export.

    ``HTTP``/``SOCKS*`` are natively forwardable by the built-in cascade.
    The remaining ones are handed to an external core (sing-box / xray).
    """

    HTTP = "http"
    HTTPS = "https"
    SOCKS4 = "socks4"
    SOCKS5 = "socks5"
    VLESS = "vless"
    VMESS = "vmess"
    SHADOWSOCKS = "ss"
    SHADOWSOCKSR = "ssr"
    TROJAN = "trojan"
    WIREGUARD = "wireguard"
    HYSTERIA2 = "hysteria2"
    TUIC = "tuic"
    ANYTLS = "anytls"
    MTPROTO = "mtproto"
    CLASH = "clash"
    UNKNOWN = "unknown"


#: Protocols the built-in Python cascade can actually forward.
NATIVE_PROTOCOLS = frozenset(
    {Protocol.HTTP, Protocol.HTTPS, Protocol.SOCKS4, Protocol.SOCKS5}
)

#: Protocols that require an external core to carry traffic.
EXTERNAL_PROTOCOLS: frozenset[Protocol] = frozenset(Protocol) - NATIVE_PROTOCOLS - {Protocol.UNKNOWN, Protocol.CLASH}


class Anonymity(str, Enum):
    TRANSPARENT = "transparent"
    ANONYMOUS = "anonymous"
    ELITE = "elite"
    UNKNOWN = "unknown"


_ANONYMITY_RANK = {
    Anonymity.UNKNOWN: 0,
    Anonymity.TRANSPARENT: 1,
    Anonymity.ANONYMOUS: 2,
    Anonymity.ELITE: 3,
}


@dataclass(slots=True)
class ProxyNode:
    """A single proxy endpoint plus its validation metadata.

    ``credential`` holds protocol-specific *sensitive* material (passwords,
    UUIDs, private keys, ...). It is sealed by the Vault before being written
    to a pool and is never logged. ``params`` holds non-sensitive protocol
    options (SNI, ALPN, cipher, network type, ...).
    """

    protocol: Protocol
    host: str
    port: int
    credential: dict[str, Any] | None = None
    params: dict[str, Any] = field(default_factory=dict)  # type: ignore[arg-type]
    raw: str | None = None
    latency_ms: float | None = None
    anonymity: Anonymity = Anonymity.UNKNOWN
    country: str | None = None
    score: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    last_checked_at: float | None = None
    source: str | None = None
    first_seen_at: float = field(default_factory=time.time)  # type: ignore[arg-type]

    def __post_init__(self) -> None:
        if not isinstance(self.protocol, Protocol):
            self.protocol = Protocol(self.protocol)
        if not isinstance(self.anonymity, Anonymity):
            self.anonymity = Anonymity(self.anonymity)
        self.host = normalize_host(self.host)
        self.port = int(self.port)
        if not 1 <= self.port <= 65535:
            raise ValueError(f"invalid port: {self.port}")

    def stable_key(self) -> str:
        """Content-derived identity used for O(1) deduplication.

        Two nodes with the same endpoint but different credentials are distinct
        (common for vmess/vless subscriptions sharing one host).
        """
        return stable_digest(
            self.protocol.value,
            self.host.lower(),
            str(self.port),
            canonical_value(self.credential),
        )

    @property
    def endpoint(self) -> tuple[str, int]:
        return self.host, self.port

    @property
    def is_native(self) -> bool:
        return self.protocol in NATIVE_PROTOCOLS

    @property
    def anonymity_rank(self) -> int:
        return _ANONYMITY_RANK[self.anonymity]

    def summary(self) -> str:
        """Safe for logs: never includes credentials."""
        return f"{self.protocol.value}://{self.host}:{self.port}"

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "protocol": self.protocol.value,
            "host": self.host,
            "port": self.port,
            "params": dict(self.params),
            "latency_ms": self.latency_ms,
            "anonymity": self.anonymity.value,
            "country": self.country,
            "score": round(self.score, 4),
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "last_checked_at": self.last_checked_at,
            "source": self.source,
            "first_seen_at": self.first_seen_at,
            "stable_key": self.stable_key(),
        }
        if include_secrets:
            data["credential"] = dict(self.credential or {})
            data["raw"] = self.raw
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProxyNode:
        return cls(
            protocol=Protocol(data.get("protocol", "unknown")),
            host=data["host"],
            port=int(data["port"]),
            credential=data.get("credential") or None,
            params=dict(data.get("params") or {}),
            raw=data.get("raw"),
            latency_ms=data.get("latency_ms"),
            anonymity=Anonymity(data.get("anonymity", "unknown")),
            country=data.get("country"),
            score=float(data.get("score") or 0.0),
            success_count=int(data.get("success_count") or 0),
            fail_count=int(data.get("fail_count") or 0),
            last_checked_at=data.get("last_checked_at"),
            source=data.get("source"),
            first_seen_at=float(data.get("first_seen_at") or time.time()),
        )
