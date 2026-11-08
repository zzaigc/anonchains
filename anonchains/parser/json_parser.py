"""Tolerant JSON parser for public proxy APIs and metadata-rich lists.

Rather than hard-coding every API schema, it walks the document and treats any
mapping containing a plausible host + port pair as a node. Unknown keys become
non-sensitive ``params`` so protocol adapters can still use them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from ..models.proxy_node import Anonymity, Protocol, ProxyNode
from ..utils.net import is_private_address, parse_endpoint
from .base import BaseParser, ParseError, SourceSpec, register
from .plain import PlainParser, protocol_hint

_HOST_KEYS = ("ip", "host", "server", "address", "addr", "hostname", "proxy_ip", "proxy")
_PORT_KEYS = ("port", "p", "proxy_port", "server_port")
_PROTOCOL_KEYS = ("protocol", "proto", "type", "scheme", "proxy_type", "protocols", "protocol_type")
_USER_KEYS = ("username", "user", "login", "auth_user")
_SECRET_KEYS = ("password", "passwd", "pass", "uuid", "id", "auth", "auth_str", "token", "private_key")
_ANON_KEYS = ("anonymity", "anonymitylevel", "anonymity_level", "anon", "anonlevel")
_COUNTRY_KEYS = ("country", "country_code", "cc", "countrycode", "location", "geo")
_LATENCY_KEYS = ("latency", "responsetime", "response_time", "speed", "delay", "response")
_MAX_DEPTH = 12


def _lower_key_map(data: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower().replace("-", "_"): v for k, v in data.items()}


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", [], {}):
            return mapping[key]
    return None


def _protocol_of(value: Any, hint: Protocol) -> Protocol:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not value:
        return hint
    text = str(value).strip().lower()
    aliases = {
        "socks": Protocol.SOCKS5,
        "socks5h": Protocol.SOCKS5,
        "socks4a": Protocol.SOCKS4,
        "ssl": Protocol.HTTPS,
        "https": Protocol.HTTPS,
        "http": Protocol.HTTP,
        "shadowsocks": Protocol.SHADOWSOCKS,
        "shadowsocksr": Protocol.SHADOWSOCKSR,
        "hy2": Protocol.HYSTERIA2,
        "wg": Protocol.WIREGUARD,
        "clash": Protocol.CLASH,
    }
    try:
        return Protocol(text)
    except ValueError:
        return aliases.get(text, hint)


def _anonymity_of(value: Any) -> Anonymity:
    if not value:
        return Anonymity.UNKNOWN
    text = str(value).lower()
    if "elite" in text or "high" in text:
        return Anonymity.ELITE
    if "anonymous" in text or "anon" in text:
        return Anonymity.ANONYMOUS
    if "transparent" in text:
        return Anonymity.TRANSPARENT
    return Anonymity.UNKNOWN


def _walk(value: Any, depth: int = 0) -> Iterator[Any]:
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, depth + 1)
    elif isinstance(value, str):
        yield value


def node_from_mapping(raw: dict[str, Any], spec: SourceSpec) -> ProxyNode | None:
    data = _lower_key_map(raw)
    host = _first(data, _HOST_KEYS)
    port = _first(data, _PORT_KEYS)
    if host is None or port is None:
        return None
    try:
        port_int = int(str(port).strip())
    except (TypeError, ValueError):
        return None
    host_str = str(host).strip().strip("[]")
    if not host_str or not 1 <= port_int <= 65535 or is_private_address(host_str):
        return None

    hint = protocol_hint(spec.protocol_hint)
    protocol = _protocol_of(_first(data, _PROTOCOL_KEYS), hint)
    credential: dict[str, Any] = {}
    for key in (*_USER_KEYS, *_SECRET_KEYS):
        if key in data and data[key] not in (None, ""):
            credential[key] = data[key]
    params = {
        k: v
        for k, v in data.items()
        if k not in {*_HOST_KEYS, *_PORT_KEYS, *_PROTOCOL_KEYS, *_USER_KEYS, *_SECRET_KEYS}
        and isinstance(v, (str, int, float, bool, list, dict))
    }
    latency = _first(data, _LATENCY_KEYS)
    try:
        latency_ms = float(latency) if latency is not None else None
    except (TypeError, ValueError):
        latency_ms = None
    country = _first(data, _COUNTRY_KEYS)
    return ProxyNode(
        protocol=protocol,
        host=host_str,
        port=port_int,
        credential=credential or None,
        params=params,
        anonymity=_anonymity_of(_first(data, _ANON_KEYS)),
        country=str(country) if isinstance(country, str) and len(country) <= 8 else None,
        latency_ms=latency_ms,
        raw=json.dumps(raw, ensure_ascii=False, default=str)[:2048],
        source=spec.name,
    )


@register
class JsonParser(BaseParser):
    priority = 30

    def can_parse(self, sample: bytes, spec: SourceSpec) -> bool:
        if spec.kind in {"yaml", "subscription", "plain"}:
            return False
        text = sample.decode("utf-8", "ignore").lstrip()
        return text.startswith("{") or text.startswith("[")

    def parse(self, payload: bytes, spec: SourceSpec) -> Iterable[ProxyNode]:
        try:
            document = json.loads(payload.decode("utf-8", "replace"))
        except (ValueError, UnicodeError) as exc:
            raise ParseError(f"source {spec.name}: invalid JSON document") from exc
        plain = PlainParser()
        hint = protocol_hint(spec.protocol_hint)
        for item in _walk(document):
            if isinstance(item, dict):
                node = node_from_mapping(item, spec)
            else:
                endpoint = parse_endpoint(item)
                if endpoint is None:
                    continue
                node = plain.parse_line(item, hint, spec.name)
            if node is not None:
                yield node
