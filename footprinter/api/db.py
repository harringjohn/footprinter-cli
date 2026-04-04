"""Database connection for Footprinter HTTP API."""

import sqlite3
from contextlib import contextmanager

from footprinter.paths import get_db_path
from footprinter.services.access_service import load_globals


class DatabaseNotInitializedError(Exception):
    """Raised when the database exists but has no tables (uninitialized)."""


def _check_db_initialized(conn: sqlite3.Connection) -> None:
    """Check that the database has been initialized with the expected schema.

    Uses the ``files`` table as a sentinel — if it's missing, the database
    has never been populated by ``fp ingest``.
    """
    row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='files'").fetchone()
    if row[0] == 0:
        raise DatabaseNotInitializedError()


@contextmanager
def get_db():
    """Context manager for database connections.

    Divergences from MCP's ``get_db()``:

    - No ``PRAGMA query_only`` — the HTTP API uses Role.ADMIN and may need
      write access for future endpoints.
    - No ``handle_db_errors`` decorator — ``DatabaseNotInitializedError`` is
      caught by a FastAPI exception handler registered in ``server.create_app()``.

    Calls ``load_globals()`` to refresh the global visibility/permission
    policy cache in ``access_service`` for the current request.
    """
    conn = sqlite3.connect(str(get_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _check_db_initialized(conn)
        load_globals(conn)
        yield conn
    finally:
        conn.close()


def get_conn():
    """FastAPI dependency that yields a database connection.

    Usage::

        @router.get("/endpoint")
        def handler(conn=Depends(get_conn)):
            ...
    """
    with get_db() as conn:
        yield conn
