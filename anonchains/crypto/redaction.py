"""Sensitive-data redaction and memory hygiene helpers."""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from typing import Any

MASK = "***REDACTED***"

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "token",
        "api_token",
        "api_key",
        "secret",
        "client_secret",
        "private_key",
        "privatekey",
        "uuid",
        "id",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "cookie",
    }
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # user:password@ inside a URL
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"),
    # Authorization: Bearer <value>
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"),
    # key = value / "key": "value" for known sensitive keys
    re.compile(
        r"(?i)([\"']?(?:password|passwd|passphrase|token|api_token|api_key|secret|private_key|uuid|authorization|cookie)[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}\]]+)"
    ),
    # query-string secrets
    re.compile(r"(?i)([?&](?:token|key|password|secret|passwd)=)[^&\s]+"),
)


def redact(text: Any) -> str:
    """Return a log-safe representation of ``text``."""
    value = text if isinstance(text, str) else str(text)
    value = _PATTERNS[0].sub(lambda m: m.group(1) + MASK + "@", value)
    value = _PATTERNS[1].sub(lambda m: m.group(1) + MASK, value)
    value = _PATTERNS[2].sub(lambda m: m.group(1) + MASK, value)
    value = _PATTERNS[3].sub(lambda m: m.group(1) + MASK, value)
    return value


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy a mapping with sensitive values masked (for API responses/logs)."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in SENSITIVE_KEYS:
            out[key] = MASK
        elif isinstance(value, Mapping):
            out[key] = redact_mapping(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [redact_mapping(v) if isinstance(v, Mapping) else v for v in value]
        else:
            out[key] = value
    return out


def wipe(buffer: bytearray | None) -> None:
    """Best-effort in-place zeroing of a mutable byte buffer."""
    if buffer is None:
        return
    for i in range(len(buffer)):
        buffer[i] = 0


def constant_time_eq(a: bytes | str, b: bytes | str) -> bool:
    left = a.encode() if isinstance(a, str) else a
    right = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(left, right)
