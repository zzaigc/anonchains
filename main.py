"""Convenience launcher: ``python main.py [args]``."""

from __future__ import annotations

from anonchains.cli.commands import main

if __name__ == "__main__":
    raise SystemExit(main())
