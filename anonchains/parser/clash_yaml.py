"""Clash YAML / subscription document parser.

Maps ``proxies:`` entries onto :class:`ProxyNode` for every protocol sing-box
and xray can consume, including vless, vmess, wireguard, hysteria2 and tuic.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import yaml

from ..models.proxy_node import Protocol, ProxyNode
from ..utils.net import is_private_address
from .base import BaseParser, SourceSpec, register

_CLASH_TYPES: dict[str, Protocol] = {
    "http": Protocol.HTTP,
    "https": Protocol.HTTPS,
    "socks4": Protocol.SOCKS4,
    "socks5": Protocol.SOCKS5,
    "ss": Protocol.SHADOWSOCKS,
    "shadowsocks": Protocol.SHADOWSOCKS,
    "ssr": Protocol.SHADOWSOCKSR,
    "vmess": Protocol.VMESS,
    "vless": Protocol.VLESS,
    "trojan": Protocol.TROJAN,
    "wireguard": Protocol.WIREGUARD,
    "wg": Protocol.WIREGUARD,
    "hysteria": Protocol.HYSTERIA2,
    "hysteria2": Protocol.HYSTERIA2,
    "hy2": Protocol.HYSTERIA2,
    "tuic": Protocol.TUIC,
    "snell": Protocol.UNKNOWN,
    "anytls": Protocol.UNKNOWN,
}

_SECRET_FIELDS = frozenset(
    {
        "password", "passwd", "pass", "uuid", "id", "auth", "auth-str", "auth_str",
        "private-key", "private_key", "token", "obfs-password", "obfs_password",
        "psk", "public-key", "presharedkey", "pre_shared_key",
    }
)
_SKIP_FIELDS = frozenset({"name", "type", "server", "port", "tag"})


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def node_from_clash(entry: dict[str, Any], source: str) -> ProxyNode | None:
    data = {str(k).lower(): v for k, v in entry.items()}
    raw_type = str(data.get("type", "")).lower()
    protocol = _CLASH_TYPES.get(raw_type)
    if protocol is None or protocol is Protocol.UNKNOWN:
        return None
    host = str(data.get("server", "")).strip().strip("[]")
    try:
        port = int(data.get("port", 0))
    except (TypeError, ValueError):
        return None
    if not host or not 1 <= port <= 65535 or is_private_address(host):
        return None

    credential: dict[str, Any] = {}
    params: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SKIP_FIELDS or value is None:
            continue
        if key in _SECRET_FIELDS:
            credential[key] = _jsonable(value)
        else:
            params[key] = _jsonable(value)
    return ProxyNode(
        protocol=protocol,
        host=host,
        port=port,
        credential=credential or None,
        params=params,
        raw=json.dumps(_jsonable(entry), ensure_ascii=False)[:4096],
        source=source,
        country=None,
    )


@register
class ClashYamlParser(BaseParser):
    priority = 40

    def can_parse(self, sample: bytes, spec: SourceSpec) -> bool:
        if spec.kind == "subscription" or spec.kind == "plain":
            return False
        text = sample.decode("utf-8", "ignore").lstrip()
        if spec.kind == "yaml":
            return True
        return "proxies:" in text[:8192] or text.startswith("port:")

    def parse(self, payload: bytes, spec: SourceSpec) -> Iterable[ProxyNode]:
        try:
            document = yaml.safe_load(payload.decode("utf-8", "replace"))
        except (yaml.YAMLError, UnicodeError):
            return
        if isinstance(document, dict):
            entries = document.get("proxies") or []
        elif isinstance(document, list):
            entries = document
        else:
            return
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            node = node_from_clash(entry, spec.name)
            if node is not None:
                yield node
