"""Shared database connection setup for MCP and HTTP API interfaces."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Union

from footprinter.paths import get_db_path
from footprinter.services.access_service import load_globals
from footprinter.utils.exceptions import DatabaseNotInitializedError


def get_connection(db_path: Union[str, Path], *, read_only: bool = False) -> sqlite3.Connection:
    """Create a SQLite connection with standard PRAGMAs.

    Single source of truth for per-connection configuration: row_factory,
    busy_timeout, foreign_keys, and optionally query_only.
    """
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


def _check_db_initialized(conn: sqlite3.Connection) -> None:
    """Check that the database has been initialized with the expected schema.

    Uses the ``files`` table as a sentinel — if it's missing, the database
    has never been populated by ``fp ingest``.
    """
    row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='files'").fetchone()
    if row[0] == 0:
        raise DatabaseNotInitializedError()


@contextmanager
def open_checked_connection(*, read_only: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Open a database connection with standard PRAGMAs and initialization check.

    Both the MCP server (read-only) and the HTTP API (read-write) use this
    as their base connection setup. Interface-specific wrappers in
    ``mcp/db.py`` and ``api/db.py`` add their own error handling on top.
    """
    conn = get_connection(get_db_path(), read_only=read_only)
    try:
        _check_db_initialized(conn)
        load_globals(conn)
        yield conn
    finally:
        conn.close()
