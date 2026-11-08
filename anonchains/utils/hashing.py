"""Stable digests and deduplication keys."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


def stable_digest(*parts: str) -> str:
    """Order-independent-of-caller, deterministic hex digest over string parts."""
    h = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8", "replace")
        h.update(len(encoded).to_bytes(8, "big"))
        h.update(encoded)
    return h.hexdigest()


def canonical_value(value: Any) -> str:
    """Stable serialization of nested dictionaries / lists for identity hashing."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={canonical_value(value[k])}" for k in sorted(value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_value(v) for v in value) + "]"
    return str(value)


def node_key(
    protocol: str, host: str, port: int, credential: dict[str, Any] | None = None
) -> str:
    """Compute the pool deduplication key without constructing a node."""
    return stable_digest(protocol, host.lower(), str(port), canonical_value(credential))


def content_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
