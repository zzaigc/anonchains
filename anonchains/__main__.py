"""Enable ``python -m anonchains``."""

from __future__ import annotations

from .cli.commands import main

if __name__ == "__main__":
    raise SystemExit(main())
