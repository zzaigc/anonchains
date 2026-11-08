"""anonchains - modular multi-hop proxy chain orchestrator.

The package root intentionally imports only lightweight, dependency-free
modules so that ``import anonchains`` works even in a minimal install.
"""

from __future__ import annotations

from .config.schema import AppConfig
from .models.proxy_node import Anonymity, Protocol, ProxyNode

__version__ = "0.1.0"
__all__ = ["__version__", "ProxyNode", "Protocol", "Anonymity", "AppConfig"]
