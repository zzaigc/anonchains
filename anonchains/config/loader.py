"""Configuration loading, environment overrides, hot reload and safe write-back."""

from __future__ import annotations

import asyncio
import configparser
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..utils.hashing import content_digest
from ..utils.paths import display_path
from .schema import AppConfig, ConfigError, apply_env_overrides

log = logging.getLogger(__name__)

_OPTION_LINE = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)(\s*[=:]\s*)(.*)$")


def default_config_path() -> Path:
    """Resolve ``config/anonchains.conf`` relative to the project root."""
    return Path(__file__).resolve().parents[2] / "config" / "anonchains.conf"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


class ConfigManager:
    """Single source of truth for configuration.

    * ``load`` parses and validates; failure leaves the previous config intact.
    * ``watch`` polls mtime + content hash so external edits take effect.
    * ``set_value`` edits the INI text in place, preserving comments.
    """

    def __init__(self, path: Path | None = None, base_dir: Path | None = None) -> None:
        self.path = Path(path) if path else default_config_path()
        self.base_dir = Path(base_dir) if base_dir else project_root()
        self._config: AppConfig | None = None
        self._digest: str | None = None
        self._listeners: list[Callable[[AppConfig], None]] = []

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            return self.load()
        return self._config

    def on_change(self, callback: Callable[[AppConfig], None]) -> None:
        self._listeners.append(callback)

    def _notify(self, cfg: AppConfig) -> None:
        for callback in list(self._listeners):
            try:
                callback(cfg)
            except Exception:  # pragma: no cover - listener isolation
                log.exception("config change listener failed")

    def _parse(self) -> tuple[AppConfig, str]:
        if not self.path.is_file():
            raise ConfigError(f"configuration file not found: {display_path(self.path, self.base_dir)}")
        raw = self.path.read_bytes()
        parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
        try:
            parser.read_string(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, configparser.Error) as exc:
            raise ConfigError(
                f"cannot parse {display_path(self.path, self.base_dir)}: {exc}"
            ) from exc
        overrides = apply_env_overrides(parser)
        if overrides:
            log.debug("environment overrides applied: %s", ", ".join(overrides))
        cfg = AppConfig.from_parser(parser, self.base_dir, self.path)
        return cfg, content_digest(raw)

    def load(self, *, force: bool = False) -> AppConfig:
        """Load (or reload if the file changed). Never downgrades to invalid state."""
        if self._config is not None and not force:
            try:
                _, digest = self._parse()
            except ConfigError:
                return self._config
            if digest == self._digest:
                return self._config
        cfg, digest = self._parse()
        changed = self._config is not None and cfg != self._config
        self._config, self._digest = cfg, digest
        if changed:
            log.info("configuration reloaded from %s", display_path(self.path, self.base_dir))
            self._notify(cfg)
        return cfg

    def reload(self) -> tuple[AppConfig, bool]:
        """Explicit reload. Returns ``(config, changed)``; invalid edits roll back."""
        old = self._config
        try:
            cfg, digest = self._parse()
        except ConfigError as exc:
            log.warning("config reload rejected, keeping previous values: %s", exc)
            return (old or self.config), False
        changed = old is not None and cfg != old
        self._config, self._digest = cfg, digest
        if changed:
            self._notify(cfg)
        return cfg, changed

    async def watch(self, stop: asyncio.Event, interval: float = 1.5) -> None:
        """Background hot-reload loop. Stops when ``stop`` is set."""
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                self.load()
            except ConfigError as exc:  # pragma: no cover - defensive
                log.warning("hot reload failed: %s", exc)

    def set_value(self, section: str, option: str, value: Any) -> AppConfig:
        """Persist one option, preserving comments and formatting, then reload.

        Used by the CLI for stealth level, hop count and the gateway switch.
        """
        if not self.path.is_file():
            raise ConfigError(f"configuration file not found: {display_path(self.path, self.base_dir)}")
        text = self.path.read_text(encoding="utf-8-sig")
        rendered = _render(value)
        new_text = _replace_option(text, section, option, rendered)
        if new_text is None:
            raise ConfigError(
                f"option {section}.{option} not found in "
                f"{display_path(self.path, self.base_dir)}"
            )
        if new_text == text:
            # Idempotent write: no disk I/O, no spurious reload notification.
            return self.config
        # Validate before writing: an invalid value must never hit the disk.
        parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
        parser.read_string(new_text)
        apply_env_overrides(parser)
        AppConfig.from_parser(parser, self.base_dir, self.path)
        self.path.write_text(new_text, encoding="utf-8")
        return self.reload()[0]

    def as_dict(self) -> dict[str, Any]:
        return self.config.as_dict()


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    return str(value)


def _replace_option(text: str, section: str, option: str, rendered: str) -> str | None:
    """Replace one option value without touching comments.

    Returns ``None`` when the option does not exist, otherwise the (possibly
    unchanged) text, so callers can distinguish "missing" from "no-op".
    """
    target_section = section.lower()
    current = ""
    out: list[str] = []
    replaced = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().lower()
            out.append(line)
            continue
        match = _OPTION_LINE.match(line.rstrip("\r\n"))
        if current == target_section and match and match.group(2).lower() == option.lower():
            newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            out.append(f"{match.group(1)}{match.group(2)}{match.group(3)}{rendered}{newline}")
            replaced = True
        else:
            out.append(line)
    return "".join(out) if replaced else None
