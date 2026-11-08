"""Proxy payload parsers. Importing this package registers all built-ins."""

from . import clash_yaml, detector, json_parser, plain, subscription  # noqa: F401
from .base import MAX_NODES_PER_SOURCE, BaseParser, ParseError, SourceSpec, register
from .detector import parse_payload, sniff_kind
from .subscription import parse_share_link

__all__ = [
    "SourceSpec",
    "BaseParser",
    "ParseError",
    "register",
    "parse_payload",
    "sniff_kind",
    "parse_share_link",
    "MAX_NODES_PER_SOURCE",
]
