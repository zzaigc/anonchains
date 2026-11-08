"""Cross-platform path rendering for logs, CLI output and REST responses.

Internal filesystem operations still resolve paths (that is the portable way to
open files in Python), but nothing user-facing should expose a local drive
letter, a home directory or a machine-specific folder layout. ``display_path``
renders a path relative to the project root and falls back to a bare file name
when the path lies outside the project, which also removes private information
from logs and exported status data.
"""

from __future__ import annotations

from pathlib import Path


def display_path(value: str | Path, base_dir: str | Path | None = None) -> str:
    """Return a POSIX-style, project-relative path suitable for display.

    Examples (base_dir = ``/srv/anonchains``):

    * ``/srv/anonchains/pools/candidate/candidate.db`` -> ``pools/candidate/candidate.db``
    * ``C:\\tmp\\secret.conf`` -> ``secret.conf``
    * ``pools/.vault`` -> ``pools/.vault``
    """
    raw = str(value)
    path = Path(raw)
    if base_dir is not None:
        try:
            relative = path.resolve().relative_to(Path(base_dir).resolve())
            return relative.as_posix() or "."
        except (ValueError, OSError, RuntimeError):
            pass
    # ``is_absolute()`` is platform-specific: on Windows a POSIX path such as
    # /opt/elsewhere/x is not "absolute", yet it still leaks a directory layout.
    unix_absolute = raw.replace("\\", "/").startswith("/")
    if path.is_absolute() or unix_absolute:
        # Outside the project: keep only the file name, never the user's
        # home directory / drive layout.
        return path.name or raw.replace("\\", "/").rstrip("/") or "."
    return path.as_posix() or "."
