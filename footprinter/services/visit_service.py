"""Visit (browser history) read service — get/list with role-based visibility filtering."""

import sqlite3
from typing import Optional

from footprinter.db import browser as db
from footprinter.services.access_service import filter_result, filter_results_list
from footprinter.services.roles import Role


def get(conn: sqlite3.Connection, entry_id: int, *, role: Role = Role.ADMIN) -> dict | None:
    """Fetch a single browser visit by ID, filtered by role."""
    result = db.get_visit(conn, entry_id)
    if result is None:
        return None
    if role.sees_all:
        return result
    return filter_result("visit", result)


def assign(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    role: Role = Role.ADMIN,
    project_id: int | None = None,
    client_id: int | None = None,
) -> dict | None:
    """Assign a visit to a project and/or client.

    Returns result dict on success, None if not found.
    Raises PermissionError if role cannot write.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")
    result = db.update_visit_relationships(
        conn,
        entry_id,
        project_id=project_id,
        client_id=client_id,
    )
    if result is None:
        return None
    resp: dict = {"id": entry_id}
    if project_id is not None:
        resp["project_id"] = project_id
    if client_id is not None:
        resp["client_id"] = client_id
    return resp


def list_(
    conn: sqlite3.Connection,
    *,
    role: Role = Role.ADMIN,
    project_id: Optional[int] = None,
    client_id: Optional[int] = None,
    limit: int = 50,
    page: int = 1,
    status: Optional[str | list[str]] = None,
) -> dict:
    """List browser visits with pagination, filtered by role."""
    response = db.list_visits(
        conn, project_id=project_id, client_id=client_id, limit=limit, page=page, status=status
    )
    if role.sees_all:
        return response
    filtered, suppressed = filter_results_list("visit", response["visits"])
    response["visits"] = filtered
    response["suppressed"] = suppressed
    return response
