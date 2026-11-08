"""Numeric main menu / submenu definitions and terminal rendering.

The renderer is self-contained: ANSI colours (enabled on Windows 10+
consoles), east-asian-width aware table alignment and truncation-based
columns. It never wraps table rows, so the layout stays intact in
cmd.exe / Windows Terminal / any CJK font.
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from ..stealth.levels import choices

if sys.platform == "win32":
    os.system("")  # enable ANSI escape sequences on Windows 10+ consoles

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def color(text: str, code: str) -> str:
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return color(text, "1")


def cyan(text: str) -> str:
    return color(text, "36")


def green(text: str) -> str:
    return color(text, "32")


def yellow(text: str) -> str:
    return color(text, "33")


def red(text: str) -> str:
    return color(text, "31")


def dim(text: str) -> str:
    return color(text, "2")


def _width(text: str) -> int:
    """Display width: CJK fullwidth glyphs count as 2 columns."""
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def _truncate(text: str, width: int) -> str:
    """Cut ``text`` to at most ``width`` display columns, ellipsis on cut."""
    if _width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _fit_widths(widths: list[int], budget: int, minimum: int = 8) -> list[int]:
    """Shrink the widest columns until the total fits ``budget``."""
    fitted = list(widths)
    while sum(fitted) > budget and max(fitted) > minimum:
        largest = max(range(len(fitted)), key=lambda i: fitted[i])
        fitted[largest] = max(minimum, fitted[largest] - max(1, (sum(fitted) - budget) // len(fitted)))
    return fitted


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
        MenuItem("10", "安装外部核心（自动下载 sing-box）", "chain-core-install"),
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
    """ANSI renderer: CJK-safe aligned tables, menus, prompts and progress."""

    def __init__(self, *, force_plain: bool = False) -> None:
        self.plain = force_plain or not _COLOR

    # --- primitives -----------------------------------------------------

    def _c(self, fn, text: str) -> str:
        return text if self.plain else fn(text)

    def print(self, message: Any = "") -> None:
        print(message, flush=True)

    def error(self, message: str) -> None:
        self.print(self._c(red, message))

    def success(self, message: str) -> None:
        self.print(self._c(green, message))

    def warn(self, message: str) -> None:
        self.print(self._c(yellow, message))

    def confirm(self, prompt: str, default: bool = True) -> bool:
        """Y/n confirmation; Enter picks the default, EOF picks the default."""
        arrow = self._c(dim, "›")
        suffix = "[Y/n]" if default else "[y/N]"
        try:
            answer = input(f"{prompt} {suffix} {arrow} ").strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        return answer in {"y", "yes"}

    # --- layout ---------------------------------------------------------

    def banner(self, version: str | None = None) -> None:
        width = min(72, shutil.get_terminal_size((100, 24)).columns - 2)
        title = "  anonchains · 模块化高匿多跳代理链编排工具"
        if version:
            title += f"  v{version}"
        self.print()
        self.print(self._c(cyan, "═" * width))
        self.print(self._c(cyan, title))
        self.print(self._c(dim, "  仅限授权环境使用 · 使用前必读 docs/16-legal-use-and-disclaimer.md"))
        self.print(self._c(cyan, "═" * width))

    def menu(self, menu: Menu, *, status: str | None = None) -> None:
        width = min(72, shutil.get_terminal_size((100, 24)).columns - 2)
        self.print()
        self.print(f" {self._c(bold, menu.title)}")
        if status:
            self.print(f" {self._c(dim, status)}")
        rule = self._c(dim, "─" * width)
        self.print(f" {rule}")
        for item in menu.items:
            key = self._c(cyan, f"{item.key:>2}")
            label = self._c(dim, item.label) if item.key == "0" else item.label
            self.print(f"   {key}. {label}")
        self.print()

    def table(
        self,
        title: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
        *,
        highlight: int | None = None,
    ) -> None:
        """CJK-aware aligned table; cells are truncated, never wrapped.

        ``highlight`` colors the given row index green (e.g. the current
        stealth level).
        """
        if not rows:
            self.print(f"{title}: (空)")
            return
        width = shutil.get_terminal_size((100, 24)).columns
        header = [str(c) for c in columns]
        body: list[list[str]] = []
        for row in rows:
            cells = [" ".join(str(v).splitlines()) or "-" for v in row]
            body.append(cells)
        n = len(header)
        widths = [_width(h) for h in header]
        for cells in body:
            for i in range(min(n, len(cells))):
                widths[i] = max(widths[i], _width(cells[i]))
        budget = max(20, width - 2 - 2 * (n - 1))
        if n > 1 and _width(header[0]) <= 4:  # index-like column (# / 序号 / 跳)
            widths[0] = min(widths[0], 8)
        widths = _fit_widths(widths, budget)
        lines = ["  ".join(_pad(_truncate(h, widths[i]), widths[i]) for i, h in enumerate(header))]
        lines.append("  ".join("-" * widths[i] for i in range(n)))
        for cells in body:
            lines.append(
                "  ".join(
                    _pad(_truncate(cells[i], widths[i]) if i < len(cells) else "-", widths[i])
                    for i in range(n)
                )
            )
        self.print()
        self.print(self._c(bold, title))
        self.print(self._c(dim, " " + lines[0]))
        self.print(self._c(dim, " " + lines[1]))
        for index, line in enumerate(lines[2:]):
            if index == highlight:
                self.print(self._c(green, " " + line))
            else:
                self.print(" " + line)

    def json(self, data: Any) -> None:
        self.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    # --- progress -------------------------------------------------------

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
        print("\r" + text.ljust(100), end="", flush=True)
        return text

    def progress_end(self) -> None:
        """Finish an inline progress line (call even when a batch fails)."""
        print(flush=True)

    # --- input ----------------------------------------------------------

    def ask(self, prompt: str = "请选择") -> str:
        arrow = self._c(dim, "›")
        try:
            return input(f"{prompt} {arrow} ").strip()
        except EOFError:
            return "0"

    def secret(self, prompt: str = "请输入主口令") -> str:
        # Env override keeps automated/offline runs usable without a TTY.
        preset = os.environ.get("ANONCHAINS_VAULT_PASSPHRASE")
        if preset:
            return preset
        return getpass.getpass(f"{prompt} > ")

    def pause(self, prompt: str = "按回车继续") -> None:
        try:
            input(self._c(dim, f"\n{prompt}... "))
        except (EOFError, KeyboardInterrupt):
            print()
