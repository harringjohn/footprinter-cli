"""File read service — get/list with role-based visibility filtering."""

import sqlite3
from typing import Optional

from footprinter.db import files as db
from footprinter.services.access_service import (
    filter_result,
    filter_results_list,
    strip_content_for_denied,
)
from footprinter.services.roles import Role


def get(conn: sqlite3.Connection, file_id: int, *, role: Role = Role.ADMIN) -> dict | None:
    """Fetch a single file by ID, filtered by role."""
    result = db.get_file(conn, file_id)
    if result is None:
        return None
    if role.sees_all:
        return result
    return filter_result("file", result)


def assign(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    role: Role = Role.ADMIN,
    project_id: int | None = None,
    client_id: int | None = None,
) -> dict | None:
    """Assign a file to a project and/or client.

    Returns result dict on success, None if not found.
    Raises PermissionError if role cannot write.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")
    result = db.update_file_relationships(
        conn,
        file_id,
        project_id=project_id,
        client_id=client_id,
    )
    if result is None:
        return None
    resp: dict = {"id": file_id}
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
    source: Optional[list[str]] = None,
    status: Optional[str | list[str]] = None,
    content_type: Optional[str] = None,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List files with pagination, filtered by role."""
    response = db.list_files(
        conn,
        project_id=project_id,
        source=source,
        status=status,
        content_type=content_type,
        limit=limit,
        page=page,
    )
    if role.sees_all:
        return response
    filtered, suppressed = filter_results_list("file", response["files"])
    filtered = strip_content_for_denied("file", filtered)
    response["files"] = filtered
    response["suppressed"] = suppressed
    return response
