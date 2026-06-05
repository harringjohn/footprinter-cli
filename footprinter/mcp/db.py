"""Database connection for Footprinter MCP server.

Schema Migration Resilience
~~~~~~~~~~~~~~~~~~~~~~~~~~~
WAL mode is set by ``init_db()`` (schema.py) and persists at the DB file
level — MCP reads already benefit from snapshot isolation.  However, WAL
does NOT prevent ``SQLITE_SCHEMA`` errors when a writer bumps
``schema_version`` (as ``_migrate_access_columns`` does).  Lock gating and
table-rebuild migrations were considered but rejected: retry with a fresh
connection is simpler, covers all transient schema errors, and adds
negligible latency (0.75 s worst-case).
"""

import functools
import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Generator

from footprinter.db_base import open_checked_connection
from footprinter.mcp.errors import mcp_error
from footprinter.utils.exceptions import DatabaseNotInitializedError
from footprinter.utils.sqlite_errors import is_transient_schema_error

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 0.25


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for read-only database connections.

    Also calls ``load_globals()`` to refresh the global visibility/permission
    policy cache in ``access_service`` for the current request.
    """
    with open_checked_connection(read_only=True) as conn:
        yield conn


def handle_db_errors(func):
    """Decorator that catches database errors and returns structured MCP errors.

    Retries transient schema errors (e.g. during a concurrent migration) by
    re-invoking *func*.  Each MCP tool opens its own connection via
    ``get_db()``, so a retry naturally gets a fresh schema snapshot.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except DatabaseNotInitializedError:
                return mcp_error("DB_NOT_INITIALIZED")
            except sqlite3.OperationalError as exc:
                if not is_transient_schema_error(exc):
                    logger.warning("[DATABASE_ERROR] %s", exc)
                    return mcp_error("DATABASE_ERROR", internal_message=str(exc))
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.info("Transient schema error (attempt %d/%d), retrying in %.2fs: %s",
                                attempt + 1, _MAX_RETRIES + 1, delay, exc)
                    time.sleep(delay)
        logger.warning("[DATABASE_ERROR] Retries exhausted: %s", last_exc)
        return mcp_error("DATABASE_ERROR", internal_message=str(last_exc))

    return wrapper
