"""SQLite error classification for transient schema migration errors."""

import sqlite3

_SCHEMA_BUSY_PATTERNS = (
    "database schema has changed",
    "database is locked",
)

_TRANSIENT_SCHEMA_PATTERNS = _SCHEMA_BUSY_PATTERNS + (
    "no such column",
    "no such table",
    "vtable constructor failed",
)


def is_schema_busy_error(exc: sqlite3.OperationalError) -> bool:
    """True for errors that definitively indicate active schema modification."""
    msg = str(exc).lower()
    return any(p in msg for p in _SCHEMA_BUSY_PATTERNS)


def is_transient_schema_error(exc: sqlite3.OperationalError) -> bool:
    """True for errors that might be caused by concurrent schema migration."""
    msg = str(exc).lower()
    return any(p in msg for p in _TRANSIENT_SCHEMA_PATTERNS)
