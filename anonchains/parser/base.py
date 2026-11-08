"""Parser plug-in contract and source specification."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from ..models.proxy_node import ProxyNode

#: Guard against a malicious/oversized payload exhausting memory.
MAX_NODES_PER_SOURCE = 50_000


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    url: str
    kind: str = "plain"  # plain | json | yaml | subscription | api
    protocol_hint: str | None = None
    enabled: bool = True
    weight: int = 1
    headers: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.url:
            raise ValueError("SourceSpec requires name and url")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"source {self.name}: only http(s) URLs are allowed")
        if self.kind not in {"plain", "json", "yaml", "subscription", "api"}:
            raise ValueError(f"source {self.name}: unknown kind {self.kind!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceSpec:
        return cls(
            name=str(data["name"]),
            url=str(data["url"]),
            kind=str(data.get("kind", "plain")),
            protocol_hint=data.get("protocol_hint"),
            enabled=bool(data.get("enabled", True)),
            weight=int(data.get("weight", 1)),
            headers=dict(data.get("headers") or {}),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "kind": self.kind,
            "protocol_hint": self.protocol_hint,
            "enabled": self.enabled,
            "weight": self.weight,
            "headers": dict(self.headers),
            "note": self.note,
        }


class ParseError(ValueError):
    """Raised when a payload cannot be interpreted as proxy data."""


class BaseParser(ABC):
    """All parsers are stateless and registered in :data:`PARSER_REGISTRY`."""

    priority: ClassVar[int] = 0

    @abstractmethod
    def can_parse(self, sample: bytes, spec: SourceSpec) -> bool:
        """Cheap sniff; ``sample`` is at most the first 64 KiB."""

    @abstractmethod
    def parse(self, payload: bytes, spec: SourceSpec) -> Iterable[ProxyNode]:
        """Yield nodes; implementations must skip, not fail on, bad lines."""


PARSER_REGISTRY: list[type[BaseParser]] = []


_P = TypeVar("_P", bound=BaseParser)


def register(cls: type[_P]) -> type[_P]:
    """Class decorator adding a parser to the registry (highest priority first).

    Generic over the concrete class so decorating does not erase subclass-only
    members from the static type.
    """
    if cls not in PARSER_REGISTRY:
        PARSER_REGISTRY.append(cls)
        PARSER_REGISTRY.sort(key=lambda c: c.priority, reverse=True)
    return cls


def registered_parsers() -> tuple[type[BaseParser], ...]:
    return tuple(PARSER_REGISTRY)
