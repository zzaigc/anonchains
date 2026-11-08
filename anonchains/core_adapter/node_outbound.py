"""Map :class:`ProxyNode` onto sing-box / xray outbound objects.

Only the field mapping lives here; chain wiring (``detour`` / ``dialerProxy``)
is done in :mod:`anonchains.core_adapter.config_gen`. Any protocol a core
cannot express returns ``None`` and is reported as a warning, never silently
replaced with a direct connection.
"""

from __future__ import annotations

from typing import Any

from ..models.proxy_node import Protocol, ProxyNode

_SN = ("sni", "servername", "server_name", "peer", "host")
_ALPN = ("alpn",)
_INSECURE = ("insecure", "skip_cert_verify", "skip-cert-verify", "allowinsecure")
_REALITY_PBK = ("pbk", "public_key", "public-key", "reality_pbk")
_REALITY_SID = ("sid", "short_id", "short-id")
_FINGERPRINT = ("fp", "fingerprint", "client-fingerprint")

_XRAY_PROTOCOL = {
    Protocol.HTTP: "http",
    Protocol.HTTPS: "http",
    Protocol.SOCKS4: "socks",
    Protocol.SOCKS5: "socks",
    Protocol.VLESS: "vless",
    Protocol.VMESS: "vmess",
    Protocol.SHADOWSOCKS: "shadowsocks",
    Protocol.TROJAN: "trojan",
}


def _value(node: ProxyNode, keys: tuple[str, ...]) -> Any:
    for source in (node.credential, node.params):
        if not isinstance(source, dict):
            continue
        lowered = {str(k).lower().replace("-", "_"): v for k, v in source.items()}
        for key in keys:
            candidate = lowered.get(key.lower().replace("-", "_"))
            if candidate not in (None, "", [], {}):
                return candidate
    return None


def _text(node: ProxyNode, keys: tuple[str, ...], default: str = "") -> str:
    value = _value(node, keys)
    return str(value) if value is not None else default


def _bool(node: ProxyNode, keys: tuple[str, ...]) -> bool:
    value = _value(node, keys)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _list_of_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace("|", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def tls_enabled(node: ProxyNode) -> bool:
    security = _text(node, ("security", "tls", "encryption")).lower()
    if security in {"", "none", "false", "0", "no"}:
        return False
    return security in {"tls", "reality", "true", "1", "yes", "xtls"} or "tls" in security


def singbox_tls(node: ProxyNode, *, force: bool = False) -> dict[str, Any] | None:
    if not force and not tls_enabled(node):
        return None
    tls: dict[str, Any] = {"enabled": True}
    sni = _text(node, _SN)
    if sni:
        tls["server_name"] = sni
    alpn = _list_of_strings(_value(node, _ALPN))
    if alpn:
        tls["alpn"] = alpn
    if _bool(node, _INSECURE):
        tls["insecure"] = True
    pbk = _text(node, _REALITY_PBK)
    if pbk:
        tls["reality"] = {
            "enabled": True,
            "public_key": pbk,
            "short_id": _text(node, _REALITY_SID),
        }
    fingerprint = _text(node, _FINGERPRINT)
    if fingerprint:
        tls["utls"] = {"enabled": True, "fingerprint": fingerprint}
    return tls


def xray_tls(node: ProxyNode) -> dict[str, Any] | None:
    if not tls_enabled(node):
        return None
    settings: dict[str, Any] = {"allowInsecure": _bool(node, _INSECURE)}
    sni = _text(node, _SN)
    if sni:
        settings["serverName"] = sni
    alpn = _list_of_strings(_value(node, _ALPN))
    if alpn:
        settings["alpn"] = alpn
    fingerprint = _text(node, _FINGERPRINT)
    if fingerprint:
        settings["fingerprint"] = fingerprint
    return settings


def to_singbox_outbound(node: ProxyNode, tag: str) -> dict[str, Any] | None:
    protocol = node.protocol
    base: dict[str, Any] = {"tag": tag, "server": node.host, "server_port": node.port}
    username = _text(node, ("username", "user", "login"))
    password = _text(node, ("password", "passwd", "pass", "auth", "auth_str"))

    if protocol in {Protocol.HTTP, Protocol.HTTPS}:
        result = {"type": "http", **base}
        if username:
            result["username"] = username
        if password:
            result["password"] = password
        if protocol is Protocol.HTTPS:
            result["tls"] = singbox_tls(node, force=True) or {"enabled": True}
        return result
    if protocol in {Protocol.SOCKS4, Protocol.SOCKS5}:
        result = {
            "type": "socks",
            **base,
            "version": "4" if protocol is Protocol.SOCKS4 else "5",
        }
        if username:
            result["username"] = username
        if password:
            result["password"] = password
        return result
    if protocol is Protocol.VLESS:
        result = {
            "type": "vless",
            **base,
            "uuid": _text(node, ("uuid", "id")),
        }
        flow = _text(node, ("flow",))
        if flow:
            result["flow"] = flow
        tls = singbox_tls(node)
        if tls:
            result["tls"] = tls
        return result
    if protocol is Protocol.VMESS:
        result = {
            "type": "vmess",
            **base,
            "uuid": _text(node, ("uuid", "id")),
            "alter_id": int(_text(node, ("alterid", "aid"), "0") or 0),
            "security": _text(node, ("scy", "cipher", "security"), "auto"),
        }
        tls = singbox_tls(node)
        if tls:
            result["tls"] = tls
        return result
    if protocol is Protocol.SHADOWSOCKS:
        return {
            "type": "shadowsocks",
            **base,
            "method": _text(node, ("method", "cipher"), "aes-256-gcm"),
            "password": password,
        }
    if protocol is Protocol.TROJAN:
        result = {"type": "trojan", **base, "password": password}
        tls = singbox_tls(node) or {"enabled": True}
        result["tls"] = tls
        return result
    if protocol is Protocol.HYSTERIA2:
        return {
            "type": "hysteria2",
            **base,
            "password": password,
            "tls": singbox_tls(node) or {"enabled": True},
        }
    if protocol is Protocol.TUIC:
        result = {
            "type": "tuic",
            **base,
            "uuid": _text(node, ("uuid", "id")),
            "password": password,
        }
        tls = singbox_tls(node)
        if tls:
            result["tls"] = tls
        return result
    if protocol is Protocol.WIREGUARD:
        addresses = _list_of_strings(_value(node, ("allowed_ips", "local_address", "address", "ip")))
        result = {
            "type": "wireguard",
            **base,
            "private_key": _text(node, ("private_key", "private-key")),
            "peer_public_key": _text(node, ("public_key", "public-key")),
            "local_address": addresses or ["172.16.0.2/32"],
        }
        mtu = _text(node, ("mtu",))
        if mtu.isdigit():
            result["mtu"] = int(mtu)
        return result
    return None


def to_xray_outbound(node: ProxyNode, tag: str) -> dict[str, Any] | None:
    """Xray outbound. Chaining uses ``streamSettings.sockopt.dialerProxy``."""
    protocol = node.protocol
    server = {"address": node.host, "port": node.port}
    stream: dict[str, Any] = {"network": _text(node, ("net", "network", "type"), "tcp")}
    tls = xray_tls(node)
    if tls:
        stream["security"] = "tls"
        stream["tlsSettings"] = tls
    settings: dict[str, Any]

    if protocol in {Protocol.HTTP, Protocol.HTTPS, Protocol.SOCKS4, Protocol.SOCKS5}:
        entry: dict[str, Any] = dict(server)
        username = _text(node, ("username", "user"))
        password = _text(node, ("password", "passwd", "pass"))
        if username:
            entry["users"] = [{"user": username, "pass": password, "level": 0}]
        settings = {"servers": [entry]}
    elif protocol is Protocol.VLESS:
        settings = {
            "vnext": [
                {
                    **server,
                    "users": [
                        {
                            "id": _text(node, ("uuid", "id")),
                            "encryption": _text(node, ("encryption",), "none"),
                            "flow": _text(node, ("flow",)),
                            "level": 0,
                        }
                    ],
                }
            ]
        }
    elif protocol is Protocol.VMESS:
        settings = {
            "vnext": [
                {
                    **server,
                    "users": [
                        {
                            "id": _text(node, ("uuid", "id")),
                            "alterId": int(_text(node, ("alterid", "aid"), "0") or 0),
                            "security": _text(node, ("scy", "cipher"), "auto"),
                            "level": 0,
                        }
                    ],
                }
            ]
        }
    elif protocol is Protocol.SHADOWSOCKS:
        settings = {
            "servers": [
                {
                    **server,
                    "method": _text(node, ("method", "cipher"), "aes-256-gcm"),
                    "password": _text(node, ("password", "passwd", "pass")),
                    "level": 0,
                }
            ]
        }
    elif protocol is Protocol.TROJAN:
        settings = {
            "servers": [
                {**server, "password": _text(node, ("password", "passwd", "pass")), "level": 0}
            ]
        }
    else:
        # wireguard / hysteria2 / tuic / anytls / mtproto / clash: Xray outbound
        # support varies by version. Never guess -- report as unsupported.
        return None

    return {
        "tag": tag,
        "protocol": _XRAY_PROTOCOL[protocol],
        "settings": settings,
        "streamSettings": stream,
    }
