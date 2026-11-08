from __future__ import annotations

import asyncio
import socket

import pytest

from anonchains.chain import Chain, ChainBuildError, ChainBuilder, ChainPolicy, ChainRotator
from anonchains.forwarder.cascade import cascade_connect, pump
from anonchains.forwarder.local_server import LocalEntryServer
from anonchains.models.proxy_node import Protocol, ProxyNode


def pool(count: int = 8) -> list[ProxyNode]:
    return [
        ProxyNode(
            protocol=Protocol.HTTP if i % 2 == 0 else Protocol.SOCKS5,
            host=f"10.{i}.0.1",
            port=8000 + i,
            score=100 - i,
        )
        for i in range(count)
    ]


def test_five_hops_and_no_hard_limit():
    builder = ChainBuilder(ChainPolicy(hops=5))
    chain = builder.build(pool())
    assert chain.hops == 5
    assert len({n.stable_key() for n in chain.nodes}) == 5
    assert chain.native_only
    assert not chain.requires_external_core

    huge = ChainBuilder(ChainPolicy(hops=120, allow_reuse=True)).build(pool(4))
    assert huge.hops == 120


def test_strict_requires_enough_distinct_nodes():
    with pytest.raises(ChainBuildError):
        ChainBuilder(ChainPolicy(hops=10, mode="strict")).build(pool(3))
    degraded = ChainBuilder(ChainPolicy(hops=10, mode="dynamic")).build(pool(3))
    assert degraded.hops == 3
    reused = ChainBuilder(ChainPolicy(hops=10, mode="strict", allow_reuse=True)).build(pool(3))
    assert reused.hops == 10


def test_rotation_modes():
    nodes = pool()
    for mode in ("strict", "dynamic", "random", "round_robin"):
        rotator = ChainRotator(ChainBuilder(ChainPolicy(hops=3, mode=mode)), lambda: nodes)
        first = rotator.rotate()
        assert first.hops == 3
        second = rotator.rotate()
        assert second.hops == 3
        assert rotator.status()["active"]


def test_round_robin_advances():
    rotator = ChainRotator(
        ChainBuilder(ChainPolicy(hops=2, mode="round_robin")), pool
    )
    one = rotator.rotate().summaries()
    two = rotator.rotate().summaries()
    assert one != two


def test_chain_serialisation_is_credential_free():
    node = ProxyNode(protocol=Protocol.HTTP, host="1.2.3.4", port=8080)
    node.credential = {"password": "must-not-appear"}
    chain = Chain(nodes=(node,), policy=ChainPolicy(hops=1))
    assert "must-not-appear" not in str(chain.as_dict())
    assert "credential" in chain.as_dict(include_secrets=True)["nodes"][0]


# --- local-only end-to-end cascade -------------------------------------

async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.read(1024)
        writer.write(b"pong")
        await writer.drain()
    finally:
        writer.close()


def _connect_proxy_factory():
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            while True:
                header = await asyncio.wait_for(reader.readline(), 5)
                if header in (b"\r\n", b"\n", b""):
                    break
            target = line.decode("latin-1").split()[1]
            host, _, port_text = target.rpartition(":")
            up_reader, upstream_writer = await asyncio.open_connection(host, int(port_text))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await pump(reader, writer, up_reader, upstream_writer)
        except Exception:
            pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
            writer.close()

    return handler


async def _local_infrastructure():
    echo = await asyncio.start_server(_echo, "127.0.0.1", 0)
    proxy1 = await asyncio.start_server(_connect_proxy_factory(), "127.0.0.1", 0)
    proxy2 = await asyncio.start_server(_connect_proxy_factory(), "127.0.0.1", 0)
    echo_port = echo.sockets[0].getsockname()[1]
    p1 = proxy1.sockets[0].getsockname()[1]
    p2 = proxy2.sockets[0].getsockname()[1]
    nodes = (
        ProxyNode(protocol=Protocol.HTTP, host="127.0.0.1", port=p1),
        ProxyNode(protocol=Protocol.HTTP, host="127.0.0.1", port=p2),
    )
    servers = (echo, proxy1, proxy2)
    async def close_all() -> None:
        for server in servers:
            server.close()
        await asyncio.gather(*(s.wait_closed() for s in servers))
    return nodes, echo_port, close_all


def test_cascade_two_hops_over_real_local_sockets():
    async def scenario():
        nodes, echo_port, close_all = await _local_infrastructure()
        try:
            tunnel = await cascade_connect(list(nodes), "127.0.0.1", echo_port, 8.0)
            tunnel.writer.write(b"ping")
            await tunnel.writer.drain()
            assert await asyncio.wait_for(tunnel.reader.readexactly(4), 5) == b"pong"
            await tunnel.close()
        finally:
            await close_all()

    asyncio.run(scenario())


def test_local_socks5_entry_uses_chain():
    async def scenario():
        nodes, echo_port, close_all = await _local_infrastructure()
        chain = Chain(nodes=nodes, policy=ChainPolicy(hops=2))
        server = LocalEntryServer("127.0.0.1", 0, lambda: chain, timeout=8.0)
        port = await server.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            assert await reader.readexactly(2) == b"\x05\x00"
            request = (
                b"\x05\x01\x00\x01"
                + socket.inet_aton("127.0.0.1")
                + echo_port.to_bytes(2, "big")
            )
            writer.write(request)
            await writer.drain()
            reply = await asyncio.wait_for(reader.readexactly(10), 5)
            assert reply[1] == 0x00, f"SOCKS5 refused: {reply!r}"
            writer.write(b"ping")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), 5) == b"pong"
            writer.close()
            assert server.status()["connections"] >= 1
        finally:
            await server.stop()
            await close_all()

    asyncio.run(scenario())
