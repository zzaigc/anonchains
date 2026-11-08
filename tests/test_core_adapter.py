from __future__ import annotations

from anonchains.chain import Chain, ChainBuilder, ChainPolicy
from anonchains.core_adapter import (
    CoreManager,
    generate_singbox,
    generate_xray,
    to_singbox_outbound,
    to_xray_outbound,
)
from anonchains.models.proxy_node import Protocol, ProxyNode


def chain_of(*protocols: Protocol) -> Chain:
    nodes = [
        ProxyNode(protocol=p, host=f"10.0.0.{i + 1}", port=1000 + i,
                  credential={"uuid": f"uuid-{i}", "password": f"pw-{i}"},
                  params={"sni": "example.com", "security": "tls"})
        for i, p in enumerate(protocols)
    ]
    return ChainBuilder(ChainPolicy(hops=len(nodes), allow_reuse=True)).build(nodes)


def test_singbox_detour_chain():
    generated = generate_singbox(
        chain_of(Protocol.HTTP, Protocol.SOCKS5, Protocol.VLESS, Protocol.WIREGUARD, Protocol.VMESS),
        listen_port=1080,
    )
    assert generated.exported_hops == 5
    outbounds = generated.config["outbounds"]
    hops = [o for o in outbounds if o["tag"].startswith("anonchains-hop")]
    assert hops[0].get("detour") is None
    assert hops[1]["detour"] == hops[0]["tag"]
    assert hops[2]["detour"] == hops[1]["tag"]
    assert generated.config["route"]["final"] == hops[-1]["tag"]
    assert generated.config["inbounds"][0]["listen_port"] == 1080
    assert all("REPLACE" not in str(o) for o in outbounds)


def test_xray_dialerproxy_chain():
    generated = generate_xray(
        chain_of(Protocol.SOCKS5, Protocol.VLESS, Protocol.TROJAN), listen_port=1081
    )
    outbounds = generated.config["outbounds"]
    assert outbounds[0]["protocol"] == "blackhole"  # fail closed
    hops = [o for o in outbounds if o["tag"].startswith("anonchains-hop")]
    assert len(hops) == 3
    assert hops[0]["streamSettings"].get("sockopt", {}).get("dialerProxy") is None
    assert hops[1]["streamSettings"]["sockopt"]["dialerProxy"] == hops[0]["tag"]
    assert hops[2]["streamSettings"]["sockopt"]["dialerProxy"] == hops[1]["tag"]
    assert generated.config["routing"]["rules"][0]["outboundTag"] == hops[-1]["tag"]


def test_xray_reports_unsupported_hops():
    generated = generate_xray(chain_of(Protocol.SOCKS5, Protocol.WIREGUARD))
    assert generated.exported_hops == 1
    assert any("not supported" in w for w in generated.warnings)


def test_outbound_field_mapping():
    vless = ProxyNode(
        protocol=Protocol.VLESS, host="1.2.3.4", port=443,
        credential={"uuid": "UUID-X"}, params={"sni": "cdn.example.com", "security": "tls"},
    )
    sing = to_singbox_outbound(vless, "hop-1")
    assert sing is not None
    assert sing["type"] == "vless" and sing["uuid"] == "UUID-X"
    assert sing["tls"]["server_name"] == "cdn.example.com"
    xray = to_xray_outbound(vless, "hop-1")
    assert xray is not None
    assert xray["protocol"] == "vless"
    assert xray["settings"]["vnext"][0]["users"][0]["id"] == "UUID-X"
    assert xray["streamSettings"]["security"] == "tls"

    ss = ProxyNode(protocol=Protocol.SHADOWSOCKS, host="1.2.3.4", port=8388,
                   credential={"method": "aes-128-gcm", "password": "pw"})
    xray_ss = to_xray_outbound(ss, "s")
    sing_ss = to_singbox_outbound(ss, "s")
    assert xray_ss is not None and xray_ss["protocol"] == "shadowsocks"
    assert sing_ss is not None and sing_ss["method"] == "aes-128-gcm"


def test_core_manager_degrades_without_binary(config_copy, monkeypatch):
    monkeypatch.setattr("anonchains.core_adapter.process.shutil.which", lambda _: None)
    manager = CoreManager(config_copy.config)
    assert manager.detect() is None
    assert "sing-box" in manager.install_hint()
    status = manager.status()
    assert status["running"] is False and status["install_hint"]
