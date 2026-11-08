"""CLI entry point: numeric menus, action handlers and non-interactive commands."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Sequence

import aiohttp

from .. import __version__
from ..chain import CHAIN_MODES
from ..config.schema import ConfigError
from ..crypto.redaction import redact, redact_mapping
from ..fetcher import summarise
from ..parser.base import SourceSpec
from ..sources.discovery import SourceDiscoverer
from ..stealth.levels import choices, get_policy
from .app import AppContext
from .menus import MAIN, SUBMENUS, UI

log = logging.getLogger(__name__)

Handler = Callable[[AppContext, UI], Coroutine[Any, Any, None]]

COMPLIANCE = (
    "anonchains 仅用于合法授权的网络隐私、研究与测试：\n"
    "  - 只采集公开免费代理列表，遵守目标站点 robots.txt、服务条款与速率限制；\n"
    "  - 不提供攻击、未授权访问、凭证窃取或绕过付费/身份验证的能力；\n"
    "  - 免费公共代理不可信，绝不要传输真实凭据、机密或敏感个人数据；\n"
    "  - 仅限获得书面授权的红队 / 渗透测试等合法安全评估使用；\n"
    "  - 高匿 != 绝对匿名：节点运营者、时序关联、DNS/WebRTC 泄漏仍可能关联身份；\n"
    "  - 使用前必读 docs/16-legal-use-and-disclaimer.md（授权、免责、二次开发规则），\n"
    "    另见 docs/14-compliance.md 与 docs/08-security-and-vault.md。"
)


# --- small helpers -----------------------------------------------------

async def _ask(ui: UI, prompt: str) -> str:
    return await asyncio.to_thread(ui.ask, prompt)


async def _secret(ui: UI, prompt: str) -> str:
    return await asyncio.to_thread(ui.secret, prompt)


def _int(text: str, default: int | None = None) -> int | None:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return default


async def _crawl(ctx: AppContext, ui: UI, specs: list[SourceSpec] | None = None) -> None:
    targets = specs if specs is not None else ctx.registry.enabled_specs
    total = len(targets)
    state = {"done": 0, "parsed": 0, "ingested": 0, "ok": 0, "failed": 0, "blocked": 0}
    failures: list[tuple[str, str]] = []

    def on_result(result: Any) -> None:
        if result.nodes:
            state["ingested"] += ctx.candidate_pool.add(result.nodes)
        state["done"] += 1
        state["parsed"] += len(result.nodes)
        if result.ok:
            state["ok"] += 1
        elif result.blocked:
            state["blocked"] += 1
        else:
            state["failed"] += 1
            if len(failures) < 5:
                failures.append((result.spec.name, (result.error or "unknown")[:90]))
        ui.progress(
            state["done"], total,
            f"源 | 成功 {state['ok']} 失败 {state['failed']} 拦截 {state['blocked']} "
            f"| 解析 {state['parsed']} 条 | 入池 {state['ingested']} 条",
        )

    ui.print(f"开始抓取（共 {total} 个源，Ctrl+C 可中断）...")
    started = time.monotonic()
    try:
        results = await ctx.crawler.run(specs, on_result=on_result)
    finally:
        ui.progress_end()
    summary = summarise(results)
    ui.print(
        f"完成：源 {summary['ok']}/{summary['sources']} 成功 | 解析 {summary['nodes']} 条 "
        f"| 入池 {state['ingested']} 条 | 未变更 {summary['unchanged']} "
        f"| 耗时 {time.monotonic() - started:.1f}s"
    )
    if failures:
        ui.print(f"失败源 {state['failed']} 个（前 {len(failures)} 条）：")
        for name, reason in failures:
            ui.print(f"  - {name}: {redact(reason)}")
    ui.table(
        "抓取汇总",
        ("源", "状态", "节点数", "耗时(ms)", "说明"),
        [
            (r.spec.name, "OK" if r.ok else ("BLOCKED" if r.blocked else "FAIL"),
             len(r.nodes), r.elapsed_ms, redact(r.error or "-"))
            for r in results
        ],
    )
    ui.print(f"合计入候选池：{state['ingested']} 个节点")


# --- source actions ----------------------------------------------------

async def a_crawl_all(ctx: AppContext, ui: UI) -> None:
    await _crawl(ctx, ui)


async def a_crawl_one(ctx: AppContext, ui: UI) -> None:
    specs = ctx.registry.enabled_specs
    ui.table("启用源", ("序号", "名称", "类型", "URL"),
             [(i + 1, s.name, s.kind, s.url) for i, s in enumerate(specs)])
    choice = _int(await _ask(ui, "输入序号"))
    if choice is None or not 1 <= choice <= len(specs):
        ui.print("无效序号")
        return
    await _crawl(ctx, ui, [specs[choice - 1]])


async def a_sources_list(ctx: AppContext, ui: UI) -> None:
    ui.table("源清单", ("名称", "启用", "类型", "协议", "权重", "URL"),
             [(s.name, "Y" if s.enabled else "N", s.kind, s.protocol_hint or "-", s.weight, s.url)
              for s in ctx.registry.all_specs])
    ui.json(ctx.registry.stats())


async def a_sources_toggle(ctx: AppContext, ui: UI) -> None:
    name = await _ask(ui, "输入源名称")
    spec = ctx.registry.by_name(name)
    if spec is None:
        ui.print(f"未找到源：{name}")
        return
    updated = ctx.registry.toggle(name, not spec.enabled)
    ui.print(f"{updated.name} -> {'启用' if updated.enabled else '禁用'}（仅本次运行生效）")


async def a_sources_discover(ctx: AppContext, ui: UI) -> None:
    ui.print("发现功能会通过 GitHub API 发起少量请求（未认证限 60 次/小时）。")
    if (await _ask(ui, "继续？[y/N]")).lower() not in {"y", "yes"}:
        return

    async def fetch(url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url) as response:
                    return await response.read() if response.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None

    discoverer = SourceDiscoverer(ctx.registry.discovery, fetch)
    candidates = await discoverer.discover()
    ui.table("候选新源（不会自动启用）", ("名称", "类型", "URL"),
             [(c.name, c.kind, c.url) for c in candidates])
    if not candidates:
        ui.print("未发现新源，或 GitHub API 不可用。")


# --- pool actions ------------------------------------------------------

async def a_pools_candidate(ctx: AppContext, ui: UI) -> None:
    nodes = ctx.candidate_pool.sample(30)
    ui.table("候选池样本", ("协议", "地址", "匿名度", "分数", "来源"),
             [(n.protocol.value, n.summary().split("://")[-1], n.anonymity.value,
               round(n.score, 1), n.source or "-") for n in nodes])
    ui.json(ctx.candidate_pool.stats())


async def a_pools_verified(ctx: AppContext, ui: UI) -> None:
    nodes = ctx.verified_pool.best(30)
    ui.table("可用池 Top", ("协议", "地址", "延迟(ms)", "匿名度", "分数", "成功/失败"),
             [(n.protocol.value, n.summary().split("://")[-1], n.latency_ms or "-",
               n.anonymity.value, round(n.score, 1), f"{n.success_count}/{n.fail_count}")
              for n in nodes])
    ui.json(ctx.verified_pool.stats())


async def a_pools_stats(ctx: AppContext, ui: UI) -> None:
    ui.json({"candidate": ctx.candidate_pool.stats(), "verified": ctx.verified_pool.stats()})


async def a_pools_clear(ctx: AppContext, ui: UI) -> None:
    if (await _ask(ui, "确认清空两个池？输入 yes")).lower() != "yes":
        ui.print("已取消")
        return
    ctx.candidate_store.clear()
    ctx.verified_store.clear()
    ui.print("两个池已清空")


# --- validation actions ------------------------------------------------

async def _report_cycle(ctx: AppContext, ui: UI, *, skip_crawl: bool) -> None:
    state = {"passed": 0, "skipped": 0, "failed": 0}

    def on_progress(done: int, total: int, result: Any) -> None:
        if result.ok:
            state["passed"] += 1
        else:
            state["failed"] += 1
        if result.skipped:
            state["skipped"] += 1
        ui.progress(
            done, total,
            f"验证 | 通过 {state['passed']} 跳过 {state['skipped']} 失败 {state['failed']}",
        )

    try:
        report = await ctx.scheduler.run_once(skip_crawl=skip_crawl, on_progress=on_progress)
    except Exception as exc:
        ui.progress_end()
        ui.print(f"执行失败：{type(exc).__name__}: {exc}")
        return
    ui.progress_end()
    ui.json(report.as_dict())


async def a_validate_candidate(ctx: AppContext, ui: UI) -> None:
    due = ctx.candidate_pool.take_stale(ctx.config.validator.concurrency * 4,
                                        ctx.config.validator.ttl_seconds)
    ui.print(f"待检节点：{len(due)}")
    await _report_cycle(ctx, ui, skip_crawl=True)


async def a_cycle(ctx: AppContext, ui: UI) -> None:
    await _report_cycle(ctx, ui, skip_crawl=False)


async def a_validate_only(ctx: AppContext, ui: UI) -> None:
    await _report_cycle(ctx, ui, skip_crawl=True)


# --- chain actions -----------------------------------------------------

async def a_chain_show(ctx: AppContext, ui: UI) -> None:
    chain = ctx.rotator.current
    if chain is None:
        ui.print("当前没有活动链，请先组装。")
        return
    ui.table("当前链", ("跳", "协议", "地址", "分数", "是否需外部核心"),
             [(i + 1, n.protocol.value, n.summary().split("://")[-1], round(n.score, 1),
               "否" if n.is_native else "是") for i, n in enumerate(chain.nodes)])
    ui.json({"hops": chain.hops, "mode": chain.policy.mode,
             "requires_external_core": chain.requires_external_core})


async def a_chain_build(ctx: AppContext, ui: UI) -> None:
    from ..chain import ChainBuildError

    try:
        chain = ctx.rotator.rotate()
        ui.print(f"已组装 {chain.hops} 跳链：")
        for i, summary in enumerate(chain.summaries()):
            ui.print(f"  {i + 1}. {summary}")
    except ChainBuildError as exc:
        ui.print(f"无法组链：{exc}（请先抓取并校验节点）")


async def a_chain_hops(ctx: AppContext, ui: UI) -> None:
    value = _int(await _ask(ui, f"输入跳数（当前 {ctx.config.chain.hops}，无上限）"),
                 ctx.config.chain.hops)
    if value is None or value < 1:
        ui.print("跳数必须 >= 1")
        return
    try:
        ctx.set_config_value("chain", "hops", value)
        ui.print(f"跳数已设为 {value} 并写入配置")
    except ConfigError as exc:
        ui.print(f"配置被拒绝：{exc}")


async def a_chain_mode(ctx: AppContext, ui: UI) -> None:
    ui.table("链模式", ("序号", "模式"), [(i + 1, m) for i, m in enumerate(CHAIN_MODES)])
    choice = _int(await _ask(ui, "输入序号"))
    if choice is None or not 1 <= choice <= len(CHAIN_MODES):
        ui.print("无效序号")
        return
    mode = CHAIN_MODES[choice - 1]
    ctx.set_config_value("chain", "mode", mode)
    ui.print(f"链模式已设为 {mode}")


async def a_chain_entry_start(ctx: AppContext, ui: UI) -> None:
    if ctx.rotator.current is None:
        ui.print("请先组装链")
        return
    port = await ctx.entry_server.start()
    ui.print(f"本地混合入口已启动：socks5://127.0.0.1:{port} （同时支持 HTTP CONNECT）")


async def a_chain_entry_stop(ctx: AppContext, ui: UI) -> None:
    await ctx.entry_server.stop()
    ui.print("本地入口已停止")


async def a_chain_core_start(ctx: AppContext, ui: UI) -> None:
    chain = ctx.rotator.current
    if chain is None:
        ui.print("请先组装链")
        return
    ok, message = await ctx.core_manager.start(chain)
    ui.print(message)
    if not ok:
        ui.print(ctx.core_manager.install_hint())


async def a_chain_core_stop(ctx: AppContext, ui: UI) -> None:
    await ctx.core_manager.stop()
    ui.print("外部核心已停止")


async def a_chain_core_export(ctx: AppContext, ui: UI) -> None:
    chain = ctx.rotator.current
    if chain is None:
        ui.print("请先组装链")
        return
    ui.print("注意：导出内容包含节点凭证，请勿外传。")
    ui.json(ctx.core_manager.dry_run(chain))


# --- gateway actions ---------------------------------------------------

async def a_gateway_status(ctx: AppContext, ui: UI) -> None:
    ui.json(ctx.gateway.status())


async def a_gateway_start(ctx: AppContext, ui: UI) -> None:
    ok, message = await ctx.gateway.start(force=True)
    ui.print(message)
    if ok and ctx.token_manager.token:
        ui.print(f"API token: {ctx.token_manager.token}")


async def a_gateway_stop(ctx: AppContext, ui: UI) -> None:
    await ctx.gateway.stop()
    ui.print("网关已停止")


async def a_gateway_toggle(ctx: AppContext, ui: UI) -> None:
    current = ctx.config.gateway.enabled
    answer = (await _ask(ui, "输入 true 或 false")).strip().lower()
    if answer not in {"true", "false", "1", "0"}:
        ui.print("无效输入")
        return
    enabled = answer in {"true", "1"}
    if enabled != current:
        ctx.set_config_value("gateway", "enabled", enabled)
    ok, message = await ctx.gateway.apply_config_switch()
    ui.print(f"gateway.enabled = {str(enabled).lower()}；{message}（running={ok}）")


async def a_gateway_token(ctx: AppContext, ui: UI) -> None:
    token = ctx.token_manager.token
    ui.print(f"当前 token: {token or '(未启用鉴权)'}")
    ui.print(f"状态: {ctx.token_manager.status()}")
    if token and (await _ask(ui, "轮换 token？[y/N]")).lower() in {"y", "yes"}:
        ui.print(f"新 token: {ctx.token_manager.rotate()}")


# --- stealth -----------------------------------------------------------

def _stealth_handler(level: int) -> Handler:
    async def handler(ctx: AppContext, ui: UI) -> None:
        ctx.set_config_value("crawler", "stealth_level", level)
        policy = get_policy(level)
        ui.print(f"隐匿等级已设为 {level} [{policy.name}] 并写入配置")
        ui.print(f"并发上限={policy.concurrency_cap}, 抖动={policy.min_delay_ms}~{policy.max_delay_ms}ms, "
                 f"TLS指纹={'开(需 curl_cffi)' if policy.tls_impersonation else '关'}")
    return handler


async def a_stealth_show(ctx: AppContext, ui: UI) -> None:
    current = ctx.config.crawler.stealth_level
    ui.table("隐匿等级", ("序号", "名称", "当前", "并发上限", "抖动(ms)", "说明"),
             [(level, name, "<==" if level == current else "",
               get_policy(level).concurrency_cap,
               f"{get_policy(level).min_delay_ms}~{get_policy(level).max_delay_ms}", description)
              for level, name, description in choices()])


# --- vault -------------------------------------------------------------

async def a_vault_status(ctx: AppContext, ui: UI) -> None:
    ui.json(ctx.vault.status())


async def a_vault_init(ctx: AppContext, ui: UI) -> None:
    from ..crypto.vault import VaultError, VaultState

    if ctx.vault.state is not VaultState.UNINITIALISED:
        ui.print(f"Vault 状态为 {ctx.vault.state.value}，无需初始化（可用修改口令）")
        return
    first = await _secret(ui, "设置主口令（至少 8 位）")
    second = await _secret(ui, "再次确认主口令")
    if first != second or len(first) < 8:
        ui.print("两次输入不一致或长度不足")
        return
    try:
        ctx.vault.initialize(first)
        ctx.token_manager.refresh()
        ui.print("Vault 已创建并解锁；池内敏感字段现在会加密落盘")
    except (VaultError, ValueError) as exc:
        ui.print(f"失败：{exc}")


async def a_vault_unlock(ctx: AppContext, ui: UI) -> None:
    from ..crypto.vault import VaultError

    phrase = await _secret(ui, "输入主口令")
    try:
        ctx.vault.unlock(phrase)
        ctx.token_manager.refresh()
        ui.print(f"已解锁：{ctx.vault.state.value}")
    except VaultError as exc:
        ui.print(f"解锁失败：{type(exc).__name__}")


async def a_vault_lock(ctx: AppContext, ui: UI) -> None:
    ctx.vault.lock()
    ui.print("已锁定；敏感字段将不再写入磁盘")


async def a_vault_rotate(ctx: AppContext, ui: UI) -> None:
    from ..crypto.vault import VaultError

    phrase = await _secret(ui, "输入新主口令")
    try:
        ctx.vault.rotate_key(phrase)
        ui.print("主口令已轮换（数据密钥不变，既有密文仍可解密）")
    except VaultError as exc:
        ui.print(f"失败：{type(exc).__name__}: {exc}")


# --- system ------------------------------------------------------------

async def a_system_status(ctx: AppContext, ui: UI) -> None:
    ui.json(ctx.status())


async def a_config_show(ctx: AppContext, ui: UI) -> None:
    ui.json(redact_mapping(ctx.config.as_dict()))


async def a_config_set(ctx: AppContext, ui: UI) -> None:
    raw = await _ask(ui, "输入 section.option=value，例如 chain.hops=7")
    if "=" not in raw or "." not in raw.split("=", 1)[0]:
        ui.print("格式错误")
        return
    left, value = raw.split("=", 1)
    section, option = left.strip().split(".", 1)
    try:
        ctx.set_config_value(section.strip(), option.strip(), value.strip())
        ui.print("已写入配置并热更新")
    except (ConfigError, KeyError, ValueError) as exc:
        ui.print(f"配置被拒绝：{exc}")


async def a_config_reload(ctx: AppContext, ui: UI) -> None:
    cfg, changed = ctx.config_manager.reload()
    ctx.apply_config()
    ui.print(f"重载完成（changed={changed}），当前 hops={cfg.chain.hops}")


async def a_compliance(ctx: AppContext, ui: UI) -> None:
    ui.print(COMPLIANCE)


# --- scheduler ---------------------------------------------------------

async def a_scheduler_start(ctx: AppContext, ui: UI) -> None:
    await ctx.scheduler.start()
    ui.print("后台周期任务已启动（抓取 / 复检 / 轮换）")


async def a_scheduler_stop(ctx: AppContext, ui: UI) -> None:
    await ctx.scheduler.stop()
    ui.print("后台周期任务已停止")


async def a_scheduler_once(ctx: AppContext, ui: UI) -> None:
    await _report_cycle(ctx, ui, skip_crawl=False)


async def a_scheduler_status(ctx: AppContext, ui: UI) -> None:
    report = ctx.scheduler.last_report
    ui.json(report.as_dict() if report else {"status": "idle"})


ACTIONS: dict[str, Handler] = {
    "crawl-all": a_crawl_all,
    "crawl-one": a_crawl_one,
    "sources-list": a_sources_list,
    "sources-toggle": a_sources_toggle,
    "sources-discover": a_sources_discover,
    "pools-candidate": a_pools_candidate,
    "pools-verified": a_pools_verified,
    "pools-stats": a_pools_stats,
    "pools-clear": a_pools_clear,
    "validate-candidate": a_validate_candidate,
    "cycle": a_cycle,
    "validate-only": a_validate_only,
    "chain-show": a_chain_show,
    "chain-build": a_chain_build,
    "chain-hops": a_chain_hops,
    "chain-mode": a_chain_mode,
    "chain-entry-start": a_chain_entry_start,
    "chain-entry-stop": a_chain_entry_stop,
    "chain-core-start": a_chain_core_start,
    "chain-core-stop": a_chain_core_stop,
    "chain-core-export": a_chain_core_export,
    "gateway-status": a_gateway_status,
    "gateway-start": a_gateway_start,
    "gateway-stop": a_gateway_stop,
    "gateway-toggle": a_gateway_toggle,
    "gateway-token": a_gateway_token,
    "stealth-show": a_stealth_show,
    "vault-status": a_vault_status,
    "vault-init": a_vault_init,
    "vault-unlock": a_vault_unlock,
    "vault-lock": a_vault_lock,
    "vault-rotate": a_vault_rotate,
    "system-status": a_system_status,
    "config-show": a_config_show,
    "config-set": a_config_set,
    "config-reload": a_config_reload,
    "compliance": a_compliance,
    "scheduler-start": a_scheduler_start,
    "scheduler-stop": a_scheduler_stop,
    "scheduler-once": a_scheduler_once,
    "scheduler-status": a_scheduler_status,
    **{f"stealth-{level}": _stealth_handler(level) for level, _, _ in choices()},
}


# --- menu loop ---------------------------------------------------------

async def run_menu(ctx: AppContext, ui: UI, menu: Any = MAIN) -> None:
    """Nested numeric navigation. ``0`` goes back one level (or exits at MAIN)."""
    stack: list[Any] = [menu]
    while stack:
        current = stack[-1]
        ui.menu(current)
        choice = await _ask(ui, "请输入数字")
        item = current.find(choice)
        if item is None:
            ui.print("无效选择，请重新输入")
            continue
        if item.key == "0":
            stack.pop()
            continue
        submenu = SUBMENUS.get(item.action)
        if submenu is not None:
            stack.append(submenu)
            continue
        if item.action == "exit":
            return
        handler = ACTIONS.get(item.action)
        if handler is None:
            ui.print(f"未实现的操作：{item.action}")
            continue
        try:
            await handler(ctx, ui)
        except KeyboardInterrupt:
            ui.print("操作已中断")
        except Exception as exc:
            log.exception("menu action %s failed", item.action)
            ui.print(f"操作失败：{type(exc).__name__}: {exc}")
        if not ui.plain:
            await _ask(ui, "按回车继续")


# --- non-interactive ---------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anonchains", description="模块化高匿多跳代理链编排工具"
    )
    parser.add_argument("--config", type=Path, default=None, help="主配置文件路径")
    parser.add_argument("--base-dir", type=Path, default=None, help="运行时数据根目录")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="打印综合状态（离线）")
    sub.add_parser("version", help="打印版本")
    sub.add_parser("sources", help="列出代理源（离线）")
    sub.add_parser("config-show", help="打印配置（脱敏，离线）")
    sub.add_parser("vault", help="打印 Vault 状态（离线）").add_argument(
        "state", nargs="?", default="status", choices=["status"]
    )

    config_set = sub.add_parser("config-set", help="修改配置项")
    config_set.add_argument("section")
    config_set.add_argument("option")
    config_set.add_argument("value")

    stealth = sub.add_parser("stealth", help="设置隐匿等级")
    stealth.add_argument("level", type=int, choices=[1, 2, 3, 4])

    gateway = sub.add_parser("gateway", help="网关开关")
    gateway.add_argument("state", choices=["on", "off", "status"])

    crawl = sub.add_parser("crawl", help="抓取代理源（需要网络）")
    crawl.add_argument("--source", default=None, help="只抓取指定源名称")

    sub.add_parser("validate", help="复检并升降级（需要网络）")
    sub.add_parser("cycle", help="完整闭环（需要网络）")
    chain = sub.add_parser("chain", help="显示或组装链")
    chain.add_argument("--hops", type=int, default=None, help="先设置跳数")
    return parser


async def _run_noninteractive(args: argparse.Namespace, ui: UI) -> int:
    ctx = AppContext(args.config, base_dir=args.base_dir)
    try:
        command = args.command
        if command == "version":
            ui.print(f"anonchains {__version__}")
        elif command == "status":
            ui.json(ctx.status())
        elif command == "sources":
            ui.table("代理源", ("名称", "启用", "类型", "URL"),
                     [(s.name, "Y" if s.enabled else "N", s.kind, s.url)
                      for s in ctx.registry.all_specs])
        elif command == "config-show":
            ui.json(redact_mapping(ctx.config.as_dict()))
        elif command == "config-set":
            ctx.set_config_value(args.section, args.option, args.value)
            ui.print("已写入配置")
        elif command == "stealth":
            await _stealth_handler(args.level)(ctx, ui)
        elif command == "gateway":
            if args.state == "status":
                ui.json(ctx.gateway.status())
            else:
                enabled = args.state == "on"
                ctx.set_config_value("gateway", "enabled", enabled)
                ok, message = await ctx.gateway.apply_config_switch()
                ui.print(f"gateway.enabled={enabled}; running={ok}; {message}")
        elif command == "vault":
            ui.json(ctx.vault.status())
        elif command == "crawl":
            specs = None
            if args.source:
                spec = ctx.registry.by_name(args.source)
                if spec is None:
                    ui.print(f"未知源：{args.source}")
                    return 2
                specs = [spec]
            await _crawl(ctx, ui, specs)
        elif command == "validate":
            await _report_cycle(ctx, ui, skip_crawl=True)
        elif command == "cycle":
            await _report_cycle(ctx, ui, skip_crawl=False)
        elif command == "chain":
            if args.hops:
                ctx.set_config_value("chain", "hops", args.hops)
            await a_chain_show(ctx, ui)
        return 0
    finally:
        await ctx.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"anonchains {__version__}", flush=True)
        return 0

    async def runner() -> int:
        ui = UI()
        if args.command is None:
            ctx = AppContext(args.config, base_dir=args.base_dir)
            try:
                await ctx.startup()
                ui.print(f"anonchains {__version__} | 合规提示：主菜单 8 -> 5")
                await run_menu(ctx, ui)
                return 0
            finally:
                await ctx.shutdown()
        return await _run_noninteractive(args, ui)

    try:
        return asyncio.run(runner())
    except (ConfigError, FileNotFoundError) as exc:
        print(f"配置错误：{exc}", flush=True)
        return 2
    except KeyboardInterrupt:
        print("\n已退出", flush=True)
        return 130
