from __future__ import annotations

import shutil

import pytest

from anonchains.cli.commands import ACTIONS, build_parser, main
from anonchains.cli.menus import MAIN, SUBMENUS, UI
from anonchains.config.loader import default_config_path


@pytest.fixture
def isolated(tmp_path):
    config = tmp_path / "anonchains.conf"
    shutil.copyfile(default_config_path(), config)
    return config, tmp_path


def args_for(isolated, *extra):
    config, base = isolated
    return ["--config", str(config), "--base-dir", str(base), *extra]


def test_menu_graph_is_complete():
    valid = set(ACTIONS) | set(SUBMENUS) | {"back", "exit"}
    for menu in (MAIN, *SUBMENUS.values()):
        assert menu.items
        for item in menu.items:
            assert item.action in valid, f"{menu.key}/{item.key} -> {item.action} is unhandled"
        assert any(item.key == "0" for item in menu.items)
    assert len(ACTIONS) >= 40
    for level in range(1, 5):
        assert f"stealth-{level}" in ACTIONS


def test_version_command(isolated):
    assert main(["version"]) == 0
    assert main(args_for(isolated, "version")) == 0


def test_offline_status_sources_config(isolated, capsys):
    assert main(args_for(isolated, "status")) == 0
    assert main(args_for(isolated, "sources")) == 0
    assert main(args_for(isolated, "config-show")) == 0
    assert main(args_for(isolated, "vault")) == 0
    output = capsys.readouterr().out
    assert "anonchains" in output or "candidate" in output


def test_config_set_and_stealth_persist(isolated):
    config, _ = isolated
    assert main(args_for(isolated, "stealth", "3")) == 0
    assert main(args_for(isolated, "config-set", "chain", "hops", "7")) == 0
    text = config.read_text(encoding="utf-8")
    assert "stealth_level = 3" in text
    assert "hops = 7" in text
    assert "; Number of hops" in text  # comments preserved


def test_gateway_switch_offline(isolated):
    assert main(args_for(isolated, "gateway", "status")) == 0
    assert main(args_for(isolated, "gateway", "off")) == 0


def test_interactive_menu_exit_path(isolated, monkeypatch, capsys):
    # 8 -> system submenu, 1 -> status action, "" -> pause, 0 -> back,
    # 0 -> exit prompt, "n" -> cancel (menu re-shown), 0 -> prompt, "" -> confirm
    inputs = iter(["8", "1", "", "0", "0", "n", "0", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert main(args_for(isolated)) == 0
    output = capsys.readouterr().out
    assert "系统配置与状态" in output
    assert "等级 2 Balanced" in output  # sticky status line under the main menu
    # exit confirmation: main menu rendered once initially + twice after cancel
    assert output.count("anonchains 主菜单") == 3


def test_parser_has_expected_commands():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["stealth", "9"])
    args = parser.parse_args(["stealth", "2"])
    assert args.command == "stealth" and args.level == 2
    assert UI(force_plain=True).plain is True
