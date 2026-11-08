"""Local mixed inbound (SOCKS4 / SOCKS5 / HTTP CONNECT) exposing a chain.

Bind to loopback only. Every accepted connection is tunnelled through the
active chain by the built-in cascade, so ordinary applications can use
``127.0.0.1:<port>`` as a SOCKS5 or HTTP proxy without any LD_PRELOAD tricks.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any, Callable

from .cascade import CascadeError, cascade_connect, pump

if TYPE_CHECKING:
    from ..chain.builder import Chain

log = logging.getLogger(__name__)

ChainProvider = Callable[[], "Chain | None"]


class LocalEntryServer:
    def __init__(
        self,
        host: str,
        port: int,
        chain_provider: ChainProvider,
        *,
        timeout: float = 30.0,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("local entry must bind to a loopback address")
        self.host = host
        self.requested_port = port
        self.chain_provider = chain_provider
        self.timeout = timeout
        self._server: asyncio.Server | None = None
        self.connections = 0
        self.bytes_out = 0
        self.bytes_in = 0

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "host": self.host,
            "port": self.bound_port,
            "connections": self.connections,
            "bytes_out": self.bytes_out,
            "bytes_in": self.bytes_in,
        }

    async def start(self) -> int:
        if self.running:
            return self.bound_port or self.requested_port
        self._server = await asyncio.start_server(
            self._handle, self.host, self.requested_port, limit=256 * 1024
        )
        self.connections = 0
        log.info("local entry listening on %s:%s", self.host, self.bound_port)
        return self.bound_port or self.requested_port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            log.info("local entry stopped")

    # --- dispatch -----------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            first = await asyncio.wait_for(reader.readexactly(1), self.timeout)
            if first == b"\x05":
                await self._socks5(reader, writer)
            elif first == b"\x04":
                await self._socks4(reader, writer)
            else:
                await self._http(reader, writer, first)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError,
                OSError, ValueError, UnicodeError) as exc:
            log.debug("local connection closed: %s", type(exc).__name__)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    def _chain_or_none(self):
        chain = self.chain_provider()
        if chain is None or chain.hops == 0:
            log.warning("local entry request refused: no active chain")
            return None
        if chain.requires_external_core:
            log.warning("local entry request refused: chain needs an external core")
            return None
        return chain

    async def _tunnel(self, chain, host: str, port: int):
        return await cascade_connect(list(chain.nodes), host, port, self.timeout)

    async def _pump(self, reader, writer, tunnel) -> None:
        sent, received = await pump(reader, writer, tunnel.reader, tunnel.writer)
        self.bytes_out += sent
        self.bytes_in += received
        await tunnel.close()

    # --- SOCKS5 -------------------------------------------------------

    async def _socks5(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        count = (await asyncio.wait_for(reader.readexactly(1), self.timeout))[0]
        await asyncio.wait_for(reader.readexactly(count), self.timeout)
        writer.write(b"\x05\x00")  # no-auth only; local clients are trusted
        await writer.drain()

        head = await asyncio.wait_for(reader.readexactly(4), self.timeout)
        command, atyp = head[1], head[3]
        if command != 0x01:
            writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        if atyp == 0x01:
            host = socket.inet_ntoa(await asyncio.wait_for(reader.readexactly(4), self.timeout))
        elif atyp == 0x03:
            length = (await asyncio.wait_for(reader.readexactly(1), self.timeout))[0]
            host = (await asyncio.wait_for(reader.readexactly(length), self.timeout)).decode(
                "utf-8", "replace"
            )
        elif atyp == 0x04:
            host = socket.inet_ntop(
                socket.AF_INET6, await asyncio.wait_for(reader.readexactly(16), self.timeout)
            )
        else:
            writer.write(b"\x05\x08\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        port = int.from_bytes(await asyncio.wait_for(reader.readexactly(2), self.timeout), "big")

        chain = self._chain_or_none()
        if chain is None:
            writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        try:
            tunnel = await self._tunnel(chain, host, port)
        except (CascadeError, OSError, asyncio.TimeoutError) as exc:
            log.debug("socks5 chain failed for %s:%s: %s", host, port, type(exc).__name__)
            writer.write(b"\x05\x05\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
        await writer.drain()
        await self._pump(reader, writer, tunnel)

    # --- SOCKS4 / SOCKS4a ---------------------------------------------

    async def _socks4(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body = await asyncio.wait_for(reader.readexactly(7), self.timeout)
        port = int.from_bytes(body[1:3], "big")
        ip_bytes = body[3:7]
        await asyncio.wait_for(reader.readuntil(b"\x00"), self.timeout)
        if ip_bytes[:3] == b"\x00\x00\x00" and ip_bytes[3] != 0:
            domain = await asyncio.wait_for(reader.readuntil(b"\x00"), self.timeout)
            host = domain[:-1].decode("utf-8", "replace")
        else:
            host = socket.inet_ntoa(ip_bytes)

        chain = self._chain_or_none()
        if chain is None:
            writer.write(b"\x00\x5B" + b"\x00" * 6)
            await writer.drain()
            return
        try:
            tunnel = await self._tunnel(chain, host, port)
        except (CascadeError, OSError, asyncio.TimeoutError):
            writer.write(b"\x00\x5B" + b"\x00" * 6)
            await writer.drain()
            return
        writer.write(b"\x00\x5A" + port.to_bytes(2, "big") + ip_bytes)
        await writer.drain()
        await self._pump(reader, writer, tunnel)

    # --- HTTP ---------------------------------------------------------

    async def _http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        first: bytes,
    ) -> None:
        rest = await asyncio.wait_for(reader.readuntil(b"\r\n"), self.timeout)
        request_line = (first + rest).decode("latin-1", "replace").strip()
        header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.timeout)
        header_lines = header_bytes.decode("latin-1", "replace").split("\r\n")[:-2]
        parts = request_line.split()
        if len(parts) < 2:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        method, target = parts[0].upper(), parts[1]
        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            try:
                port = int(port_text)
            except ValueError:
                port = 443
            await self._http_connect(reader, writer, host, port)
            return

        # Origin-form or absolute-form request: rewrite to a plain upstream GET.
        from urllib.parse import urlsplit

        split = urlsplit(target)
        if split.scheme and split.hostname:
            host = split.hostname
            port = split.port or (443 if split.scheme == "https" else 80)
            path = split.path or "/"
            if split.query:
                path += "?" + split.query
        else:
            host = ""
            for line in header_lines:
                if line.lower().startswith("host:"):
                    host = line.split(":", 1)[1].strip()
                    break
            port = 80
            path = target
            if not host:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                return
            if ":" in host:
                host, _, port_text = host.rpartition(":")
                port = int(port_text) if port_text.isdigit() else 80

        chain = self._chain_or_none()
        if chain is None:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            await writer.drain()
            return
        try:
            tunnel = await self._tunnel(chain, host, port)
        except (CascadeError, OSError, asyncio.TimeoutError):
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return
        forwarded = [f"{method} {path} HTTP/1.1"]
        for line in header_lines:
            lower = line.lower()
            if lower.startswith(("proxy-authorization:", "proxy-connection:", "connection:")):
                continue
            forwarded.append(line)
        forwarded.append("Connection: close")
        tunnel.writer.write(("\r\n".join(forwarded) + "\r\n\r\n").encode("latin-1"))
        await tunnel.writer.drain()
        await self._pump(reader, writer, tunnel)

    async def _http_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        chain = self._chain_or_none()
        if chain is None:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
            await writer.drain()
            return
        try:
            tunnel = await self._tunnel(chain, host, port)
        except (CascadeError, OSError, asyncio.TimeoutError):
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self._pump(reader, writer, tunnel)
