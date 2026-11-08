"""Low-level, dependency-free proxy handshake probes.

These operate directly on asyncio streams so SOCKS4/SOCKS5 can be validated
without aiohttp-socks. They verify that a node actually accepts a CONNECT to a
target -- a plain TCP accept is not proof that a proxy is usable.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..models.proxy_node import NATIVE_PROTOCOLS, Protocol, ProxyNode
from ..utils.net import proxy_url

log = logging.getLogger(__name__)


class UnsupportedProtocol(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    latency_ms: float | None = None
    skipped: bool = False
    reason: str | None = None


@dataclass(slots=True)
class Tunnel:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (OSError, ConnectionError, asyncio.TimeoutError):
            pass


async def _connect(host: str, port: int, timeout: float) -> Tunnel:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    return Tunnel(reader, writer)


async def _read_exact(reader: asyncio.StreamReader, size: int, timeout: float) -> bytes:
    return await asyncio.wait_for(reader.readexactly(size), timeout=timeout)


async def socks5_greet(
    tunnel: Tunnel, credential: dict[str, Any] | None, timeout: float
) -> None:
    user = str((credential or {}).get("username", "") or "")
    password = str((credential or {}).get("password", "") or "")
    methods = b"\x05\x02\x00\x02" if user else b"\x05\x01\x00"
    tunnel.writer.write(methods)
    await asyncio.wait_for(tunnel.writer.drain(), timeout)
    reply = await _read_exact(tunnel.reader, 2, timeout)
    if reply[0] != 0x05 or reply[1] == 0xFF:
        raise UnsupportedProtocol("SOCKS5 authentication methods rejected")
    if reply[1] == 0x02:
        auth = (
            b"\x01"
            + bytes([len(user) & 0xFF])
            + user.encode()
            + bytes([len(password) & 0xFF])
            + password.encode()
        )
        tunnel.writer.write(auth)
        await asyncio.wait_for(tunnel.writer.drain(), timeout)
        status = await _read_exact(tunnel.reader, 2, timeout)
        if status != b"\x01\x00":
            raise UnsupportedProtocol("SOCKS5 authentication failed")
    elif reply[1] != 0x00:
        raise UnsupportedProtocol("unsupported SOCKS5 authentication method")


async def socks5_connect_to(
    tunnel: Tunnel, target_host: str, target_port: int, timeout: float
) -> None:
    try:
        address = b"\x01" + ipaddress.IPv4Address(target_host).packed
    except ipaddress.AddressValueError:
        encoded = target_host.encode("idna")[:255]
        address = b"\x03" + bytes([len(encoded)]) + encoded
    request = b"\x05\x01\x00" + address + target_port.to_bytes(2, "big")
    tunnel.writer.write(request)
    await asyncio.wait_for(tunnel.writer.drain(), timeout)
    head = await _read_exact(tunnel.reader, 4, timeout)
    if head[0] != 0x05 or head[1] != 0x00:
        raise UnsupportedProtocol(f"SOCKS5 CONNECT rejected (REP={head[1]})")
    atyp = head[3]
    if atyp == 0x01:
        skip = 4
    elif atyp == 0x04:
        skip = 16
    elif atyp == 0x03:
        length = (await _read_exact(tunnel.reader, 1, timeout))[0]
        skip = length
    else:
        raise UnsupportedProtocol("invalid SOCKS5 ATYP")
    await _read_exact(tunnel.reader, skip + 2, timeout)


async def socks4_connect_to(
    tunnel: Tunnel, target_host: str, target_port: int, timeout: float
) -> None:
    try:
        ip = ipaddress.IPv4Address(target_host).packed
    except ipaddress.AddressValueError:
        ip = b"\x00\x00\x00\x01"  # SOCKS4a: resolve remotely
    packet = (
        b"\x04\x01"
        + target_port.to_bytes(2, "big")
        + ip
        + b"\x00"
    )
    if ip == b"\x00\x00\x00\x01":
        packet += target_host.encode("idna") + b"\x00"
    tunnel.writer.write(packet)
    await asyncio.wait_for(tunnel.writer.drain(), timeout)
    reply = await _read_exact(tunnel.reader, 8, timeout)
    if reply[0] != 0x00 or reply[1] != 0x5A:
        raise UnsupportedProtocol(f"SOCKS4 CONNECT rejected (REP={reply[1]})")


async def http_connect_to(
    tunnel: Tunnel,
    target_host: str,
    target_port: int,
    credential: dict[str, Any] | None,
    timeout: float,
) -> None:
    lines = [
        f"CONNECT {target_host}:{target_port} HTTP/1.1",
        f"Host: {target_host}:{target_port}",
        "Proxy-Connection: Keep-Alive",
    ]
    user = str((credential or {}).get("username", "") or "")
    password = str((credential or {}).get("password", "") or "")
    if user:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        lines.append(f"Proxy-Authorization: Basic {token}")
    tunnel.writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await asyncio.wait_for(tunnel.writer.drain(), timeout)
    header = await asyncio.wait_for(tunnel.reader.readuntil(b"\r\n\r\n"), timeout)
    status_line = header.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = status_line.split()
    if len(parts) < 2 or parts[1] != "200":
        raise UnsupportedProtocol(f"HTTP CONNECT rejected: {status_line}")


async def open_tunnel(
    node: ProxyNode, target_host: str, target_port: int, timeout: float
) -> Tunnel:
    """Return a stream that is already connected to ``target_host`` via node."""
    if node.protocol not in NATIVE_PROTOCOLS:
        raise UnsupportedProtocol(f"{node.protocol.value} requires an external core")
    tunnel = await _connect(node.host, node.port, timeout)
    try:
        if node.protocol in {Protocol.SOCKS5, Protocol.SOCKS4}:
            if node.protocol is Protocol.SOCKS5:
                await socks5_greet(tunnel, node.credential, timeout)
                await socks5_connect_to(tunnel, target_host, target_port, timeout)
            else:
                await socks4_connect_to(tunnel, target_host, target_port, timeout)
        else:
            await http_connect_to(tunnel, target_host, target_port, node.credential, timeout)
    except Exception:
        await tunnel.close()
        raise
    return tunnel


async def probe_node(
    node: ProxyNode,
    timeout: float = 8.0,
    *,
    target_host: str = "example.com",
    target_port: int = 443,
) -> ProbeResult:
    """Handshake probe with latency measurement. Never raises for network errors."""
    if node.protocol not in NATIVE_PROTOCOLS:
        return ProbeResult(ok=False, skipped=True, reason="requires external core")
    started = time.monotonic()
    try:
        tunnel = await open_tunnel(node, target_host, target_port, timeout)
        await tunnel.close()
        return ProbeResult(ok=True, latency_ms=round((time.monotonic() - started) * 1000, 1))
    except (UnsupportedProtocol, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError,
            ConnectionError, ValueError) as exc:
        return ProbeResult(ok=False, reason=f"{type(exc).__name__}: {exc}")


def node_proxy_url(node: ProxyNode) -> str:
    credential = node.credential or {}
    return proxy_url(
        node.protocol.value,
        node.host,
        node.port,
        str(credential.get("username", "") or ""),
        str(credential.get("password", "") or ""),
    )
