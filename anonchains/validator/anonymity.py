"""Exit-IP echo, anonymity classification and node scoring.

A proxy is only "usable" if it can actually fetch a public echo endpoint and
its exit IP differs from our direct IP. Response headers are inspected for
``X-Forwarded-For`` / ``Via`` leakage to grade transparency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import aiohttp

from ..models.proxy_node import NATIVE_PROTOCOLS, Anonymity, Protocol, ProxyNode
from ..utils.net import clamp
from .probes import UnsupportedProtocol, open_tunnel

log = logging.getLogger(__name__)

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_LEAK_HEADERS = ("x-forwarded-for", "via", "x-real-ip", "forwarded", "x-proxy-id")
_UA = "anonchains-validator/0.1"


@dataclass(frozen=True, slots=True)
class EchoResult:
    ok: bool = False
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # type: ignore[arg-type]
    exit_ip: str | None = None
    latency_ms: float = 0.0
    anonymity: Anonymity = Anonymity.UNKNOWN
    skipped: bool = False
    reason: str | None = None


def extract_ip(body: str) -> str | None:
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            for key in ("ip", "origin", "query", "address"):
                value = data.get(key)
                if isinstance(value, str):
                    match = _IPV4.search(value)
                    if match:
                        return match.group(0)
    except (ValueError, TypeError):
        pass
    match = _IPV4.search(body)
    return match.group(0) if match else None


def classify(body: str, headers: dict[str, str], exit_ip: str | None, our_ip: str | None) -> tuple[Anonymity, bool]:
    """Return ``(anonymity, effective)``; ``effective`` is False if the proxy
    did not actually change the exit IP."""
    if our_ip and ((exit_ip and exit_ip == our_ip) or our_ip in body and not exit_ip):
        return Anonymity.TRANSPARENT, False
    if any(name in headers for name in _LEAK_HEADERS):
        return Anonymity.ANONYMOUS, True
    if exit_ip:
        return Anonymity.ELITE, True
    return Anonymity.UNKNOWN, True


def compute_score(
    latency_ms: float | None,
    anonymity: Anonymity,
    success_count: int,
    fail_count: int,
) -> float:
    score = 100.0
    if latency_ms is not None:
        score -= min(60.0, latency_ms / 50.0)
    score += {
        Anonymity.ELITE: 8.0,
        Anonymity.ANONYMOUS: 3.0,
        Anonymity.TRANSPARENT: -25.0,
        Anonymity.UNKNOWN: -5.0,
    }[anonymity]
    score += min(10, success_count) * 0.5
    score -= min(70, fail_count * 12)
    return float(clamp(score, 0.0, 100.0))


def _parse_http_response(raw_header: bytes, body: bytes) -> tuple[int, dict[str, str], str]:
    text = raw_header.decode("latin-1", "replace")
    lines = text.split("\r\n")
    status = 0
    if lines:
        parts = lines[0].split()
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return status, headers, body.decode("utf-8", "replace")


class EchoChecker:
    """Shared HTTP session + direct-IP baseline for one validation batch."""

    def __init__(
        self,
        echo_urls: tuple[str, ...],
        timeout: float = 8.0,
        *,
        our_ip: str | None = None,
    ) -> None:
        self.echo_urls = echo_urls or ("https://api.ipify.org?format=json",)
        self.timeout = timeout
        self.our_ip = our_ip
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> EchoChecker:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            headers={"User-Agent": _UA, "Accept": "*/*"},
        )
        if self.our_ip is None:
            self.our_ip = await self._direct_ip()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def echo_target(self) -> tuple[str, int]:
        parsed = urlparse(self.echo_urls[0])
        host = parsed.hostname or "api.ipify.org"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port

    async def _direct_ip(self) -> str | None:
        if self._session is None:
            return None
        for url in self.echo_urls:
            try:
                async with self._session.get(url) as response:
                    body = await response.text(errors="replace")
                    ip = extract_ip(body)
                    if ip:
                        return ip
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                log.debug("direct echo failed for %s: %s", url, type(exc).__name__)
        log.info("direct IP baseline unavailable; anonymity grading is best-effort")
        return None

    async def check(self, node: ProxyNode) -> EchoResult:
        if node.protocol not in NATIVE_PROTOCOLS:
            return EchoResult(skipped=True, reason="requires external core")
        if node.protocol in {Protocol.HTTP, Protocol.HTTPS}:
            return await self._check_http_proxy(node)
        return await self._check_socks_tunnel(node)

    async def _check_http_proxy(self, node: ProxyNode) -> EchoResult:
        from .probes import node_proxy_url

        if self._session is None:
            return EchoResult(reason="session not initialised")
        started = time.monotonic()
        try:
            async with self._session.get(
                self.echo_urls[0], proxy=node_proxy_url(node)
            ) as response:
                body = await response.text(errors="replace")
                headers = {k.lower(): v for k, v in response.headers.items()}
                latency = (time.monotonic() - started) * 1000
                if response.status >= 400:
                    return EchoResult(reason=f"echo HTTP {response.status}", latency_ms=latency)
                exit_ip = extract_ip(body)
                anonymity, effective = classify(body, headers, exit_ip, self.our_ip)
                return EchoResult(
                    ok=effective,
                    body=body[:4096],
                    headers=headers,
                    exit_ip=exit_ip,
                    latency_ms=round(latency, 1),
                    anonymity=anonymity,
                    reason=None if effective else "exit IP equals direct IP (proxy ineffective)",
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
            return EchoResult(reason=f"{type(exc).__name__}: {exc}")

    async def _check_socks_tunnel(self, node: ProxyNode) -> EchoResult:
        parsed = urlparse(self.echo_urls[0])
        host = parsed.hostname or "api.ipify.org"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        started = time.monotonic()
        tunnel = None
        try:
            tunnel = await open_tunnel(node, host, port, self.timeout)
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                await asyncio.wait_for(
                    tunnel.writer.start_tls(context, server_hostname=host), self.timeout
                )
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {_UA}\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            )
            tunnel.writer.write(request.encode())
            await asyncio.wait_for(tunnel.writer.drain(), self.timeout)
            raw_header = await asyncio.wait_for(tunnel.reader.readuntil(b"\r\n\r\n"), self.timeout)
            try:
                body_bytes = await asyncio.wait_for(tunnel.reader.read(), self.timeout)
            except asyncio.TimeoutError:
                body_bytes = b""
            status, headers, body = _parse_http_response(raw_header, body_bytes)
            latency = (time.monotonic() - started) * 1000
            if status >= 400:
                return EchoResult(reason=f"echo HTTP {status}", latency_ms=latency)
            exit_ip = extract_ip(body)
            anonymity, effective = classify(body, headers, exit_ip, self.our_ip)
            return EchoResult(
                ok=effective,
                body=body[:4096],
                headers=headers,
                exit_ip=exit_ip,
                latency_ms=round(latency, 1),
                anonymity=anonymity,
                reason=None if effective else "exit IP equals direct IP (proxy ineffective)",
            )
        except (UnsupportedProtocol, OSError, asyncio.TimeoutError,
                asyncio.IncompleteReadError, ssl.SSLError, ValueError) as exc:
            return EchoResult(reason=f"{type(exc).__name__}: {exc}")
        finally:
            if tunnel is not None:
                await tunnel.close()
