"""Shared exception types for Footprinter."""


class DatabaseNotInitializedError(Exception):
    """Raised when the database exists but has no tables (uninitialized)."""
