"""Load, merge and manage the source registry."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from ..parser.base import SourceSpec
from ..utils.paths import display_path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    enabled: bool = False
    max_results: int = 20
    search_queries: tuple[str, ...] = ()
    github_repositories: tuple[str, ...] = ()
    raw_candidate_paths: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = (".txt", ".json", ".yaml", ".yml")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DiscoveryConfig:
        data = data or {}

        def tup(key: str) -> tuple[str, ...]:
            value = data.get(key) or ()
            return tuple(str(v) for v in value)

        return cls(
            enabled=bool(data.get("enabled", False)),
            max_results=max(1, int(data.get("max_results", 20))),
            search_queries=tup("search_queries"),
            github_repositories=tup("github_repositories"),
            raw_candidate_paths=tup("raw_candidate_paths"),
            file_extensions=tup("file_extensions") or (".txt", ".json", ".yaml", ".yml"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_results": self.max_results,
            "search_queries": list(self.search_queries),
            "github_repositories": list(self.github_repositories),
            "raw_candidate_paths": list(self.raw_candidate_paths),
            "file_extensions": list(self.file_extensions),
        }


class SourceRegistry:
    """Ordered collection of :class:`SourceSpec` plus discovery settings."""

    def __init__(
        self,
        specs: list[SourceSpec],
        discovery: DiscoveryConfig | None = None,
        *,
        user_path: Path | None = None,
    ) -> None:
        self._specs: dict[str, SourceSpec] = {s.name: s for s in specs}
        self.discovery = discovery or DiscoveryConfig()
        self.user_path = user_path

    # --- loading ------------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> tuple[list[SourceSpec], DiscoveryConfig]:
        if not path.is_file():
            raise FileNotFoundError(f"source registry not found: {display_path(path)}")
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"invalid source registry (expected mapping): {display_path(path)}")
        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, list):
            raise ValueError(f"invalid 'sources' list in {display_path(path)}")
        specs: list[SourceSpec] = []
        for item in raw_sources:
            if not isinstance(item, dict):
                log.warning("skipping non-mapping source entry in %s", display_path(path))
                continue
            try:
                specs.append(SourceSpec.from_dict(item))
            except (KeyError, ValueError) as exc:
                log.warning("skipping invalid source entry in %s: %s", display_path(path), exc)
        return specs, DiscoveryConfig.from_dict(data.get("discovery"))

    @classmethod
    def load(cls, primary: Path, extras: list[Path] | None = None) -> SourceRegistry:
        """Load built-ins then merge user files (same ``name`` overrides)."""
        builtins, discovery = cls.read_file(Path(primary))
        registry = cls(builtins, discovery, user_path=(extras[0] if extras else None))
        for extra in extras or []:
            if not Path(extra).is_file():
                log.debug("optional source file absent: %s", display_path(extra))
                continue
            try:
                overrides, extra_discovery = cls.read_file(Path(extra))
            except (OSError, ValueError, yaml.YAMLError) as exc:
                log.warning("ignoring invalid source file %s: %s", display_path(extra), exc)
                continue
            for spec in overrides:
                registry._specs[spec.name] = spec
            if extra_discovery.enabled:
                registry.discovery = extra_discovery
        return registry

    # --- queries ------------------------------------------------------

    @property
    def all_specs(self) -> list[SourceSpec]:
        return list(self._specs.values())

    @property
    def enabled_specs(self) -> list[SourceSpec]:
        return [s for s in self._specs.values() if s.enabled]

    def by_name(self, name: str) -> SourceSpec | None:
        return self._specs.get(name)

    def stats(self) -> dict[str, int]:
        specs = self.all_specs
        return {
            "total": len(specs),
            "enabled": sum(1 for s in specs if s.enabled),
            "disabled": sum(1 for s in specs if not s.enabled),
            "kinds": len({s.kind for s in specs}),
        }

    # --- mutation -----------------------------------------------------

    def toggle(self, name: str, enabled: bool) -> SourceSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise KeyError(f"unknown source: {name}")
        updated = replace(spec, enabled=enabled)
        self._specs[name] = updated
        return updated

    def upsert(self, spec: SourceSpec) -> None:
        self._specs[spec.name] = spec

    def remove(self, name: str) -> bool:
        return self._specs.pop(name, None) is not None

    def save_user_sources(self, path: Path | None = None) -> Path:
        """Persist user-owned entries (everything not in the built-in file)."""
        target = Path(path or self.user_path or "config/sources.local.yaml")
        builtin_names: set[str] = set()
        try:
            builtin_names = {
                s.name
                for s in self.read_file(Path(__file__).resolve().parents[2] / "config" / "sources.yaml")[0]
            }
        except (OSError, ValueError):  # pragma: no cover - defensive
            pass
        custom = [s for name, s in self._specs.items() if name not in builtin_names]
        payload = {
            "version": 1,
            "sources": [s.to_dict() for s in custom],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return target
