"""External core discovery, process lifecycle and graceful degradation."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config.schema import AppConfig
from ..utils.paths import display_path
from .config_gen import CoreConfigError, GeneratedConfig, generate

if TYPE_CHECKING:
    from ..chain.builder import Chain

log = logging.getLogger(__name__)

INSTALL_HINT = (
    "Run `python scripts/install_singbox.py` (or CLI menu 4 -> 10) to install "
    "sing-box into vendor/sing-box automatically; for Xray-core download from "
    "https://github.com/XTLS/Xray-core/releases, then set "
    "core.sing_box_path / core.xray_path in config/anonchains.conf."
)


class CoreManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._temp_dir: Path | None = None
        self._config_path: Path | None = None
        self._core: str | None = None
        self._port: int | None = None
        self._last_error: str | None = None

    # --- discovery ----------------------------------------------------

    def binaries(self) -> dict[str, str | None]:
        return {
            "sing-box": shutil.which(self.config.core.sing_box_path),
            "xray": shutil.which(self.config.core.xray_path),
        }

    def detect(self) -> str | None:
        if not self.config.core.enabled:
            return None
        found = self.binaries()
        prefer = self.config.core.prefer
        order = [prefer] if prefer in {"sing-box", "xray"} else ["sing-box", "xray"]
        for core in order:
            if found.get(core):
                return core
        return next((core for core, path in found.items() if path), None)

    def install_hint(self) -> str:
        return INSTALL_HINT

    # --- lifecycle ----------------------------------------------------

    async def start(self, chain: Chain, *, listen_port: int | None = None) -> tuple[bool, str]:
        core = self.detect()
        if core is None:
            self._last_error = "no external core binary found"
            return False, f"{self._last_error}. {INSTALL_HINT}"
        port = listen_port or self.config.gateway.entry_port + 1
        try:
            generated = generate(
                chain, core, listen_host=self.config.gateway.host, listen_port=port
            )
        except CoreConfigError as exc:
            self._last_error = str(exc)
            return False, str(exc)
        for warning in generated.warnings:
            log.warning("core config: %s", warning)

        await self.stop()
        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="anonchains-core-"))
            self._config_path = self._temp_dir / "config.json"
            self._config_path.write_text(generated.to_json(), encoding="utf-8")
            command = (
                [str(self.binaries()[core]), "run", "-c", str(self._config_path)]
                if core == "sing-box"
                else [str(self.binaries()[core]), "run", "-config", str(self._config_path)]
            )
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._core, self._port = core, port
            ready = await self._wait_ready(port, self.config.core.startup_timeout_seconds)
            if not ready:
                detail = await self._stderr_snippet()
                await self.stop()
                self._last_error = f"core did not become ready: {detail}"
                return False, self._last_error
            self._last_error = None
            log.info("external core %s listening on %s:%d", core, self.config.gateway.host, port)
            return True, f"{core} started on port {port} ({generated.exported_hops} hops)"
        except (OSError, ValueError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            await self.stop()
            return False, self._last_error

    async def _wait_ready(self, port: int, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + max(1.0, timeout)
        while asyncio.get_running_loop().time() < deadline:
            if self._process is not None and self._process.returncode is not None:
                return False
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.config.gateway.host, port), 0.5
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.2)
        return False

    async def _stderr_snippet(self) -> str:
        if self._process is None or self._process.stderr is None:
            return "no stderr"
        try:
            data = await asyncio.wait_for(self._process.stderr.read(2048), 0.5)
            return data.decode("utf-8", "replace").strip()[:400] or "process exited"
        except (asyncio.TimeoutError, OSError):
            return "process exited without output"

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:  # pragma: no cover
                    pass
        if self._temp_dir is not None:
            # Config files contain node credentials: never leave them behind.
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
            self._config_path = None
        self._core = None
        self._port = None

    # --- status -------------------------------------------------------

    def status(self) -> dict[str, Any]:
        running = self._process is not None and self._process.returncode is None
        return {
            "running": running,
            "core": self._core,
            "pid": self._process.pid if running and self._process else None,
            "port": self._port,
            "binaries": {
                name: display_path(path) if path else None
                for name, path in self.binaries().items()
            },
            "enabled": self.config.core.enabled,
            "last_error": self._last_error,
            "install_hint": None if self.detect() else INSTALL_HINT,
        }

    def dry_run(self, chain: Chain) -> dict[str, Any]:
        """Generate a config without launching anything (used by CLI/API)."""
        core = self.detect() or "sing-box"
        generated: GeneratedConfig = generate(
            chain, core, listen_host=self.config.gateway.host,
            listen_port=self.config.gateway.entry_port + 1,
        )
        return {
            "core": generated.core,
            "exported_hops": generated.exported_hops,
            "warnings": list(generated.warnings),
            "config": json.loads(generated.to_json()),
        }
