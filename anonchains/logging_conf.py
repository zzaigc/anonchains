"""Centralised logging with credential redaction and noisy-log sampling."""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any

from .crypto.redaction import redact

_configured = False


class RedactingFilter(logging.Filter):
    """Mask passphrases, tokens and proxy credentials in every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # pragma: no cover - logging must never crash the app
            record.msg = "<log record suppressed: redaction failed>"
            record.args = ()
        return True


class SamplingFilter(logging.Filter):
    """Allow at most ``max_per_window`` identical messages per time window."""

    def __init__(self, window_seconds: float = 5.0, max_per_window: int = 3) -> None:
        super().__init__()
        self.window = max(0.1, window_seconds)
        self.limit = max(1, max_per_window)
        self._seen: dict[str, tuple[float, int]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            return True
        key = f"{record.levelno}:{record.name}:{str(record.msg)[:160]}"
        now = time.monotonic()
        started, count = self._seen.get(key, (now, 0))
        if now - started > self.window:
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] <= self.window}
            started, count = now, 0
        if count >= self.limit:
            return False
        self._seen[key] = (started, count + 1)
        return True


def setup_logging(
    level: str = "INFO",
    log_file: Path | None = None,
    *,
    redact_logs: bool = True,
    sample_window_seconds: float = 5.0,
    force: bool = False,
) -> logging.Logger:
    """Configure the root logger once; safe to call repeatedly."""
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        return root
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file is not None:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler: logging.Handler = logging.handlers.RotatingFileHandler(
                Path(log_file), maxBytes=2 << 20, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:  # pragma: no cover - filesystem dependent
            logging.getLogger(__name__).warning("file logging disabled: %s", exc)

    if redact_logs:
        redactor = RedactingFilter()
        for handler in root.handlers:
            handler.addFilter(redactor)
    sampler = SamplingFilter(sample_window_seconds)
    for handler in root.handlers:
        handler.addFilter(sampler)

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_dict(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Structured-ish logging helper; values pass through the redaction filter."""
    payload = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.log(level, "%s | %s", message, payload)
