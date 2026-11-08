"""Format detection, parser dispatch and bounded deduplication."""

from __future__ import annotations

import importlib
import logging

from ..models.proxy_node import ProxyNode
from .base import MAX_NODES_PER_SOURCE, BaseParser, ParseError, SourceSpec, registered_parsers
from .plain import PlainParser

log = logging.getLogger(__name__)
SAMPLE_SIZE = 64 * 1024

_registered = False
_PARSER_MODULES = ("plain", "json_parser", "clash_yaml", "subscription")


def _ensure_parsers() -> None:
    """Import built-in parsers lazily to avoid a package <-> detector cycle."""
    global _registered
    if _registered:
        return
    for module in _PARSER_MODULES:
        importlib.import_module(f"{__package__}.{module}")
    _registered = True


def sniff_kind(payload: bytes, spec: SourceSpec) -> str:
    """Return the class name of the parser that would handle this payload."""
    _ensure_parsers()
    parser = _choose(payload[:SAMPLE_SIZE], spec)
    return type(parser).__name__ if parser else "none"


def _choose(sample: bytes, spec: SourceSpec) -> BaseParser | None:
    for parser_cls in registered_parsers():
        try:
            if parser_cls().can_parse(sample, spec):
                return parser_cls()
        except Exception as exc:  # pragma: no cover - defensive plugin isolation
            log.debug("parser %s sniff failed: %s", parser_cls.__name__, exc)
    return None


def parse_payload(payload: bytes, spec: SourceSpec) -> list[ProxyNode]:
    """Parse a source payload into a deduplicated, bounded list of nodes.

    A single malformed line never aborts a whole source; a source producing no
    nodes is reported by the caller as an empty result, not an exception.
    """
    _ensure_parsers()
    if not payload:
        return []
    sample = payload[:SAMPLE_SIZE]
    parser = _choose(sample, spec)
    if parser is None:
        parser = PlainParser()  # last-resort text fallback
    nodes: list[ProxyNode] = []
    seen: set[str] = set()
    try:
        for node in parser.parse(payload, spec):
            key = node.stable_key()
            if key in seen:
                continue
            seen.add(key)
            nodes.append(node)
            if len(nodes) >= MAX_NODES_PER_SOURCE:
                log.warning("source %s truncated at %d nodes", spec.name, MAX_NODES_PER_SOURCE)
                break
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ParseError(f"source {spec.name}: {type(parser).__name__} failed: {exc}") from exc
    log.debug("source %s -> %s -> %d nodes", spec.name, type(parser).__name__, len(nodes))
    return nodes
