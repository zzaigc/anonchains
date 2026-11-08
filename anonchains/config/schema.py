"""Typed configuration model.

The main configuration file is INI style (proxychains-like) and is parsed by
:class:`configparser.ConfigParser`. Environment variables of the form
``ANONCHAINS_<SECTION>_<OPTION>`` override file values.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from ..utils.net import clamp
from ..utils.paths import display_path

CHAIN_MODES = ("strict", "dynamic", "random", "round_robin")
STEALTH_LEVELS = (1, 2, 3, 4)
KDF_CHOICES = ("auto", "argon2id", "scrypt")
CORE_CHOICES = ("sing-box", "xray", "auto")


class ConfigError(ValueError):
    """Raised for invalid or out-of-range configuration values."""


@dataclass(frozen=True, slots=True)
class ChainConfig:
    hops: int = 5
    mode: str = "strict"
    rotation_seconds: int = 0
    prefer_protocols: tuple[str, ...] = ()
    allow_reuse: bool = False


@dataclass(frozen=True, slots=True)
class PoolsConfig:
    candidate_dir: Path = Path("pools/candidate")
    verified_dir: Path = Path("pools/verified")
    batch_size: int = 500


@dataclass(frozen=True, slots=True)
class CrawlerConfig:
    concurrency: int = 32
    timeout_seconds: float = 15.0
    retries: int = 2
    rate_limit_per_second: float = 8.0
    max_response_mb: float = 8.0
    stealth_level: int = 2
    user_sources_file: Path = Path("config/sources.local.yaml")
    discovery_enabled: bool = False
    respect_robots: bool = True


@dataclass(frozen=True, slots=True)
class ValidatorConfig:
    concurrency: int = 64
    timeout_seconds: float = 8.0
    interval_seconds: int = 600
    ttl_seconds: int = 1800
    max_consecutive_failures: int = 3
    echo_urls: tuple[str, ...] = (
        "https://api.ipify.org?format=json",
        "https://httpbin.org/get",
    )
    min_anonymity: str = "unknown"
    min_score: float = 0.0


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8790
    entry_port: int = 8791
    token_required: bool = True


@dataclass(frozen=True, slots=True)
class CoreConfig:
    enabled: bool = True
    prefer: str = "auto"
    sing_box_path: str = "sing-box"
    xray_path: str = "xray"
    startup_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class VaultConfig:
    enabled: bool = True
    path: Path = Path("pools/.vault")
    kdf: str = "auto"
    key_ttl_seconds: int = 900
    cipher: str = "aes-256-gcm"


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str = "INFO"
    file: Path | None = Path("logs/anonchains.log")
    redact: bool = True
    sample_window_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class AppConfig:
    base_dir: Path
    chain: ChainConfig = ChainConfig()
    pools: PoolsConfig = PoolsConfig()
    crawler: CrawlerConfig = CrawlerConfig()
    validator: ValidatorConfig = ValidatorConfig()
    gateway: GatewayConfig = GatewayConfig()
    core: CoreConfig = CoreConfig()
    vault: VaultConfig = VaultConfig()
    logging: LoggingConfig = LoggingConfig()
    config_path: Path | None = None

    # --- construction -------------------------------------------------

    @classmethod
    def from_parser(
        cls, parser: configparser.ConfigParser, base_dir: Path, config_path: Path | None = None
    ) -> AppConfig:
        get = _Getter(parser)

        def path_of(value: str) -> Path:
            p = Path(value)
            return p if p.is_absolute() else (base_dir / p)

        cfg = cls(
            base_dir=base_dir,
            config_path=config_path,
            chain=ChainConfig(
                hops=get.int("chain", "hops", 5),
                mode=get.str("chain", "mode", "strict").lower(),
                rotation_seconds=get.int("chain", "rotation_seconds", 0),
                prefer_protocols=get.tuple("chain", "prefer_protocols", ()),
                allow_reuse=get.boolean("chain", "allow_reuse", False),
            ),
            pools=PoolsConfig(
                candidate_dir=path_of(get.str("pools", "candidate_dir", "pools/candidate")),
                verified_dir=path_of(get.str("pools", "verified_dir", "pools/verified")),
                batch_size=get.int("pools", "batch_size", 500),
            ),
            crawler=CrawlerConfig(
                concurrency=get.int("crawler", "concurrency", 32),
                timeout_seconds=get.float("crawler", "timeout_seconds", 15.0),
                retries=get.int("crawler", "retries", 2),
                rate_limit_per_second=get.float("crawler", "rate_limit_per_second", 8.0),
                max_response_mb=get.float("crawler", "max_response_mb", 8.0),
                stealth_level=get.int("crawler", "stealth_level", 2),
                user_sources_file=path_of(
                    get.str("crawler", "user_sources_file", "config/sources.local.yaml")
                ),
                discovery_enabled=get.boolean("crawler", "discovery_enabled", False),
                respect_robots=get.boolean("crawler", "respect_robots", True),
            ),
            validator=ValidatorConfig(
                concurrency=get.int("validator", "concurrency", 64),
                timeout_seconds=get.float("validator", "timeout_seconds", 8.0),
                interval_seconds=get.int("validator", "interval_seconds", 600),
                ttl_seconds=get.int("validator", "ttl_seconds", 1800),
                max_consecutive_failures=get.int("validator", "max_consecutive_failures", 3),
                echo_urls=get.tuple(
                    "validator",
                    "echo_urls",
                    ("https://api.ipify.org?format=json", "https://httpbin.org/get"),
                ),
                min_anonymity=get.str("validator", "min_anonymity", "unknown"),
                min_score=get.float("validator", "min_score", 0.0),
            ),
            gateway=GatewayConfig(
                enabled=get.boolean("gateway", "enabled", False),
                host=get.str("gateway", "host", "127.0.0.1"),
                port=get.int("gateway", "port", 8790),
                entry_port=get.int("gateway", "entry_port", 8791),
                token_required=get.boolean("gateway", "token_required", True),
            ),
            core=CoreConfig(
                enabled=get.boolean("core", "enabled", True),
                prefer=get.str("core", "prefer", "auto"),
                sing_box_path=get.str("core", "sing_box_path", "sing-box"),
                xray_path=get.str("core", "xray_path", "xray"),
                startup_timeout_seconds=get.float("core", "startup_timeout_seconds", 10.0),
            ),
            vault=VaultConfig(
                enabled=get.boolean("vault", "enabled", True),
                path=path_of(get.str("vault", "path", "pools/.vault")),
                kdf=get.str("vault", "kdf", "auto"),
                key_ttl_seconds=get.int("vault", "key_ttl_seconds", 900),
                cipher=get.str("vault", "cipher", "aes-256-gcm"),
            ),
            logging=LoggingConfig(
                level=get.str("logging", "level", "INFO").upper(),
                file=path_of(get.str("logging", "file", "logs/anonchains.log")),
                redact=get.boolean("logging", "redact", True),
                sample_window_seconds=get.float("logging", "sample_window_seconds", 5.0),
            ),
        )
        cfg.validate()
        return cfg

    # --- validation ---------------------------------------------------

    def validate(self) -> None:
        """Reject nonsensical values. ``hops`` has no upper bound by design."""
        if self.chain.hops < 1:
            raise ConfigError("chain.hops must be >= 1 (no upper limit)")
        if self.chain.mode not in CHAIN_MODES:
            raise ConfigError(f"chain.mode must be one of {CHAIN_MODES}")
        if self.chain.rotation_seconds < 0:
            raise ConfigError("chain.rotation_seconds must be >= 0")
        if self.crawler.stealth_level not in STEALTH_LEVELS:
            raise ConfigError(f"crawler.stealth_level must be one of {STEALTH_LEVELS}")
        if self.vault.kdf not in KDF_CHOICES:
            raise ConfigError(f"vault.kdf must be one of {KDF_CHOICES}")
        if self.core.prefer not in CORE_CHOICES:
            raise ConfigError(f"core.prefer must be one of {CORE_CHOICES}")
        for name, value in (
            ("crawler.concurrency", self.crawler.concurrency),
            ("validator.concurrency", self.validator.concurrency),
            ("pools.batch_size", self.pools.batch_size),
        ):
            if value < 1:
                raise ConfigError(f"{name} must be >= 1")
        for name, value in (
            ("crawler.timeout_seconds", self.crawler.timeout_seconds),
            ("validator.timeout_seconds", self.validator.timeout_seconds),
            ("crawler.rate_limit_per_second", self.crawler.rate_limit_per_second),
            ("crawler.max_response_mb", self.crawler.max_response_mb),
        ):
            if value <= 0:
                raise ConfigError(f"{name} must be > 0")
        if self.crawler.retries < 0 or self.validator.max_consecutive_failures < 1:
            raise ConfigError("retry/failure thresholds must be non-negative")
        for port in (self.gateway.port, self.gateway.entry_port):
            if not 1 <= port <= 65535:
                raise ConfigError("gateway ports must be in 1..65535")
        if self.gateway.port == self.gateway.entry_port and self.gateway.enabled:
            raise ConfigError("gateway.port and gateway.entry_port must differ")
        if self.gateway.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("gateway must bind to a loopback address only")
        if self.vault.cipher not in {"aes-256-gcm", "chacha20-poly1305"}:
            raise ConfigError("vault.cipher must be aes-256-gcm or chacha20-poly1305")
        if self.validator.min_anonymity not in {"unknown", "transparent", "anonymous", "elite"}:
            raise ConfigError("validator.min_anonymity is invalid")

    # --- helpers ------------------------------------------------------

    def with_overrides(self, **section_values: dict[str, Any]) -> AppConfig:
        """Return a copy with selected ``section.option`` values replaced."""
        updates: dict[str, Any] = {}
        for section, values in section_values.items():
            current = getattr(self, section, None)
            if current is None or not values:
                continue
            updates[section] = replace(current, **values)
        candidate = replace(self, **updates)
        candidate.validate()
        return candidate

    def as_dict(self) -> dict[str, Any]:
        def conv(value: Any) -> Any:
            if isinstance(value, Path):
                return display_path(value, self.base_dir)
            if isinstance(value, tuple):
                return list(value)
            return value

        result: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if hasattr(value, "__dataclass_fields__"):
                result[f.name] = {k: conv(v) for k, v in asdict(value).items()}
            else:
                result[f.name] = conv(value)
        return result

    def effective_connect_timeout(self) -> float:
        """Resource guard: very long chains get a proportional timeout budget."""
        base = self.validator.timeout_seconds
        return float(clamp(base * (1.0 + 0.15 * max(0, self.chain.hops - 1)), 1.0, 120.0))


class _Getter:
    """Tolerant configparser accessor with defaults for missing sections."""

    def __init__(self, parser: configparser.ConfigParser) -> None:
        self.p = parser

    def _has(self, section: str, option: str) -> bool:
        return self.p.has_option(section, option)

    def str(self, section: str, option: str, default: str) -> str:
        value = self.p.get(section, option, fallback=default)
        return value.strip() if isinstance(value, str) else default

    def int(self, section: str, option: str, default: int) -> int:
        try:
            return self.p.getint(section, option, fallback=default)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{section}.{option} must be an integer") from exc

    def float(self, section: str, option: str, default: float) -> float:
        try:
            return self.p.getfloat(section, option, fallback=default)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{section}.{option} must be a number") from exc

    def boolean(self, section: str, option: str, default: bool) -> bool:
        try:
            return self.p.getboolean(section, option, fallback=default)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{section}.{option} must be true/false") from exc

    def tuple(self, section: str, option: str, default: tuple[str, ...]) -> tuple[str, ...]:
        raw = self.str(section, option, "")
        if not raw:
            return default
        parts = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
        return tuple(parts) if parts else default


def apply_env_overrides(parser: configparser.ConfigParser) -> list[str]:
    """Apply ``ANONCHAINS_<SECTION>_<OPTION>`` overrides in place."""
    applied: list[str] = []
    prefix = "ANONCHAINS_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if "_" not in rest:
            continue
        section, option = rest.split("_", 1)
        section, option = section.lower(), option.lower()
        if parser.has_section(section) and parser.has_option(section, option):
            parser.set(section, option, value)
            applied.append(f"{section}.{option}")
    return applied
