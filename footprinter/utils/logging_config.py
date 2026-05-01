"""Centralized logging configuration for Footprinter."""

import logging
import os
import sys
from pathlib import Path

_configured = False


def setup_logging(level=None):
    """Configure the root logger. Only the first call takes effect.

    Level resolution order:
    1. Explicit ``level`` argument (if provided)
    2. ``LOG_LEVEL`` environment variable (e.g. ``LOG_LEVEL=DEBUG``)
    3. Falls back to INFO
    """
    global _configured
    if _configured:
        return
    _configured = True

    if level is None:
        env_level = os.environ.get("LOG_LEVEL", "").upper()
        level = getattr(logging, env_level, None) if env_level else None
        if level is None:
            level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )


def add_file_handler(log_path: Path, level: int = logging.DEBUG) -> logging.FileHandler:
    """Add a file handler to the root logger for pipeline run logging.

    Creates parent directories, sets a timestamped format, and suppresses
    noisy schema migration logs. Returns the handler so it can be removed
    after the run.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(str(log_path))
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.root.addHandler(handler)

    # Ensure root logger level doesn't gate the file handler.
    # --quiet suppresses Rich console output but NOT file logging.
    if logging.root.level > level:
        logging.root.setLevel(level)

    # Suppress schema migration noise (INFO-level chatter on every run).
    # Uses a handler filter instead of mutating the logger level so the
    # suppression disappears when the handler is removed.
    handler.addFilter(_schema_noise_filter)

    return handler


def _schema_noise_filter(record: logging.LogRecord) -> bool:
    """Allow all records except low-level schema migration noise."""
    if record.name.startswith("footprinter.ingest.db.schema"):
        return record.levelno >= logging.WARNING
    return True
