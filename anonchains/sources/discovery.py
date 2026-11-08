"""Opt-in discovery of additional public proxy sources via the GitHub API.

Discovery only *proposes* candidates. Nothing is enabled automatically: a
human reviews the list in the CLI before adding anything to the registry.
This keeps the crawler from silently following untrusted third-party URLs.

Unauthenticated GitHub API allows 60 requests/hour, so this module is strict
about request counts and only runs when explicitly invoked.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..parser.base import SourceSpec
from .registry import DiscoveryConfig

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
FetchFn = Callable[[str], Awaitable[bytes | None]]


@dataclass(frozen=True, slots=True)
class CandidateSource:
    name: str
    url: str
    kind: str
    reason: str
    repository: str = ""

    def to_spec(self, enabled: bool = False) -> SourceSpec:
        return SourceSpec.from_dict(
            {
                "name": self.name,
                "url": self.url,
                "kind": self.kind,
                "enabled": enabled,
                "weight": 1,
                "note": f"discovered: {self.reason}",
            }
        )


def _kind_for(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ("vless", "vmess", "trojan", "ss", "hy2", "tuic", "sub", "wireguard")):
        return "subscription"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    if lower.endswith(".json"):
        return "json"
    return "plain"


class SourceDiscoverer:
    def __init__(self, config: DiscoveryConfig, fetch: FetchFn) -> None:
        self.config = config
        self._fetch = fetch
        self._request_count = 0

    async def _json(self, url: str) -> Any:
        if self._request_count >= self.config.max_results:
            return None
        self._request_count += 1
        try:
            payload = await self._fetch(url)
            return json.loads(payload.decode("utf-8", "replace")) if payload else None
        except (ValueError, UnicodeError, OSError) as exc:
            log.debug("discovery request failed for %s: %s", url, exc)
            return None

    async def discover(self) -> list[CandidateSource]:
        """Return de-duplicated candidate raw URLs. Never mutates the registry."""
        found: dict[str, CandidateSource] = {}

        for query in self.config.search_queries:
            url = f"{API_ROOT}/search/repositories?q={quote(query)}&per_page=10&sort=updated"
            data = await self._json(url)
            for repo in (data or {}).get("items", [])[:10]:
                full_name = str(repo.get("full_name", ""))
                if full_name and full_name not in self.config.github_repositories:
                    self.config = DiscoveryConfig(
                        enabled=self.config.enabled,
                        max_results=self.config.max_results,
                        search_queries=self.config.search_queries,
                        github_repositories=(*self.config.github_repositories, full_name),
                        raw_candidate_paths=self.config.raw_candidate_paths,
                        file_extensions=self.config.file_extensions,
                    )

        for full_name in self.config.github_repositories:
            if len(found) >= self.config.max_results:
                break
            repo = await self._json(f"{API_ROOT}/repos/{full_name}")
            if not isinstance(repo, dict):
                continue
            branch = str(repo.get("default_branch") or "main")
            listing = await self._json(f"{API_ROOT}/repos/{full_name}/contents/?ref={quote(branch)}")
            if not isinstance(listing, list):
                continue
            for entry in listing:
                name = str(entry.get("name", ""))
                if entry.get("type") != "file" or not name.lower().endswith(
                    tuple(self.config.file_extensions)
                ):
                    continue
                raw = entry.get("download_url")
                if not raw:
                    raw = (
                        f"https://raw.githubusercontent.com/{full_name}/{branch}/{name}"
                    )
                key = str(raw)
                if key in found:
                    continue
                found[key] = CandidateSource(
                    name=f"{full_name.split('/')[-1]}-{name.rsplit('.', 1)[0]}".lower(),
                    url=key,
                    kind=_kind_for(name),
                    reason=f"file in {full_name}",
                    repository=full_name,
                )
                if len(found) >= self.config.max_results:
                    break

            for path in self.config.raw_candidate_paths:
                url = f"https://raw.githubusercontent.com/{full_name}/{branch}/{path}"
                if url not in found:
                    found[url] = CandidateSource(
                        name=f"{full_name.split('/')[-1]}-{path.rsplit('.', 1)[0]}".lower(),
                        url=url,
                        kind=_kind_for(path),
                        reason=f"conventional path in {full_name}",
                        repository=full_name,
                    )
        return list(found.values())[: self.config.max_results]
