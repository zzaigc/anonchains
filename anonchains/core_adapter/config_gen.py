"""Generate multi-hop core configurations.

sing-box chains hops with ``outbound.detour``; Xray chains them with
``streamSettings.sockopt.dialerProxy`` (verified against Xray-core v26.x).
For a chain A -> B -> C -> target, the final outbound C is selected by the
route, C dials through B, and B dials through A.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .node_outbound import to_singbox_outbound, to_xray_outbound

if TYPE_CHECKING:
    from ..chain.builder import Chain


class CoreConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GeneratedConfig:
    core: str
    config: dict[str, Any]
    warnings: tuple[str, ...]
    listen_host: str
    listen_port: int
    exported_hops: int

    def to_json(self) -> str:
        return json.dumps(self.config, indent=2, ensure_ascii=False)


def _hop_tags(count: int) -> list[str]:
    return [f"anonchains-hop-{index + 1}" for index in range(count)]


def generate_singbox(
    chain: Chain,
    *,
    listen_host: str = "127.0.0.1",
    listen_port: int = 1080,
) -> GeneratedConfig:
    warnings: list[str] = []
    outbounds: list[dict[str, Any]] = []
    tags = _hop_tags(chain.hops)
    previous: str | None = None

    for node, tag in zip(chain.nodes, tags):
        outbound = to_singbox_outbound(node, tag)
        if outbound is None:
            warnings.append(
                f"hop {node.summary()}: {node.protocol.value} is not expressible by sing-box; skipped"
            )
            continue
        if previous:
            outbound["detour"] = previous
        outbounds.append(outbound)
        previous = tag

    if not outbounds or previous is None:
        raise CoreConfigError("no chain hop could be translated into a sing-box outbound")
    outbounds.append({"type": "direct", "tag": "anonchains-direct"})

    config: dict[str, Any] = {
        "log": {"level": "warn", "timestamp": True},
        # Deliberately no custom DNS server: a hardcoded public resolver may be
        # unreachable behind free proxies. See docs/08 for the DNS leak caveat.
        "dns": {"strategy": "prefer_ipv4"},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "anonchains-in",
                "listen": listen_host,
                "listen_port": listen_port,
                "sniff": False,
            }
        ],
        "outbounds": outbounds,
        "route": {"final": previous, "auto_detect_interface": True},
    }
    warnings.append(
        "DNS uses the core's default resolver; configure a DoH server with a "
        "detour in sing-box if strict DNS-leak protection is required."
    )
    return GeneratedConfig(
        core="sing-box",
        config=config,
        warnings=tuple(warnings),
        listen_host=listen_host,
        listen_port=listen_port,
        exported_hops=len(outbounds) - 1,
    )


def generate_xray(
    chain: Chain,
    *,
    listen_host: str = "127.0.0.1",
    listen_port: int = 1080,
) -> GeneratedConfig:
    warnings: list[str] = []
    outbounds: list[dict[str, Any]] = [
        {"tag": "anonchains-deny", "protocol": "blackhole", "settings": {}}
    ]
    tags = _hop_tags(chain.hops)
    previous: str | None = None

    for node, tag in zip(chain.nodes, tags):
        outbound = to_xray_outbound(node, tag)
        if outbound is None:
            warnings.append(
                f"hop {node.summary()}: {node.protocol.value} is not supported by the Xray path; skipped"
            )
            continue
        if previous:
            stream = outbound.setdefault("streamSettings", {})
            stream.setdefault("sockopt", {})["dialerProxy"] = previous
        outbounds.append(outbound)
        previous = tag

    if previous is None:
        raise CoreConfigError("no chain hop could be translated into an Xray outbound")

    config: dict[str, Any] = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "anonchains-in",
                "listen": listen_host,
                "port": listen_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        ],
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["anonchains-in"],
                    "network": "tcp",
                    "outboundTag": previous,
                }
            ],
        },
    }
    return GeneratedConfig(
        core="xray",
        config=config,
        warnings=tuple(warnings),
        listen_host=listen_host,
        listen_port=listen_port,
        exported_hops=len(outbounds) - 1,
    )


def generate(
    chain: Chain,
    core: str,
    *,
    listen_host: str = "127.0.0.1",
    listen_port: int = 1080,
) -> GeneratedConfig:
    if core == "xray":
        return generate_xray(chain, listen_host=listen_host, listen_port=listen_port)
    if core == "sing-box":
        return generate_singbox(chain, listen_host=listen_host, listen_port=listen_port)
    raise CoreConfigError(f"unknown core: {core}")
