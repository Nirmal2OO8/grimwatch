"""Small, failure-safe diagnostics for grimwatch's networked features."""

from __future__ import annotations

import logging
import os
import re
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FILE_NAME = "grimwatch.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
_CONFIGURE_LOCK = threading.Lock()


def grimwatch_data_dir() -> Path:
    """Return the user-local grimwatch directory, including the test override."""
    override = os.environ.get("GRIMWATCH_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".grimwatch"


def get_logger(name: str) -> logging.Logger:
    """Return a logger backed by a small rotating user-local debug log.

    Logging must never make an otherwise usable command fail, so inability to
    create the log simply leaves the named logger without a file handler.
    """
    root = logging.getLogger("grimwatch")
    path = grimwatch_data_dir() / LOG_FILE_NAME
    with _CONFIGURE_LOCK:
        configured = any(getattr(handler, "_grimwatch_log_path", None) == str(path) for handler in root.handlers)
        if not configured:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    path,
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT,
                    encoding="utf-8",
                )
                handler._grimwatch_log_path = str(path)  # type: ignore[attr-defined]
                handler.setFormatter(
                    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
                )
                root.addHandler(handler)
                root.setLevel(logging.DEBUG)
            except OSError:
                # Avoid routing diagnostic failures to the user's terminal
                # through the process root logger when the log directory is
                # unavailable (for example, a sandboxed test environment).
                if not root.handlers:
                    root.addHandler(logging.NullHandler())
        root.setLevel(logging.DEBUG)
        root.propagate = False
    return logging.getLogger(f"grimwatch.{name}")


def redact_sensitive(value: object) -> str:
    """Keep API credentials out of a diagnostic log while retaining errors."""
    text = str(value)
    text = re.sub(r"(?i)([?&](?:api_?)?key=)[^&\s]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[redacted]", text)
    return text


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Record a source/provider failure without leaking an API key."""
    logger.error("%s: %s", message, redact_sensitive(exc), exc_info=True)
