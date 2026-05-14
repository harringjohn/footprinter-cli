"""Database connection for Footprinter MCP server."""

import functools
from contextlib import contextmanager
from typing import Generator

import sqlite3

from footprinter.db_base import open_checked_connection
from footprinter.mcp.errors import mcp_error
from footprinter.utils.exceptions import DatabaseNotInitializedError


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for read-only database connections.

    Also calls ``load_globals()`` to refresh the global visibility/permission
    policy cache in ``access_service`` for the current request.
    """
    with open_checked_connection(read_only=True) as conn:
        yield conn


def handle_db_errors(func):
    """Decorator that catches DatabaseNotInitializedError and returns a structured MCP error."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DatabaseNotInitializedError:
            return mcp_error("DB_NOT_INITIALIZED")

    return wrapper
