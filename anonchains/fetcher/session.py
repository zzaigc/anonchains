"""Shared aiohttp session with stealth profiles, robots handling and front proxies.

Front-proxy feedback loop: when the verified pool has usable HTTP/HTTPS nodes,
requests to source sites are routed through one of them. aiohttp natively
supports only HTTP proxies, so SOCKS and exotic protocols are deliberately
skipped here (they are handled by the built-in cascade / external core). Cold
start has an empty pool and therefore falls back to a direct connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp

from ..config.schema import AppConfig
from ..models.proxy_node import Protocol as NodeProtocol
from ..models.proxy_node import ProxyNode
from ..parser.base import SourceSpec
from ..stealth.fingerprint import HAS_CURL_CFFI, FingerprintEngine
from ..utils.net import proxy_url

log = logging.getLogger(__name__)

_DownloadResult = tuple[int, bytes, str | None, bool, str | None]
_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class FetchResponse:
    status: int = 0
    payload: bytes = b""
    etag: str | None = None
    not_modified: bool = False
    blocked: bool = False
    error: str | None = None
    elapsed_ms: float = 0.0
    front_used: str | None = None


class FrontProxyProvider(Protocol):
    def pick(self, exclude_host: str | None = None) -> ProxyNode | None: ...


class CallableFrontProvider:
    """Adapts a plain callable to :class:`FrontProxyProvider`."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def pick(self, exclude_host: str | None = None) -> ProxyNode | None:
        return self._fn(exclude_host)


class FetchSession:
    """One shared connection pool for a crawl run."""

    def __init__(
        self,
        config: AppConfig,
        engine: FingerprintEngine,
        front: FrontProxyProvider | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.front = front
        self._session: aiohttp.ClientSession | None = None
        self._curl_sessions: dict[str, Any] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._request_count = 0
        self._warned_tls_fallback = False
        # Identical in-flight GETs share one download, so duplicate source
        # definitions never cost extra bandwidth.
        self._inflight: dict[tuple[Any, ...], asyncio.Task[_DownloadResult]] = {}
        self._dedup_hits = 0

    # --- lifecycle ----------------------------------------------------

    async def __aenter__(self) -> FetchSession:
        await self._open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _open(self) -> None:
        crawler = self.config.crawler
        timeout = aiohttp.ClientTimeout(
            total=crawler.timeout_seconds + 5.0,
            connect=min(crawler.timeout_seconds, 10.0),
            sock_read=crawler.timeout_seconds,
        )
        connector = aiohttp.TCPConnector(
            limit=max(1, crawler.concurrency),
            limit_per_host=max(2, min(8, crawler.concurrency // 2)),
            ttl_dns_cache=300,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        # auto_decompress makes gzip/deflate transparent, so the response cap
        # below applies to the decompressed payload (the one we actually parse).
        self._session = aiohttp.ClientSession(
            connector=connector, timeout=timeout, auto_decompress=True
        )

    async def close(self) -> None:
        for task in list(self._inflight.values()):
            task.cancel()
        self._inflight.clear()
        if self._dedup_hits:
            log.info("crawl traffic: %d duplicate downloads coalesced", self._dedup_hits)
        if self._session is not None:
            await self._session.close()
            self._session = None
        for session in self._curl_sessions.values():
            try:
                await session.close()
            except Exception:  # pragma: no cover - optional dependency
                pass
        self._curl_sessions.clear()

    async def _rotate(self) -> None:
        # Never close a pool while other downloads are using it.
        if self._inflight:
            log.debug("stealth: deferring pool rotation (%d downloads in flight)",
                      len(self._inflight))
            return
        await self.close()
        await self._open()
        log.debug("stealth: connection pool rotated after %d requests", self._request_count)

    # --- front proxy --------------------------------------------------

    def _pick_front(self, url: str) -> tuple[str | None, str | None]:
        if self.front is None:
            return None, None
        host = urlparse(url).hostname
        node = self.front.pick(exclude_host=host)
        if node is None or node.protocol not in {NodeProtocol.HTTP, NodeProtocol.HTTPS}:
            return None, None
        credential = node.credential or {}
        url_out = proxy_url(
            node.protocol.value,
            node.host,
            node.port,
            str(credential.get("username", "") or ""),
            str(credential.get("password", "") or ""),
        )
        return url_out, node.summary()

    # --- robots -------------------------------------------------------

    async def _robots_allowed(self, url: str) -> bool:
        if not self.config.crawler.respect_robots or self._session is None:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            parser = RobotFileParser()
            try:
                async with self._session.get(
                    base + "/robots.txt",
                    timeout=aiohttp.ClientTimeout(total=min(5.0, self.config.crawler.timeout_seconds)),
                ) as response:
                    if response.status == 200:
                        text = await response.text(errors="replace")
                        parser.parse(text.splitlines())
                    else:
                        parser.parse([])
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                parser.parse([])  # unreachable robots => treat as permissive
            self._robots[base] = parser
        parser = self._robots[base]
        return True if parser is None else parser.can_fetch("*", url)

    # --- fetch --------------------------------------------------------

    def _max_bytes(self) -> int:
        """Hard cap for one decompressed response (protects bandwidth + RAM)."""
        return max(64 * 1024, int(self.config.crawler.max_response_mb * 1024 * 1024))

    async def fetch(self, spec: SourceSpec, *, etag: str | None = None) -> FetchResponse:
        if not await self._robots_allowed(spec.url):
            return FetchResponse(blocked=True, error="robots.txt disallows this URL")

        profile = self.engine.build(extra_headers=spec.headers or None)
        await self.engine.pause(profile)
        headers = dict(profile.headers)
        if etag:
            headers["If-None-Match"] = etag
        proxy, front_used = self._pick_front(spec.url)
        self._request_count += 1
        if self.engine.should_rotate_session():
            await self._rotate()

        # Same URL + conditional headers + front proxy == one wire transfer.
        key = (spec.url, etag or "", tuple(sorted(headers.items())), proxy or "")
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._download(spec, headers, proxy, etag, profile.impersonate)
            )
            self._inflight[key] = task
            task.add_done_callback(lambda _done: self._inflight.pop(key, None))
        else:
            self._dedup_hits += 1

        started = time.monotonic()
        result = await asyncio.shield(task)
        return FetchResponse(
            status=result[0],
            payload=result[1],
            etag=result[2],
            not_modified=result[3],
            error=result[4],
            elapsed_ms=round((time.monotonic() - started) * 1000.0, 1),
            front_used=front_used,
        )

    async def _download(
        self,
        spec: SourceSpec,
        headers: dict[str, str],
        proxy: str | None,
        etag: str | None,
        impersonate: str | None,
    ) -> _DownloadResult:
        if impersonate and HAS_CURL_CFFI:
            return await self._fetch_curl(spec, headers, proxy, etag, impersonate)
        if impersonate and not self._warned_tls_fallback:
            log.warning(
                "stealth level 4 TLS impersonation needs curl_cffi; using standard aiohttp TLS. "
                "Install requirements-optional.txt to enable it."
            )
            self._warned_tls_fallback = True
        return await self._fetch_aiohttp(spec, headers, proxy, etag)

    async def _fetch_aiohttp(
        self, spec: SourceSpec, headers: dict[str, str], proxy: str | None, etag: str | None
    ) -> tuple[int, bytes, str | None, bool, str | None]:
        session = self._session
        if session is None:
            return (0, b"", None, False, "session not initialised")
        try:
            async with session.get(spec.url, headers=headers, proxy=proxy) as response:
                if response.status == 304:
                    return (304, b"", etag, True, None)
                if response.status >= 400:
                    return (response.status, b"", None, False, f"HTTP {response.status}")
                # Stream in bounded chunks: huge public lists cannot exhaust
                # bandwidth or memory, and parsing can start from a safe prefix.
                limit = self._max_bytes()
                parts: list[bytes] = []
                size = 0
                truncated = False
                async for chunk in response.content.iter_chunked(_CHUNK_BYTES):
                    remaining = limit - size
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(chunk) > remaining:
                        parts.append(chunk[:remaining])
                        truncated = True
                        break
                    parts.append(chunk)
                    size += len(chunk)
                if truncated:
                    log.warning(
                        "source %s truncated at %.1f MiB (crawler.max_response_mb)",
                        spec.name, self.config.crawler.max_response_mb,
                    )
                return (
                    response.status,
                    b"".join(parts),
                    response.headers.get("ETag"),
                    False,
                    None,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            return (0, b"", None, False, f"{type(exc).__name__}: {exc}")

    async def _fetch_curl(
        self,
        spec: SourceSpec,
        headers: dict[str, str],
        proxy: str | None,
        etag: str | None,
        impersonate: str,
    ) -> tuple[int, bytes, str | None, bool, str | None]:
        """Optional TLS-fingerprint path. Falls back to aiohttp on any mismatch."""
        try:
            from importlib import import_module

            AsyncSession = import_module("curl_cffi.requests").AsyncSession
        except (ImportError, AttributeError):  # pragma: no cover - optional
            return await self._fetch_aiohttp(spec, headers, proxy, etag)
        try:
            session = self._curl_sessions.get(impersonate)
            if session is None:
                session = AsyncSession(
                    impersonate=impersonate, timeout=self.config.crawler.timeout_seconds
                )
                self._curl_sessions[impersonate] = session
            kwargs: dict[str, Any] = {"headers": headers}
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}
            response = await session.get(spec.url, **kwargs)
            status = int(response.status_code)
            if status == 304:
                return (304, b"", etag, True, None)
            if status >= 400:
                return (status, b"", None, False, f"HTTP {status}")
            payload = bytes(response.content)
            limit = self._max_bytes()
            if len(payload) > limit:
                log.warning(
                    "source %s truncated at %.1f MiB (crawler.max_response_mb)",
                    spec.name, self.config.crawler.max_response_mb,
                )
                payload = payload[:limit]
            return (status, payload, response.headers.get("ETag"), False, None)
        except Exception as exc:  # pragma: no cover - optional dependency variance
            log.debug("curl_cffi fetch failed, falling back: %s", type(exc).__name__)
            return await self._fetch_aiohttp(spec, headers, proxy, etag)
