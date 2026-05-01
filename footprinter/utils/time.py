"""Time utilities for consistent UTC timestamp handling."""

from datetime import datetime, timezone

UTC_FMT = "%Y-%m-%d %H:%M:%S"
"""Timestamp format matching SQLite CURRENT_TIMESTAMP: YYYY-MM-DD HH:MM:SS (UTC)."""


def utc_now_iso() -> str:
    """Current UTC time in SQLite CURRENT_TIMESTAMP format."""
    return datetime.now(timezone.utc).strftime(UTC_FMT)
