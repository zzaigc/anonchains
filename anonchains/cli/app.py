"""Application composition root.

Builds every component once, wires the feedback loops and owns their
lifetime. No network access happens during construction, so ``status`` and the
whole menu system work completely offline (important for first-run setup).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..chain import ChainBuilder, ChainPolicy, ChainRotator
from ..config.loader import ConfigManager, default_config_path
from ..core_adapter import CoreManager
from ..crypto.vault import Vault
from ..fetcher import Crawler
from ..forwarder import LocalEntryServer
from ..gateway import GatewayContext, GatewayServer, TokenManager
from ..logging_conf import setup_logging
from ..pools import CandidatePool, NodeStore, VerifiedPool
from ..scheduler import Scheduler
from ..sources.registry import SourceRegistry
from ..stealth import FingerprintEngine
from ..utils.paths import display_path
from ..validator import Validator

log = logging.getLogger(__name__)


class AppContext:
    def __init__(
        self,
        config_path: Path | None = None,
        *,
        base_dir: Path | None = None,
        enable_file_logging: bool = True,
    ) -> None:
        self.config_manager = ConfigManager(config_path, base_dir)
        self.config = self.config_manager.load()
        setup_logging(
            self.config.logging.level,
            self.config.logging.file if enable_file_logging else None,
            redact_logs=self.config.logging.redact,
            sample_window_seconds=self.config.logging.sample_window_seconds,
            force=True,
        )

        self.vault = Vault(
            self.config.vault.path,
            enabled=self.config.vault.enabled,
            kdf_preference=self.config.vault.kdf,
            algo=self.config.vault.cipher,
            key_ttl_seconds=self.config.vault.key_ttl_seconds,
            base_dir=self.config.base_dir,
        )
        self.candidate_store = NodeStore(
            self.config.pools.candidate_dir / "candidate.db",
            self.vault,
            batch_size=self.config.pools.batch_size,
            base_dir=self.config.base_dir,
        )
        self.verified_store = NodeStore(
            self.config.pools.verified_dir / "verified.db",
            self.vault,
            batch_size=self.config.pools.batch_size,
            base_dir=self.config.base_dir,
        )
        self.candidate_pool = CandidatePool(self.candidate_store)
        self.verified_pool = VerifiedPool(self.verified_store)

        sources_file = default_config_path().parent / "sources.yaml"
        self.registry = SourceRegistry.load(
            sources_file, [self.config.crawler.user_sources_file]
        )

        self.engine = FingerprintEngine(self.config.crawler.stealth_level)
        self.crawler = Crawler(
            self.config, self.registry, engine=self.engine, front=self.verified_pool
        )
        self.validator = Validator(self.config)
        self.builder = ChainBuilder.from_config(self.config)
        self.rotator = ChainRotator(
            self.builder,
            lambda: self.verified_pool.best(max(64, self.config.chain.hops * 4)),
        )
        self.core_manager = CoreManager(self.config)
        self.entry_server = LocalEntryServer(
            self.config.gateway.host,
            self.config.gateway.entry_port,
            lambda: self.rotator.current,
            timeout=self.config.effective_connect_timeout(),
        )
        self.token_manager = TokenManager(
            self.vault,
            self.config.pools.candidate_dir.parent / ".gateway_token",
            required=self.config.gateway.token_required,
        )
        self.scheduler = Scheduler(
            self.config,
            crawler=self.crawler,
            validator=self.validator,
            candidate_pool=self.candidate_pool,
            verified_pool=self.verified_pool,
            rotate_callback=self._rotate_callback,
        )
        self.api_context = GatewayContext(
            config_manager=self.config_manager,
            registry=self.registry,
            candidate_pool=self.candidate_pool,
            verified_pool=self.verified_pool,
            rotator=self.rotator,
            crawler=self.crawler,
            validator=self.validator,
            scheduler=self.scheduler,
            core_manager=self.core_manager,
            token_manager=self.token_manager,
            entry_server=self.entry_server,
            vault=self.vault,
        )
        self.gateway = GatewayServer(self.config_manager, self.api_context)

    async def _rotate_callback(self) -> None:
        self.rotator.rotate()

    # --- config changes -----------------------------------------------

    def apply_config(self) -> None:
        """Re-wire components after ``config_manager.set_value`` / hot reload."""
        self.config = self.config_manager.config
        self.engine = FingerprintEngine(self.config.crawler.stealth_level)
        self.crawler.config = self.config
        self.crawler.set_stealth_level(self.config.crawler.stealth_level)
        self.validator = Validator(self.config)
        self.scheduler.validator = self.validator
        self.api_context.validator = self.validator
        self.builder.update_policy(
            ChainPolicy(
                hops=self.config.chain.hops,
                mode=self.config.chain.mode,
                rotation_seconds=self.config.chain.rotation_seconds,
                prefer_protocols=tuple(self.config.chain.prefer_protocols),
                allow_reuse=self.config.chain.allow_reuse,
            )
        )
        self.rotator.set_policy(self.builder.policy)
        self.entry_server.timeout = self.config.effective_connect_timeout()

    def set_config_value(self, section: str, option: str, value: Any) -> None:
        self.config_manager.set_value(section, option, value)
        self.apply_config()

    # --- lifecycle ----------------------------------------------------

    async def startup(self) -> tuple[bool, str]:
        if self.config.gateway.enabled:
            return await self.gateway.start()
        return False, "gateway disabled by config (gateway.enabled = false)"

    async def shutdown(self) -> None:
        await self.gateway.stop()
        await self.scheduler.stop()
        self.candidate_store.close()
        self.verified_store.close()
        self.vault.lock()

    def status(self) -> dict[str, Any]:
        return {
            "config": {
                "hops": self.config.chain.hops,
                "mode": self.config.chain.mode,
                "stealth_level": self.config.crawler.stealth_level,
                "gateway_enabled": self.config.gateway.enabled,
                "config_file": display_path(self.config_manager.path, self.config.base_dir),
            },
            "sources": self.registry.stats(),
            "pools": {
                "candidate": self.candidate_pool.stats(),
                "verified": self.verified_pool.stats(),
            },
            "chain": self.rotator.status(),
            "vault": self.vault.status(),
            "gateway": self.gateway.status(),
            "core": self.core_manager.status(),
            "scheduler": self.scheduler.last_report.as_dict()
            if self.scheduler.last_report
            else {"status": "idle"},
        }
