"""Numeric main menu / submenu definitions and terminal rendering."""

from __future__ import annotations

import getpass
import importlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Sequence

from ..stealth.levels import choices

# Dynamic import keeps rich optional at runtime and avoids "possibly unbound"
# names in the except branch. When rich is missing the UI renders plain text.
_console_mod: Any = None
_table_mod: Any = None
try:
    _console_mod = importlib.import_module("rich.console")
    _table_mod = importlib.import_module("rich.table")
except ImportError:  # pragma: no cover - dependency is in requirements.txt
    _console_mod = None
    _table_mod = None
_has_rich: bool = _console_mod is not None and _table_mod is not None


@dataclass(frozen=True, slots=True)
class MenuItem:
    key: str
    label: str
    action: str


@dataclass(frozen=True, slots=True)
class Menu:
    key: str
    title: str
    items: tuple[MenuItem, ...]

    def find(self, choice: str) -> MenuItem | None:
        choice = choice.strip()
        return next((item for item in self.items if item.key == choice), None)


MAIN = Menu(
    key="main",
    title="anonchains 主菜单",
    items=(
        MenuItem("1", "代理源与爬取", "sources"),
        MenuItem("2", "双池管理（候选池 / 可用池）", "pools"),
        MenuItem("3", "校验与筛选", "validate"),
        MenuItem("4", "链路与转发", "chain"),
        MenuItem("5", "本地网关与 API", "gateway"),
        MenuItem("6", "隐匿等级（数字选择）", "stealth"),
        MenuItem("7", "加密 Vault", "vault"),
        MenuItem("8", "系统配置与状态", "system"),
        MenuItem("9", "后台调度器", "scheduler"),
        MenuItem("0", "退出", "exit"),
    ),
)

_SOURCES = Menu(
    "sources", "代理源与爬取",
    (
        MenuItem("1", "抓取全部启用源", "crawl-all"),
        MenuItem("2", "抓取单个源", "crawl-one"),
        MenuItem("3", "查看源清单", "sources-list"),
        MenuItem("4", "启用 / 禁用源", "sources-toggle"),
        MenuItem("5", "发现更多公开源（需联网 GitHub API）", "sources-discover"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_POOLS = Menu(
    "pools", "双池管理",
    (
        MenuItem("1", "查看候选池", "pools-candidate"),
        MenuItem("2", "查看可用池", "pools-verified"),
        MenuItem("3", "池统计", "pools-stats"),
        MenuItem("4", "清空两个池（危险）", "pools-clear"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_VALIDATE = Menu(
    "validate", "校验与筛选",
    (
        MenuItem("1", "校验候选池待检节点", "validate-candidate"),
        MenuItem("2", "立即跑完整闭环（抓取+校验+升降级）", "cycle"),
        MenuItem("3", "仅复检（跳过抓取）", "validate-only"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_CHAIN = Menu(
    "chain", "链路与转发",
    (
        MenuItem("1", "查看当前链", "chain-show"),
        MenuItem("2", "组装 / 重组链", "chain-build"),
        MenuItem("3", "设置跳数（无上限）", "chain-hops"),
        MenuItem("4", "切换链模式", "chain-mode"),
        MenuItem("5", "启动本地代理入口", "chain-entry-start"),
        MenuItem("6", "停止本地代理入口", "chain-entry-stop"),
        MenuItem("7", "启动外部核心（特殊协议）", "chain-core-start"),
        MenuItem("8", "停止外部核心", "chain-core-stop"),
        MenuItem("9", "导出核心配置（dry-run）", "chain-core-export"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_GATEWAY = Menu(
    "gateway", "本地网关与 API",
    (
        MenuItem("1", "查看网关状态", "gateway-status"),
        MenuItem("2", "启动网关（含本地入口）", "gateway-start"),
        MenuItem("3", "停止网关", "gateway-stop"),
        MenuItem("4", "配置开关 true / false", "gateway-toggle"),
        MenuItem("5", "显示 / 轮换 API token", "gateway-token"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_STEALTH = Menu(
    "stealth", "隐匿等级",
    (
        MenuItem("1", "等级 1 - " + choices()[0][2], "stealth-1"),
        MenuItem("2", "等级 2 - " + choices()[1][2], "stealth-2"),
        MenuItem("3", "等级 3 - " + choices()[2][2], "stealth-3"),
        MenuItem("4", "等级 4 - " + choices()[3][2], "stealth-4"),
        MenuItem("5", "查看当前等级详情", "stealth-show"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_VAULT = Menu(
    "vault", "加密 Vault",
    (
        MenuItem("1", "查看状态", "vault-status"),
        MenuItem("2", "初始化（设置主口令）", "vault-init"),
        MenuItem("3", "解锁", "vault-unlock"),
        MenuItem("4", "锁定", "vault-lock"),
        MenuItem("5", "修改主口令", "vault-rotate"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_SYSTEM = Menu(
    "system", "系统配置与状态",
    (
        MenuItem("1", "综合状态", "system-status"),
        MenuItem("2", "查看配置", "config-show"),
        MenuItem("3", "修改配置项", "config-set"),
        MenuItem("4", "手动热重载配置", "config-reload"),
        MenuItem("5", "合规与使用边界提示", "compliance"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)
_SCHEDULER = Menu(
    "scheduler", "后台调度器",
    (
        MenuItem("1", "启动周期任务", "scheduler-start"),
        MenuItem("2", "停止周期任务", "scheduler-stop"),
        MenuItem("3", "立即执行一轮", "scheduler-once"),
        MenuItem("4", "查看上轮报告", "scheduler-status"),
        MenuItem("0", "返回主菜单", "back"),
    ),
)

SUBMENUS: dict[str, Menu] = {
    m.key: m
    for m in (_SOURCES, _POOLS, _VALIDATE, _CHAIN, _GATEWAY, _STEALTH, _VAULT, _SYSTEM, _SCHEDULER)
}


class UI:
    """Small rendering facade: rich when attached to a TTY, plain text otherwise."""

    def __init__(self, *, force_plain: bool = False) -> None:
        self.plain = force_plain or not _has_rich or not sys.stdout.isatty()
        self.console = _console_mod.Console() if _has_rich and not self.plain else None

    def print(self, message: Any = "") -> None:
        if self.console is not None:
            self.console.print(message)
        else:
            print(message, flush=True)

    def menu(self, menu: Menu) -> None:
        if self.console is None:
            self.print(f"\n== {menu.title} ==")
            for item in menu.items:
                self.print(f"  {item.key}. {item.label}")
            return
        table = _table_mod.Table(title=menu.title, show_header=True, header_style="bold cyan")
        table.add_column("序号", width=6)
        table.add_column("功能")
        for item in menu.items:
            table.add_row(item.key, item.label)
        self.console.print(table)

    def table(self, title: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            self.print(f"{title}: (空)")
            return
        if self.console is None:
            self.print(f"\n== {title} ==")
            self.print(" | ".join(columns))
            for row in rows:
                self.print(" | ".join(str(cell) for cell in row))
            return
        table = _table_mod.Table(title=title, show_header=True, header_style="bold cyan")
        for column in columns:
            table.add_column(column)
        for row in rows:
            table.add_row(*[str(cell) for cell in row])
        self.console.print(table)

    def progress(self, done: int, total: int, label: str = "") -> str:
        """Inline percentage bar for long-running batches.

        Returns the rendered text so callers/tests can assert on it without
        capturing stdout. A carriage return keeps a TTY on one updating line
        instead of hundreds of log lines; plain mode behaves identically.
        """
        total = max(1, int(total))
        done = min(max(0, int(done)), total)
        width = 24
        filled = int(width * done / total)
        bar = "█" * filled + "░" * (width - filled)
        percent = int(100 * done / total)
        text = f"[{bar}] {percent:3d}% {done}/{total} {label}".rstrip()
        if self.console is not None:
            self.console.print(text.ljust(100), end="\r", highlight=False, markup=False)
        else:
            print("\r" + text.ljust(100), end="", flush=True)
        return text

    def progress_end(self) -> None:
        """Finish an inline progress line (call even when a batch fails)."""
        if self.console is not None:
            self.console.print()
        else:
            print(flush=True)

    def json(self, data: Any) -> None:
        self.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def ask(self, prompt: str = "请选择") -> str:
        try:
            return input(f"{prompt} > ").strip()
        except EOFError:
            return "0"

    def secret(self, prompt: str = "请输入主口令") -> str:
        # Env override keeps automated/offline runs usable without a TTY.
        preset = os.environ.get("ANONCHAINS_VAULT_PASSPHRASE")
        if preset:
            return preset
        return getpass.getpass(f"{prompt} > ")
