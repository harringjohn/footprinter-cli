"""SQLite error classification for transient schema migration errors."""

import sqlite3

_SCHEMA_BUSY_PATTERNS = (
    "database schema has changed",
    "database is locked",
)

_TRANSIENT_SCHEMA_PATTERNS = _SCHEMA_BUSY_PATTERNS + (
    "no such column",
    "no such table",
)

# A vtable constructor failure is only transient in the migration-window case:
# a reader opens an FTS5 virtual table between the migration commit and
# FTS-trigger re-creation, the constructor transiently fails, and recovery
# happens on retry with a fresh connection. When the same message also carries
# the missing-module cause, the FTS5 extension is unavailable — a permanent
# failure that must surface rather than be retried and swallowed.
_VTABLE_CONSTRUCTOR_FAILED = "vtable constructor failed"
_PERMANENT_VTABLE_MARKER = "no such module"


def is_schema_busy_error(exc: sqlite3.OperationalError) -> bool:
    """True for errors that definitively indicate active schema modification."""
    msg = str(exc).lower()
    return any(p in msg for p in _SCHEMA_BUSY_PATTERNS)


def is_transient_schema_error(exc: sqlite3.OperationalError) -> bool:
    """True for errors that might be caused by concurrent schema migration."""
    msg = str(exc).lower()
    # A vtable constructor failure carrying the missing-module cause is a
    # permanent FTS5-unavailable error and must surface, even when the chained
    # message also mentions a transient pattern (e.g. "no such table").
    if _VTABLE_CONSTRUCTOR_FAILED in msg:
        return _PERMANENT_VTABLE_MARKER not in msg
    return any(p in msg for p in _TRANSIENT_SCHEMA_PATTERNS)
