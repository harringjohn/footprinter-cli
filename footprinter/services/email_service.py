"""Email read service — get/list with role-based visibility filtering."""

import sqlite3
from typing import Optional

from footprinter.db import emails as db
from footprinter.services.access_service import (
    filter_result,
    filter_results_list,
    strip_content_for_denied,
)
from footprinter.services.roles import Role


def get(conn: sqlite3.Connection, email_id: int, *, role: Role = Role.ADMIN) -> dict | None:
    """Fetch a single email by ID, filtered by role."""
    result = db.get_email(conn, email_id)
    if result is None:
        return None
    if role.sees_all:
        return result
    return filter_result("email", result)


def assign(
    conn: sqlite3.Connection,
    email_id: int,
    *,
    role: Role = Role.ADMIN,
    project_id: int | None = None,
    client_id: int | None = None,
) -> dict | None:
    """Assign an email to a project and/or client.

    Returns result dict on success, None if not found.
    Raises PermissionError if role cannot write.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")
    result = db.update_email_relationships(
        conn,
        email_id,
        project_id=project_id,
        client_id=client_id,
    )
    if result is None:
        return None
    resp: dict = {"id": email_id}
    if project_id is not None:
        resp["project_id"] = project_id
    if client_id is not None:
        resp["client_id"] = client_id
    return resp


def list_(
    conn: sqlite3.Connection,
    *,
    role: Role = Role.ADMIN,
    account: Optional[str] = None,
    client_id: Optional[int] = None,
    project_id: Optional[int] = None,
    query: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    status: Optional[str | list[str]] = None,
    sort_by: str = "received_at",
    order: str = "desc",
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List emails with pagination, filtered by role."""
    response = db.list_emails(
        conn,
        account=account,
        client_id=client_id,
        project_id=project_id,
        query=query,
        has_attachments=has_attachments,
        status=status,
        sort_by=sort_by,
        order=order,
        limit=limit,
        page=page,
    )
    if role.sees_all:
        return response
    filtered, suppressed = filter_results_list("email", response["emails"])
    filtered = strip_content_for_denied("email", filtered)
    response["emails"] = filtered
    response["suppressed"] = suppressed
    return response
