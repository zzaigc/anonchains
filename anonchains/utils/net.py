"""Address parsing and small network helpers (no I/O, safe to import anywhere)."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote

DEFAULT_CONNECT_TIMEOUT = 8.0
DEFAULT_READ_TIMEOUT = 20.0

_HOST_PORT = re.compile(r"^\s*(\[[0-9a-fA-F:]+\]|[^:\s/]+)\s*[:\s,]\s*(\d{2,5})\s*$")
_SCHEME = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*)://(.*)$", re.S)


def normalize_host(host: str) -> str:
    host = (host or "").strip().strip("[]")
    return host.lower() if host.count(":") else host


def parse_endpoint(text: str) -> tuple[str, int] | None:
    """Parse ``1.2.3.4:8080``, ``1.2.3.4 8080``, ``[::1]:1080`` etc."""
    if not text:
        return None
    match = _HOST_PORT.match(text)
    if not match:
        return None
    host = normalize_host(match.group(1))
    port = int(match.group(2))
    if not host or not 1 <= port <= 65535:
        return None
    return host, port


def split_scheme(text: str) -> tuple[str | None, str]:
    match = _SCHEME.match(text)
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return None, text.strip()


def is_private_address(host: str) -> bool:
    """True for loopback / private / link-local / reserved IP literals.

    Hostnames return ``False``: resolving them here would introduce blocking
    DNS on hot paths. Callers that need resolution should do so explicitly.
    """
    try:
        ip = ipaddress.ip_address(normalize_host(host))
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def proxy_url(protocol: str, host: str, port: int, user: str = "", password: str = "") -> str:
    """Build an aiohttp-compatible proxy URL. Credentials are percent-encoded."""
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    scheme = "http" if protocol in {"http", "https"} else protocol
    return f"{scheme}://{auth}{normalize_host(host)}:{port}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
