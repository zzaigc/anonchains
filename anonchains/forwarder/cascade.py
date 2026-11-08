"""Built-in Python N-hop cascade forwarder (HTTP / SOCKS4 / SOCKS5).

Every hop is a plain TCP tunnel: after a proxy accepts a CONNECT / SOCKS
command, the stream is a raw byte pipe to the next address, so the next hop's
handshake can be written on the very same connection. Repeating this builds an
arbitrarily long chain with no LD_PRELOAD and no external binary.

Limitations (documented in docs/06-protocol-adapters.md):
* Only natively-forwardable protocols participate; exotic protocols are
  delegated to sing-box / xray.
* An HTTPS (TLS-to-proxy) hop must be the LAST hop, because ``start_tls``
  cannot be nested on a single StreamWriter.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import TYPE_CHECKING

from ..models.proxy_node import NATIVE_PROTOCOLS, Protocol, ProxyNode
from ..validator.probes import (
    Tunnel,
    UnsupportedProtocol,
    http_connect_to,
    socks4_connect_to,
    socks5_connect_to,
    socks5_greet,
)

if TYPE_CHECKING:
    from ..chain.builder import Chain

log = logging.getLogger(__name__)

# Larger copies amortise syscalls on multi-hop tunnels; matches the local
# entry server's StreamReader limit so no extra buffering layer is needed.
PUMP_CHUNK = 256 * 1024


class CascadeError(ConnectionError):
    pass


def _remaining(deadline: float) -> float:
    left = deadline - asyncio.get_running_loop().time()
    if left <= 0:
        raise asyncio.TimeoutError("cascade deadline exceeded")
    return left


async def _hop(
    tunnel: Tunnel,
    node: ProxyNode,
    target_host: str,
    target_port: int,
    deadline: float,
    *,
    is_last: bool,
) -> None:
    if node.protocol not in NATIVE_PROTOCOLS:
        raise UnsupportedProtocol(f"{node.protocol.value} needs an external core")
    timeout = _remaining(deadline)
    if node.protocol is Protocol.HTTPS:
        if not is_last:
            raise UnsupportedProtocol("an HTTPS hop must be the last hop of a native chain")
        context = ssl.create_default_context()
        await asyncio.wait_for(
            tunnel.writer.start_tls(context, server_hostname=node.host), timeout
        )
    if node.protocol is Protocol.SOCKS5:
        await socks5_greet(tunnel, node.credential, timeout)
        await socks5_connect_to(tunnel, target_host, target_port, timeout)
    elif node.protocol is Protocol.SOCKS4:
        await socks4_connect_to(tunnel, target_host, target_port, timeout)
    else:
        await http_connect_to(tunnel, target_host, target_port, node.credential, timeout)


async def cascade_connect(
    nodes: list[ProxyNode] | tuple[ProxyNode, ...],
    target_host: str,
    target_port: int,
    timeout: float,
) -> Tunnel:
    """Establish a raw tunnel to ``target_host:target_port`` through all hops."""
    if not nodes:
        raise CascadeError("empty chain")
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout)
    first = nodes[0]
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(first.host, first.port), _remaining(deadline)
        )
        tunnel = Tunnel(reader, writer)
    except (OSError, asyncio.TimeoutError) as exc:
        raise CascadeError(f"cannot reach hop 1 ({first.summary()}): {type(exc).__name__}") from exc

    index = 0
    try:
        count = len(nodes)
        for index, node in enumerate(nodes):
            is_last = index == count - 1
            if is_last:
                next_host, next_port = target_host, target_port
            else:
                next_host, next_port = nodes[index + 1].host, nodes[index + 1].port
            await _hop(tunnel, node, next_host, next_port, deadline, is_last=is_last)
    except (UnsupportedProtocol, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError,
            ssl.SSLError, ValueError) as exc:
        await tunnel.close()
        raise CascadeError(f"hop {index + 1} failed: {type(exc).__name__}: {exc}") from exc
    return tunnel


async def cascade_connect_chain(chain: Chain, target_host: str, target_port: int, timeout: float) -> Tunnel:
    """Convenience wrapper around :func:`cascade_connect`."""
    if chain.requires_external_core:
        raise CascadeError("chain contains protocols that require an external core")
    return await cascade_connect(chain.nodes, target_host, target_port, timeout)


async def pump(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
) -> tuple[int, int]:
    """Bidirectional byte pump. Returns bytes copied in each direction."""
    sent = [0, 0]

    async def copy(
        src: asyncio.StreamReader,
        dst: asyncio.StreamWriter,
        counter: int,
    ) -> None:
        try:
            while True:
                data = await src.read(PUMP_CHUNK)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
                sent[counter] += len(data)
        except (ConnectionResetError, BrokenPipeError, OSError, asyncio.TimeoutError):
            pass
        finally:
            try:
                if dst.can_write_eof():
                    dst.write_eof()
            except (OSError, RuntimeError):
                pass

    await asyncio.gather(
        copy(client_reader, remote_writer, 0),
        copy(remote_reader, client_writer, 1),
        return_exceptions=True,
    )
    return sent[0], sent[1]
