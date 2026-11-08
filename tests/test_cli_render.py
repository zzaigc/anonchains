"""Offline tests for the self-drawn CLI renderer (CJK alignment, truncation)."""

from __future__ import annotations

import re

from anonchains.cli.menus import MAIN, SUBMENUS, UI, _truncate, _width

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def test_cjk_width_accounting():
    assert _width("abc") == 3
    assert _width("主菜单") == 6
    assert _width("等abc级") == 7
    cut = _truncate("主菜单很长的标题", 6)
    assert cut.startswith("主菜") and cut.endswith("…") and _width(cut) <= 6
    assert _width(_truncate("https://example.com/very/long/url", 20)) <= 20
    assert _truncate("short", 20) == "short"


def test_table_truncates_and_never_wraps(capsys):
    ui = UI(force_plain=True)
    long_url = "https://raw.githubusercontent.com/gfcom/free-proxy-list/main/all.txt"
    ui.table(
        "候选新源",
        ("名称", "类型", "URL"),
        [("free-proxy-list-all", "json", long_url)] * 3,
    )
    out = ANSI_RE.sub("", capsys.readouterr().out)
    lines = out.splitlines()
    rows = [ln for ln in lines if "json" in ln]
    assert len(rows) == 3  # each row stays on exactly one line
    assert "…" in out  # long cells are truncated, not wrapped
    # all rendered lines respect the terminal budget (default 100 cols here)
    assert max(len(ln) for ln in lines) <= 100


def test_table_empty(capsys):
    UI(force_plain=True).table("池", ("A",), [])
    assert "(空)" in capsys.readouterr().out


def test_menu_rendering_contains_title_and_keys(capsys):
    ui = UI(force_plain=True)
    ui.menu(MAIN, status="等级 2 Balanced · 跳数 5")
    out = capsys.readouterr().out
    assert "anonchains 主菜单" in out
    assert "等级 2 Balanced" in out
    for key in ("1", "9", "0"):
        assert f"{key}." in out
    ui.menu(SUBMENUS["sources"])
    assert "代理源与爬取" in capsys.readouterr().out


def test_colors_active_when_not_plain(monkeypatch):
    import anonchains.cli.menus as menus

    monkeypatch.setattr(menus, "_COLOR", True)
    ui = UI()
    assert ui.plain is False
    rendered = ui._c(menus.red, "失败")
    assert "\033[31m" in rendered
    plain = UI(force_plain=True)
    assert plain._c(menus.red, "失败") == "失败"


def test_banner_and_error(capsys):
    ui = UI(force_plain=True)
    ui.banner("0.1.0")
    ui.error("操作失败")
    out = capsys.readouterr().out
    assert "v0.1.0" in out
    assert "操作失败" in out
