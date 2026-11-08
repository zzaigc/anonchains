from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer

from anonchains.chain import ChainBuilder, ChainPolicy, ChainRotator
from anonchains.config.loader import ConfigManager
from anonchains.core_adapter import CoreManager
from anonchains.gateway import GatewayContext, GatewayServer, TokenManager, create_app
from anonchains.pools import CandidatePool, NodeStore, VerifiedPool
from anonchains.scheduler import Scheduler
from anonchains.sources.registry import SourceRegistry
from anonchains.validator.batch import Validator


def build_context(tmp_path, config_copy: ConfigManager, sources_file) -> GatewayContext:
    candidate = CandidatePool(NodeStore(tmp_path / "candidate.db"))
    verified = VerifiedPool(NodeStore(tmp_path / "verified.db"))
    config = config_copy.config
    token = TokenManager(None, tmp_path / "token.json", required=True)
    return GatewayContext(
        config_manager=config_copy,
        registry=SourceRegistry.load(sources_file),
        candidate_pool=candidate,
        verified_pool=verified,
        rotator=ChainRotator(ChainBuilder(ChainPolicy(hops=5)), lambda: []),
        crawler=MagicMock(),
        validator=Validator(config),
        scheduler=Scheduler(
            config, crawler=MagicMock(), validator=Validator(config),
            candidate_pool=candidate, verified_pool=verified,
        ),
        core_manager=CoreManager(config),
        token_manager=token,
    )


def test_gateway_rest_offline(tmp_path, config_copy, sources_file):
    async def scenario():
        ctx = build_context(tmp_path, config_copy, sources_file)
        token = ctx.token_manager.token
        async with TestClient(TestServer(create_app(ctx))) as client:
            response = await client.get("/api/health")
            assert response.status == 200
            assert (await response.json())["status"] == "ok"

            denied = await client.get("/api/status")
            assert denied.status == 401

            headers = {"Authorization": f"Bearer {token}"}
            ok = await client.get("/api/status", headers=headers)
            assert ok.status == 200
            body = await ok.json()
            assert body["pools"]["candidate"]["total"] == 0
            assert body["chain"]["hops"] == 0

            sources = await client.get("/api/sources", headers=headers)
            assert sources.status == 200
            assert (await sources.json())["stats"]["total"] >= 25

            chain = await client.get("/api/chain", headers=headers)
            assert chain.status == 200

            vault = await client.get("/api/vault", headers=headers)
            assert (await vault.json())["state"] == "unavailable"

            patched = await client.patch(
                "/api/config", headers=headers,
                json={"section": "crawler", "option": "stealth_level", "value": 3},
            )
            assert patched.status == 200
            assert config_copy.config.crawler.stealth_level == 3

            forbidden = await client.patch(
                "/api/config", headers=headers,
                json={"section": "gateway", "option": "host", "value": "0.0.0.0"},
            )
            assert forbidden.status == 400

            rotated = await client.post("/api/chain/rotate", headers=headers)
            assert rotated.status == 409  # empty pool -> no chain

        for store in (ctx.candidate_pool.store, ctx.verified_pool.store):
            store.close()

    asyncio.run(scenario())


def test_gateway_config_switch(tmp_path, config_copy, sources_file):
    async def scenario():
        ctx = build_context(tmp_path, config_copy, sources_file)
        gateway = GatewayServer(config_copy, ctx)
        assert config_copy.config.gateway.enabled is False
        ok, message = await gateway.start()
        assert not ok and "disabled by config" in message
        assert not gateway.running

        config_copy.set_value("gateway", "enabled", True)
        ok, message = await gateway.start()
        assert ok, message
        assert gateway.running
        assert gateway.status()["api_port"] == config_copy.config.gateway.port
        await gateway.stop()
        assert not gateway.running

        for store in (ctx.candidate_pool.store, ctx.verified_pool.store):
            store.close()

    asyncio.run(scenario())


def test_token_ephemeral_without_vault(tmp_path):
    token_path = tmp_path / "token.json"
    manager = TokenManager(None, token_path, required=True)
    assert manager.verify(manager.token)
    assert not manager.verify("wrong-token")
    assert not token_path.exists()  # never written in plaintext
    assert manager.status()["ephemeral"] is True

    no_auth = TokenManager(None, token_path, required=False)
    assert no_auth.token is None and no_auth.verify(None)
