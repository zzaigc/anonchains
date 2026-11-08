"""Base64 subscriptions and per-protocol share-link parser.

Supports ``vmess://``, ``vless://``, ``ss://``, ``ssr://``, ``trojan://``,
``hysteria2://`` / ``hy2://``, ``tuic://``, ``wireguard://``, ``anytls://``,
``socks5://`` and ``http(s)://`` share links, plus base64-encoded
subscription bundles. Credentials always land in ``ProxyNode.credential`` so
the pool can seal them with the Vault.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..models.proxy_node import Protocol, ProxyNode
from ..utils.net import is_private_address
from .base import BaseParser, SourceSpec, register

_SCHEME_LINE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*)://(.+?)\s*$")
_SHARE_SCHEME = re.compile(
    r"(?i)(vmess|vless|ssr|ss|trojan|hysteria2|hysteria|hy2|hy|tuic|wireguard|anytls|socks5|socks4|https|http)://"
)
_B64_CHARS = re.compile(r"^[A-Za-z0-9+/=_-]+$")

_SCHEME_ALIASES: dict[str, str] = {
    "hy": "hysteria2",
    "hy2": "hysteria2",
    "hysteria": "hysteria2",
    "hysteria2": "hysteria2",
    "socks": "socks5",
}

_PROTOCOLS: dict[str, Protocol] = {
    "vmess": Protocol.VMESS,
    "vless": Protocol.VLESS,
    "ss": Protocol.SHADOWSOCKS,
    "ssr": Protocol.SHADOWSOCKSR,
    "trojan": Protocol.TROJAN,
    "hysteria2": Protocol.HYSTERIA2,
    "tuic": Protocol.TUIC,
    "wireguard": Protocol.WIREGUARD,
    "anytls": Protocol.ANYTLS,
    "mtproto": Protocol.MTPROTO,
    "socks5": Protocol.SOCKS5,
    "socks4": Protocol.SOCKS4,
    "http": Protocol.HTTP,
    "https": Protocol.HTTPS,
}


def decode_base64(text: str) -> str | None:
    """Tolerant standard/urlsafe base64 decode with automatic padding."""
    compact = "".join(text.split()).rstrip("=")
    if len(compact) < 8 or not _B64_CHARS.fullmatch(compact):
        return None
    normalised = compact.replace("-", "+").replace("_", "/")
    padded = normalised + "=" * (-len(normalised) % 4)
    try:
        decoded = base64.b64decode(padded, validate=True).decode("utf-8")
        return decoded if decoded.strip() else None
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def looks_like_base64_bundle(text: str) -> bool:
    compact = "".join(text.split())
    if len(compact) < 16:
        return False
    return bool(_B64_CHARS.fullmatch(compact))


def _flat_query(query: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(query, keep_blank_values=False).items() if v}


def _node(
    scheme: str,
    host: str,
    port: int,
    credential: dict[str, Any],
    params: dict[str, Any],
    raw: str,
    source: str,
) -> ProxyNode | None:
    protocol = _PROTOCOLS.get(scheme)
    if protocol is None or not host or not 1 <= port <= 65535 or is_private_address(host):
        return None
    return ProxyNode(
        protocol=protocol,
        host=host.strip("[]").lower(),
        port=port,
        credential=credential or None,
        params=params,
        raw=raw[:4096],
        source=source,
    )


def _parse_vmess(body: str, raw: str, source: str) -> ProxyNode | None:
    decoded = decode_base64(body.strip())
    if not decoded:
        return None
    try:
        data = json.loads(decoded)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        port = int(data.get("port", 0))
    except (TypeError, ValueError):
        return None
    host = str(data.get("add", "")).strip()
    credential = {"uuid": str(data.get("id", "")), "alterId": str(data.get("aid", 0))}
    params = {
        k: data[k]
        for k in ("v", "ps", "net", "type", "host", "path", "tls", "sni", "fp", "alpn", "scy")
        if k in data
    }
    return _node("vmess", host, port, credential, params, raw, source)


def parse_share_link(line: str, source: str) -> ProxyNode | None:
    """Parse a single share link. Returns ``None`` for anything unrecognised."""
    match = _SCHEME_LINE.match(line)
    if not match:
        return None
    scheme, body = match.group(1).lower(), match.group(2)
    if scheme == "vmess":
        return _parse_vmess(body, line, source)
    scheme = _SCHEME_ALIASES.get(scheme, scheme)
    if scheme not in _PROTOCOLS:
        return None

    parsed = urlparse(f"{scheme}://{body}")
    host = parsed.hostname or ""
    try:
        port = parsed.port or 0
    except ValueError:
        return None
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    query = _flat_query(parsed.query)
    name = unquote(parsed.fragment) if parsed.fragment else ""

    credential: dict[str, Any] = {}
    params: dict[str, Any] = {}

    if scheme == "ss" or scheme == "ssr":
        # SIP002: ss://base64(method:password@host:port)#tag.
        # Extract the base64 blob from the raw body; urlparse would mangle '/'
        # and '=' characters that are valid inside base64.
        blob = body.split("#", 1)[0].split("?", 1)[0]
        embedded = decode_base64(blob)
        if embedded and "@" in embedded:
            inner = urlparse(f"{scheme}://{embedded}")
            host = inner.hostname or ""
            try:
                port = inner.port or 0
            except ValueError:
                return None
            user = unquote(inner.username or "")
            password = unquote(inner.password or "")
            credential["method"] = user
            credential["password"] = password
            query.update(_flat_query(inner.query))
        else:
            decoded_user = decode_base64(user)
            if decoded_user and ":" in decoded_user:
                method, password = decoded_user.split(":", 1)
                credential["method"] = method
            elif user:
                credential["method"] = user
            credential["password"] = password
        params.update(query)
    elif scheme == "vless":
        credential["uuid"] = user
        params.update(query)
    elif scheme == "trojan":
        credential["password"] = user
        params.update(query)
    elif scheme == "hysteria2":
        credential["password"] = user or password
        params.update(query)
    elif scheme == "tuic":
        credential["uuid"] = user
        credential["password"] = password
        params.update(query)
    elif scheme == "wireguard":
        credential["public_key"] = user
        if "private_key" in query:
            credential["private_key"] = query.pop("private_key")
        if "private-key" in query:
            credential["private_key"] = query.pop("private-key")
        params.update(query)
    elif scheme == "anytls":
        credential["password"] = user
        params.update(query)
    else:  # http / https / socks4 / socks5
        if user:
            credential["username"] = user
        if password:
            credential["password"] = password

    if name:
        params["name"] = name
    return _node(scheme, host, port, credential, params, line, source)


@register
class SubscriptionParser(BaseParser):
    priority = 50

    def can_parse(self, sample: bytes, spec: SourceSpec) -> bool:
        if spec.kind == "subscription":
            return True
        if spec.kind in {"json", "yaml"}:
            return False
        text = sample.decode("utf-8", "ignore")
        if _SHARE_SCHEME.search(text[:16_384]):
            return True
        return looks_like_base64_bundle(text[:8192]) and "://" not in text[:256]

    def parse(self, payload: bytes, spec: SourceSpec) -> Iterable[ProxyNode]:
        text = payload.decode("utf-8", "replace")
        if looks_like_base64_bundle(text[:8192]) and "://" not in text[:256]:
            decoded = decode_base64(text)
            if decoded:
                text = decoded
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith(("#", ";", "//")):
                continue
            node = parse_share_link(candidate, spec.name)
            if node is not None:
                yield node
