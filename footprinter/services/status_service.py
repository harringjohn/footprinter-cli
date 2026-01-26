"""Status service — visibility-aware system status aggregates."""

import sqlite3

from footprinter.db import status as db_status
from footprinter.paths import get_config_path
from footprinter.services.roles import Role


def get_status(conn: sqlite3.Connection, *, role: Role = Role.ADMIN) -> dict:
    """Return system status, filtered by role.

    VIEWER gets MCP-oriented counts with hidden-client data excluded.
    ADMIN gets the full system status including config presence checks.
    """
    if role == Role.VIEWER:
        return db_status.get_mcp_status(conn)
    return db_status.get_system_status(conn, get_config_path())
