from __future__ import annotations

import base64
import json

import pytest

from anonchains.models.proxy_node import Protocol
from anonchains.parser import SourceSpec, parse_payload, parse_share_link, sniff_kind
from anonchains.parser.base import ParseError


def spec(kind: str, hint: str | None = None) -> SourceSpec:
    return SourceSpec(name="unit", url="https://example.test/list", kind=kind, protocol_hint=hint)


def test_plain_variants_and_dedupe():
    payload = (
        b"# comment\n"
        b"1.2.3.4:8080\n"
        b"1.2.3.4:8080\n"
        b"http://8.8.8.8:3128\n"
        b"socks5://9.9.9.9:1080\n"
        b"5.6.7.8 8000\n"
        b"http://user:pw@8.8.4.4:8081\n"
    )
    nodes = parse_payload(payload, spec("plain", "http"))
    protocols = {n.protocol for n in nodes}
    assert Protocol.SOCKS5 in protocols
    assert Protocol.HTTP in protocols
    assert len(nodes) == len({n.stable_key() for n in nodes})
    assert any(n.credential and n.credential.get("password") == "pw" for n in nodes)


def test_private_addresses_are_rejected():
    nodes = parse_payload(b"127.0.0.1:8080\n10.0.0.1:3128\n8.8.8.8:80\n", spec("plain"))
    assert [n.host for n in nodes] == ["8.8.8.8"]


def test_json_nested_api_payload():
    document = {
        "data": [
            {"ip": "1.1.1.1", "port": "8080", "protocols": ["socks5"], "anonymityLevel": "elite"},
            {"ip": "2.2.2.2", "port": 3128, "country": "US", "latency": 123.4},
        ],
        "meta": {"total": 2},
    }
    nodes = parse_payload(json.dumps(document).encode(), spec("json"))
    assert len(nodes) == 2
    socks = next(n for n in nodes if n.host == "1.1.1.1")
    assert socks.protocol is Protocol.SOCKS5
    assert socks.anonymity.value == "elite"


def test_clash_yaml_all_protocols():
    payload = b"""port: 7890
proxies:
  - {name: v, type: vless, server: 1.2.3.4, port: 443, uuid: UUID-1, tls: true, sni: example.com}
  - {name: w, type: wireguard, server: 5.6.7.8, port: 51820, private-key: SECRET}
  - {name: s, type: socks5, server: 9.9.9.9, port: 1080}
"""
    nodes = parse_payload(payload, spec("yaml"))
    assert {n.protocol for n in nodes} == {Protocol.VLESS, Protocol.WIREGUARD, Protocol.SOCKS5}
    wg = next(n for n in nodes if n.protocol is Protocol.WIREGUARD)
    assert wg.credential and wg.credential["private-key"] == "SECRET"
    assert "private-key" not in wg.params


def test_share_links():
    links = {
        "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443?encryption=none&security=tls#a": Protocol.VLESS,
        "trojan://secret@5.6.7.8:443?sni=example.com": Protocol.TROJAN,
        "tuic://uuid-1:pwd@9.9.9.9:443?alpn=h3": Protocol.TUIC,
        "hy2://pwd@8.8.4.4:443?sni=example.com": Protocol.HYSTERIA2,
        "socks5://u:p@8.8.8.8:1080": Protocol.SOCKS5,
    }
    for link, protocol in links.items():
        node = parse_share_link(link, "unit")
        assert node is not None, link
        assert node.protocol is protocol
        assert node.port == 443 or node.port == 1080


def test_vmess_base64_link():
    config = {
        "v": "2", "ps": "node", "add": "1.2.3.4", "port": 443,
        "id": "uuid-abc", "aid": "0", "net": "ws", "tls": "tls",
    }
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    node = parse_share_link(f"vmess://{encoded}", "unit")
    assert node is not None
    assert node.protocol is Protocol.VMESS
    assert node.credential and node.credential["uuid"] == "uuid-abc"
    assert node.params["net"] == "ws"


def test_shadowsocks_sip002():
    inner = base64.b64encode(b"aes-256-gcm:password@1.2.3.4:8388").decode()
    node = parse_share_link(f"ss://{inner}#tag", "unit")
    assert node is not None
    assert node.protocol is Protocol.SHADOWSOCKS
    assert node.host == "1.2.3.4" and node.port == 8388
    assert node.credential and node.credential["password"] == "password"


def test_base64_subscription_bundle():
    bundle = b"vless://uuid-1@1.2.3.4:443?encryption=none\ntrojan://pw@5.6.7.8:443\n"
    encoded = base64.b64encode(bundle)
    nodes = parse_payload(encoded, spec("subscription"))
    assert {n.protocol for n in nodes} == {Protocol.VLESS, Protocol.TROJAN}


def test_sniff_kind_and_bad_payload():
    assert "JsonParser" in sniff_kind(b'{"a":1}', spec("json"))
    assert "PlainParser" in sniff_kind(b"1.2.3.4:8080\n", spec("plain"))
    with pytest.raises(ParseError):
        # A YAML mapping that is not Clash config still must not crash silently.
        parse_payload(b"{", spec("json"))
