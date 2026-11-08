"""Plain-text proxy list parser.

Handles the formats used by virtually every raw GitHub proxy list:
``1.2.3.4:8080``, ``1.2.3.4 8080``, ``1.2.3.4,8080``,
``http://1.2.3.4:8080``, ``socks5://1.2.3.4:1080`` and
``1.2.3.4:8080:user:pass``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..models.proxy_node import Protocol, ProxyNode
from ..utils.net import is_private_address, normalize_host, parse_endpoint, split_scheme
from .base import BaseParser, SourceSpec, register

_SCHEME_TO_PROTOCOL = {
    "http": Protocol.HTTP,
    "https": Protocol.HTTPS,
    "socks4": Protocol.SOCKS4,
    "socks4a": Protocol.SOCKS4,
    "socks5": Protocol.SOCKS5,
    "socks5h": Protocol.SOCKS5,
}

_CREDENTIAL_LINE = re.compile(
    r"^\s*(\[[0-9a-fA-F:]+\]|[^:\s]+)[:\s,](\d{2,5})[:\s,]([^\s:]+)[:\s,]([^\s]+)\s*$"
)
_URI_WITH_AUTH = re.compile(r"^([a-z0-9+.-]+)://([^:/@]+):([^@/]+)@([^:/]+):(\d{2,5})/?", re.I)


@register
class PlainParser(BaseParser):
    priority = 10

    def can_parse(self, sample: bytes, spec: SourceSpec) -> bool:
        if spec.kind in {"json", "yaml", "subscription"}:
            return False
        text = sample.decode("utf-8", "ignore").lstrip()
        if text.startswith(("{", "[", "proxies:", "port:")):
            return False
        return any(
            parse_endpoint(line) is not None
            for line in text.splitlines()[:40]
        )

    def parse(self, payload: bytes, spec: SourceSpec) -> Iterable[ProxyNode]:
        hint = protocol_hint(spec.protocol_hint)
        for line in payload.decode("utf-8", "ignore").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";", "//", "<")):
                continue
            node = self.parse_line(line, hint, spec.name)
            if node is not None:
                yield node

    @staticmethod
    def parse_line(line: str, hint: Protocol, source: str) -> ProxyNode | None:
        # scheme://user:pass@host:port
        auth_match = _URI_WITH_AUTH.match(line)
        if auth_match:
            scheme, user, password, host, port = auth_match.groups()
            protocol = _SCHEME_TO_PROTOCOL.get(scheme.lower())
            if protocol is None or is_private_address(host):
                return None
            return ProxyNode(
                protocol=protocol,
                host=host,
                port=int(port),
                credential={"username": user, "password": password},
                raw=line,
                source=source,
            )

        # ip:port:user:pass
        cred_match = _CREDENTIAL_LINE.match(line)
        if cred_match:
            host, port, user, password = cred_match.groups()
            return ProxyNode(
                protocol=hint,
                host=normalize_host(host),
                port=int(port),
                credential={"username": user, "password": password},
                raw=line,
                source=source,
            )

        scheme, rest = split_scheme(line)
        if scheme is None:
            protocol = hint
        else:
            protocol = _SCHEME_TO_PROTOCOL.get(scheme)
            if protocol is None:
                return None
        # Strip a trailing path or fragment left by some lists.
        rest = rest.split("/", 1)[0].split("#", 1)[0].split("?", 1)[0].strip()
        endpoint = parse_endpoint(rest)
        if endpoint is None:
            return None
        host, port = endpoint
        if is_private_address(host):
            return None
        return ProxyNode(protocol=protocol, host=host, port=port, raw=line, source=source)


def protocol_hint(value: str | None) -> Protocol:
    if not value:
        return Protocol.HTTP
    try:
        return Protocol(value.lower())
    except ValueError:
        return {"http": Protocol.HTTP, "https": Protocol.HTTPS, "ssl": Protocol.HTTPS,
                "socks4": Protocol.SOCKS4, "socks5": Protocol.SOCKS5}.get(
            value.lower(), Protocol.HTTP
        )
