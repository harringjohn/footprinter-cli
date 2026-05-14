"""Database connection for Footprinter HTTP API."""

import sqlite3
from contextlib import contextmanager
from typing import Generator

from footprinter.db_base import open_checked_connection


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections.

    Divergences from MCP's ``get_db()``:

    - No ``PRAGMA query_only`` — the HTTP API uses Role.ADMIN and may need
      write access for future endpoints.
    - No ``handle_db_errors`` decorator — ``DatabaseNotInitializedError`` is
      caught by a FastAPI exception handler registered in ``server.create_app()``.

    Calls ``load_globals()`` to refresh the global visibility/permission
    policy cache in ``access_service`` for the current request.
    """
    with open_checked_connection() as conn:
        yield conn


def get_conn():
    """FastAPI dependency that yields a database connection.

    Usage::

        @router.get("/endpoint")
        def handler(conn=Depends(get_conn)):
            ...
    """
    with get_db() as conn:
        yield conn
