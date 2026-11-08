"""REST management API (loopback only).

All routes live under ``/api/`` and require a bearer token unless
``gateway.token_required = false``. Long-running operations (crawl, validate,
core start) are dispatched as background tasks and return HTTP 202.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Coroutine

from aiohttp import web

from ..core_adapter.process import CoreManager
from ..crypto.redaction import redact, redact_mapping
from ..scheduler.jobs import Scheduler
from ..validator.batch import Validator

log = logging.getLogger(__name__)

JSON_KEY: web.RequestKey[dict[str, Any]] = web.RequestKey("anonchains_json", dict)
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

#: Options the API is allowed to change. Everything else must be edited in the
#: config file, to avoid turning a local API into a remote config backdoor.
WRITABLE_OPTIONS = frozenset(
    {
        ("chain", "hops"),
        ("chain", "mode"),
        ("chain", "rotation_seconds"),
        ("crawler", "stealth_level"),
        ("crawler", "rate_limit_per_second"),
        ("validator", "interval_seconds"),
        ("validator", "ttl_seconds"),
        ("gateway", "enabled"),
        ("gateway", "token_required"),
    }
)


@dataclass(slots=True)
class GatewayContext:
    """Everything the REST handlers need. Injected by the CLI application."""

    config_manager: Any
    registry: Any
    candidate_pool: Any
    verified_pool: Any
    rotator: Any
    crawler: Any
    validator: Validator
    scheduler: Scheduler
    core_manager: CoreManager
    token_manager: Any
    entry_server: Any = None
    vault: Any = None
    background: set[asyncio.Task[Any]] = field(default_factory=set)

    def spawn(self, coroutine: Coroutine[Any, Any, Any], label: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=f"anonchains-{label}")
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        return task

    def config(self) -> Any:
        return self.config_manager.config


CTX_KEY: web.AppKey[GatewayContext] = web.AppKey("anonchains_ctx", GatewayContext)


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _payload(request: web.Request) -> dict[str, Any]:
    return request.get(JSON_KEY, {}) or {}


@web.middleware
async def auth_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    ctx = request.app[CTX_KEY]
    path = request.path
    if path in {"/", "/api/health"} or not path.startswith("/api/"):
        return await handler(request)
    token = request.headers.get("Authorization") or request.headers.get("X-Anonchains-Token")
    if not ctx.token_manager.verify(token):
        return _json({"error": "unauthorized", "hint": "use Authorization: Bearer <token>"}, 401)
    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        if request.can_read_body and request.method in {"POST", "PATCH", "PUT"}:
            try:
                request[JSON_KEY] = await request.json()
            except (ValueError, TypeError):
                request[JSON_KEY] = {}
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        log.warning("api %s %s failed: %s", request.method, request.path, type(exc).__name__)
        return _json(
            {"error": type(exc).__name__, "detail": redact(str(exc))[:300]},
            500,
        )


# --- handlers ---------------------------------------------------------

async def health(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json({"status": "ok", "service": "anonchains", "api": True,
                  "gateway_enabled": ctx.config().gateway.enabled})


async def status(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    cfg = ctx.config()
    return _json(
        {
            "config": {
                "hops": cfg.chain.hops,
                "mode": cfg.chain.mode,
                "stealth_level": cfg.crawler.stealth_level,
                "gateway_enabled": cfg.gateway.enabled,
            },
            "pools": {
                "candidate": ctx.candidate_pool.stats(),
                "verified": ctx.verified_pool.stats(),
            },
            "chain": ctx.rotator.status(),
            "gateway": {
                "api_token": ctx.token_manager.status(),
                "entry": ctx.entry_server.status() if ctx.entry_server else None,
            },
            "core": ctx.core_manager.status(),
            "scheduler": ctx.scheduler.last_report.as_dict() if ctx.scheduler.last_report else None,
            "sources": ctx.registry.stats(),
        }
    )


async def config_get(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json(redact_mapping(ctx.config().as_dict()))


async def config_patch(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    body = _payload(request)
    section, option = str(body.get("section", "")), str(body.get("option", ""))
    value = body.get("value")
    if (section, option) not in WRITABLE_OPTIONS or value is None:
        return _json(
            {"error": "option not writable", "allowed": [list(k) for k in sorted(WRITABLE_OPTIONS)]},
            400,
        )
    cfg = ctx.config_manager.set_value(section, option, value)
    return _json({"ok": True, "section": section, "option": option,
                  "value": getattr(getattr(cfg, section), option)})


async def pool_candidate(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    limit = min(200, max(1, int(request.query.get("limit", "20"))))
    return _json({"stats": ctx.candidate_pool.stats(),
                  "sample": [n.to_dict() for n in ctx.candidate_pool.sample(limit)]})


async def pool_verified(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    limit = min(200, max(1, int(request.query.get("limit", "20"))))
    return _json({"stats": ctx.verified_pool.stats(),
                  "sample": [n.to_dict() for n in ctx.verified_pool.best(limit)]})


async def chain_get(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    chain = ctx.rotator.current
    return _json(chain.as_dict() if chain else {"hops": 0, "nodes": []})


async def chain_rotate(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    try:
        chain = ctx.rotator.rotate()
        return _json({"ok": True, "chain": chain.as_dict()})
    except Exception as exc:
        return _json({"ok": False, "error": redact(str(exc))[:200]}, 409)


async def crawl(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]

    async def job() -> None:
        def on_result(result: Any) -> None:
            if result.nodes:
                ctx.candidate_pool.add(result.nodes)

        await ctx.crawler.run(on_result=on_result)

    ctx.spawn(job(), "api-crawl")
    return _json({"accepted": True, "operation": "crawl"}, 202)


async def validate(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]

    async def job() -> None:
        report = await ctx.scheduler.run_once(skip_crawl=True)
        log.info("api validation cycle: %s", report.validation)

    ctx.spawn(job(), "api-validate")
    return _json({"accepted": True, "operation": "validate"}, 202)


async def cycle(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    ctx.spawn(ctx.scheduler.run_once(), "api-cycle")
    return _json({"accepted": True, "operation": "cycle"}, 202)


async def sources(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json({"stats": ctx.registry.stats(),
                  "sources": [s.to_dict() for s in ctx.registry.all_specs]})


async def source_toggle(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    name = request.match_info["name"]
    enabled = bool(_payload(request).get("enabled", True))
    try:
        spec = ctx.registry.toggle(name, enabled)
        return _json({"ok": True, "source": spec.to_dict()})
    except KeyError:
        return _json({"error": "unknown source"}, 404)


async def vault_status(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json(ctx.vault.status() if ctx.vault else {"state": "unavailable"})


async def vault_unlock(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    passphrase = str(_payload(request).get("passphrase", ""))
    if not passphrase:
        return _json({"error": "passphrase required"}, 400)
    try:
        ctx.vault.unlock(passphrase)
        return _json({"ok": True, "state": ctx.vault.state.value})
    except Exception as exc:
        return _json({"ok": False, "error": type(exc).__name__}, 400)


async def vault_lock(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    if ctx.vault:
        ctx.vault.lock()
    return _json({"ok": True})


async def core_status(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json(ctx.core_manager.status())


async def core_start(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    chain = ctx.rotator.current
    if chain is None:
        return _json({"error": "no active chain"}, 409)
    ok, message = await ctx.core_manager.start(chain)
    return _json({"ok": ok, "message": message}, 200 if ok else 503)


async def core_stop(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    await ctx.core_manager.stop()
    return _json({"ok": True})


async def entry_status(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json(ctx.entry_server.status() if ctx.entry_server else {"running": False})


async def token_status(request: web.Request) -> web.Response:
    ctx = request.app[CTX_KEY]
    return _json(ctx.token_manager.status())


def create_app(ctx: GatewayContext) -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    app[CTX_KEY] = ctx
    app.router.add_get("/", health)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/config", config_get)
    app.router.add_patch("/api/config", config_patch)
    app.router.add_get("/api/pools/candidate", pool_candidate)
    app.router.add_get("/api/pools/verified", pool_verified)
    app.router.add_get("/api/chain", chain_get)
    app.router.add_post("/api/chain/rotate", chain_rotate)
    app.router.add_post("/api/crawl", crawl)
    app.router.add_post("/api/validate", validate)
    app.router.add_post("/api/cycle", cycle)
    app.router.add_get("/api/sources", sources)
    app.router.add_post("/api/sources/{name}/toggle", source_toggle)
    app.router.add_get("/api/vault", vault_status)
    app.router.add_post("/api/vault/unlock", vault_unlock)
    app.router.add_post("/api/vault/lock", vault_lock)
    app.router.add_get("/api/core", core_status)
    app.router.add_post("/api/core/start", core_start)
    app.router.add_post("/api/core/stop", core_stop)
    app.router.add_get("/api/entry", entry_status)
    app.router.add_get("/api/token", token_status)
    return app
