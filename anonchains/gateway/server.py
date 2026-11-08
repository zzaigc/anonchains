"""Gateway lifecycle: REST app + local proxy entry, driven by the config flag.

``gateway.enabled`` in anonchains.conf is the single master switch. Setting it
to false stops both the management API and the local proxy entry. The CLI can
override once per process with ``start(force=True)`` for diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from ..config.loader import ConfigManager
from ..utils.concurrency import cancel_all
from .api import GatewayContext, create_app

log = logging.getLogger(__name__)


class GatewayServer:
    def __init__(self, config_manager: ConfigManager, ctx: GatewayContext) -> None:
        self.config_manager = config_manager
        self.ctx = ctx
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None

    async def start(self, *, force: bool = False) -> tuple[bool, str]:
        cfg = self.config_manager.config
        if not cfg.gateway.enabled and not force:
            return False, "gateway disabled by config (gateway.enabled = false)"
        if self.running:
            return True, f"gateway already running on {cfg.gateway.host}:{cfg.gateway.port}"

        try:
            runner = web.AppRunner(create_app(self.ctx), access_log=log)
            await runner.setup()
            site = web.TCPSite(runner, cfg.gateway.host, cfg.gateway.port)
            await site.start()
        except OSError as exc:
            await self._cleanup_runner()
            message = f"cannot bind {cfg.gateway.host}:{cfg.gateway.port}: {exc}"
            log.warning(message)
            return False, message
        self._runner, self._site = runner, site

        entry_message = ""
        if self.ctx.entry_server is not None:
            try:
                port = await self.ctx.entry_server.start()
                entry_message = f", local proxy entry on port {port}"
            except OSError as exc:
                entry_message = f", local entry failed: {type(exc).__name__}"

        token = self.ctx.token_manager.token
        if token:
            log.warning("local API token (store it securely): %s", token)
        log.info("gateway started on http://%s:%d%s", cfg.gateway.host, cfg.gateway.port, entry_message)
        return True, f"gateway on {cfg.gateway.host}:{cfg.gateway.port}{entry_message}"

    async def stop(self) -> None:
        await self._cleanup_runner()
        if self.ctx.entry_server is not None:
            await self.ctx.entry_server.stop()
        if self.ctx.background:
            await cancel_all(list(self.ctx.background))
        await self.ctx.core_manager.stop()
        log.info("gateway stopped")

    async def _cleanup_runner(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def apply_config_switch(self) -> tuple[bool, str]:
        """Reconcile runtime state with ``gateway.enabled`` after a hot reload."""
        enabled = self.config_manager.config.gateway.enabled
        if enabled and not self.running:
            return await self.start()
        if not enabled and self.running:
            await self.stop()
            return True, "gateway stopped (disabled by config)"
        return self.running, "no change"

    def status(self) -> dict[str, Any]:
        cfg = self.config_manager.config
        return {
            "running": self.running,
            "enabled": cfg.gateway.enabled,
            "host": cfg.gateway.host,
            "api_port": cfg.gateway.port,
            "entry_port": cfg.gateway.entry_port,
            "base_url": f"http://{cfg.gateway.host}:{cfg.gateway.port}",
            "entry": self.ctx.entry_server.status() if self.ctx.entry_server else None,
            "token": self.ctx.token_manager.status(),
            "background_tasks": len(self.ctx.background),
        }
