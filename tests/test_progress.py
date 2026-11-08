"""Offline tests for progress reporting, relative paths and traffic de-dup."""

from __future__ import annotations

import asyncio

from anonchains.cli.commands import _crawl
from anonchains.cli.menus import UI
from anonchains.fetcher import FetchResult
from anonchains.fetcher.crawler import Crawler
from anonchains.parser.base import SourceSpec
from anonchains.utils.paths import display_path
from anonchains.validator.batch import ValidationResult, Validator


def _specs(count: int) -> list[SourceSpec]:
    return [
        SourceSpec(name=f"source-{i}", url=f"https://example.test/{i}.txt", kind="plain")
        for i in range(count)
    ]


def test_progress_bar_renders_percentage_and_counts():
    ui = UI(force_plain=True)
    assert "0%" in ui.progress(0, 10)
    text = ui.progress(3, 10, "源 | 成功 3")
    assert "30%" in text and "3/10" in text
    assert "█" in text and "░" in text and "成功 3" in text
    assert "100%" in ui.progress(10, 10)
    assert "100%" in ui.progress(99, 10)  # clamped
    ui.progress_end()


def test_display_path_is_relative_and_sanitised(tmp_path):
    base = tmp_path / "proj"
    assert display_path(base / "pools" / "candidate.db", base) == "pools/candidate.db"
    assert display_path(base / "config" / "anonchains.conf", base) == "config/anonchains.conf"
    assert display_path(base, base) == "."
    assert display_path("pools/.vault") == "pools/.vault"
    # Outside the project: only the file name survives, never the home dir.
    assert display_path("C:\\tmp\\secret.conf", base) == "secret.conf"
    assert display_path("/opt/elsewhere/config.conf", base) == "config.conf"


def test_status_paths_are_relative(config_copy):
    from anonchains.cli.app import AppContext

    ctx = AppContext(config_copy.path, base_dir=config_copy.base_dir)
    try:
        status = ctx.status()
        # config_copy lives at the root of tmp_path, so it renders as a bare name.
        assert status["config"]["config_file"] == "anonchains.conf"
        assert status["pools"]["candidate"]["path"] == "pools/candidate/candidate.db"
        assert status["pools"]["verified"]["path"] == "pools/verified/verified.db"
        assert status["vault"]["path"] == "pools/.vault"
        rendered = str(status)
        assert ":\\" not in rendered and "/opt/elsewhere" not in rendered
    finally:
        asyncio.run(ctx.shutdown())


def test_crawl_progress_reaches_every_source(config_copy, sources_file, monkeypatch):
    import anonchains.fetcher.crawler as crawler_module

    class _FakeSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def fake_one(self, session, spec):
        return FetchResult(spec, ok=True, nodes=[])

    monkeypatch.setattr(crawler_module, "FetchSession", _FakeSession)
    monkeypatch.setattr(Crawler, "_one", fake_one)

    from anonchains.sources.registry import SourceRegistry

    crawler = Crawler(config_copy.config, SourceRegistry.load(sources_file))
    seen: list[str] = []
    results = asyncio.run(crawler.run(_specs(6), on_result=lambda r: seen.append(r.spec.name)))
    assert len(results) == 6 and len(seen) == 6


def test_cli_crawl_renders_progress(config_copy, monkeypatch, capsys):
    from anonchains.cli.app import AppContext

    config, base = config_copy.path, config_copy.base_dir
    specs = _specs(4)

    async def fake_run(self, targets, *, on_result=None):
        out = []
        for spec in targets or specs:
            result = FetchResult(spec, ok=True, nodes=[], elapsed_ms=1.0)
            if on_result:
                on_result(result)
            out.append(result)
        return out

    monkeypatch.setattr(Crawler, "run", fake_run)
    ctx = AppContext(config, base_dir=base)
    try:
        asyncio.run(_crawl(ctx, UI(force_plain=True), specs))
    finally:
        asyncio.run(ctx.shutdown())
    output = capsys.readouterr().out
    assert "100%" in output and "4/4" in output
    assert "完成：源 4/4 成功" in output


def test_validation_progress_callback(config_copy, monkeypatch):
    import anonchains.validator.batch as batch_module
    from anonchains.models.proxy_node import Protocol, ProxyNode

    class _FakeEchoChecker:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def fake_one(self, checker, node):
        return ValidationResult(node, ok=True)

    monkeypatch.setattr(batch_module, "EchoChecker", _FakeEchoChecker)
    monkeypatch.setattr(Validator, "_one", fake_one)

    nodes = [ProxyNode(protocol=Protocol.HTTP, host="1.2.3.4", port=8080 + i) for i in range(5)]
    seen: list[tuple[int, int]] = []
    results = asyncio.run(
        Validator(config_copy.config).validate_many(
            nodes, on_progress=lambda done, total, _r: seen.append((done, total))
        )
    )
    assert len(results) == 5
    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
