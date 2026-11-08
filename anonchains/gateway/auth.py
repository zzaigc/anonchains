"""Local-only API token management.

The token is persisted ONLY through the Vault (as a sealed blob). If the Vault
is locked, disabled or unavailable, an ephemeral in-memory token is used and
never written to disk. Verification always uses a constant-time comparison.
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

from ..crypto.redaction import constant_time_eq
from ..crypto.vault import Vault

log = logging.getLogger(__name__)


class TokenManager:
    def __init__(self, vault: Vault | None, path: Path, *, required: bool = True) -> None:
        self.vault = vault
        self.path = Path(path)
        self.required = required
        self._token: str | None = None
        self._ephemeral = False
        self._load_or_create()

    # --- lifecycle ----------------------------------------------------

    def _load_or_create(self) -> None:
        sealed = self._read_sealed()
        if sealed and self.vault is not None:
            data = self.vault.open_dict(sealed, aad=b"gateway-token")
            candidate = data.get("token") if data else None
            if isinstance(candidate, str) and len(candidate) >= 16:
                self._token = candidate
                self._ephemeral = False
                return
            log.warning("sealed gateway token could not be opened; generating an ephemeral one")
        self._token = secrets.token_urlsafe(32)
        self._ephemeral = True
        self._persist()

    def _read_sealed(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            value = payload.get("sealed")
            return value if isinstance(value, str) else None
        except (OSError, ValueError):
            return None

    def _persist(self) -> None:
        if self._token is None:
            return
        if self.vault is None or not self.vault.is_unlocked:
            log.warning(
                "Vault unavailable: API token is ephemeral and will change on restart"
            )
            return
        sealed = self.vault.seal_dict({"token": self._token}, aad=b"gateway-token")
        if sealed is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"sealed": sealed}), encoding="utf-8")
            self._ephemeral = False
        except OSError as exc:  # pragma: no cover - filesystem dependent
            log.warning("could not persist gateway token: %s", exc)

    # --- API ----------------------------------------------------------

    @property
    def token(self) -> str | None:
        return self._token if self.required else None

    def display(self) -> str:
        if not self.required:
            return "disabled (token_required = false)"
        if self._token is None:
            return "unavailable"
        return f"{self._token[:6]}...{self._token[-4:]} (full token shown once at startup)"

    def verify(self, provided: str | None) -> bool:
        if not self.required:
            return True
        if not provided or self._token is None:
            return False
        if provided.lower().startswith("bearer "):
            provided = provided[7:].strip()
        return constant_time_eq(provided, self._token)

    def rotate(self) -> str | None:
        self._token = secrets.token_urlsafe(32)
        self._persist()
        return self._token

    def refresh(self) -> bool:
        """Called after the Vault is unlocked; seal an ephemeral token if possible."""
        if self._ephemeral and self.vault is not None and self.vault.is_unlocked:
            self._persist()
        return not self._ephemeral

    def status(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "ephemeral": self._ephemeral,
            "persisted": self.path.is_file(),
            "preview": self.display(),
        }
