"""Chat read service — get/list with role-based visibility filtering."""

import sqlite3
from typing import Optional

from footprinter.db import chats as db
from footprinter.services.access_service import (
    filter_result,
    filter_results_list,
    strip_content_for_denied,
)
from footprinter.services.roles import Role


def get(conn: sqlite3.Connection, chat_id: int, *, role: Role = Role.ADMIN) -> dict | None:
    """Fetch a single chat with messages by ID, filtered by role."""
    result = db.get_chat_detail(conn, chat_id)
    if result is None:
        return None
    if role.sees_all:
        return result
    return filter_result("chat", result)


def assign(
    conn: sqlite3.Connection,
    chat_id: int,
    *,
    role: Role = Role.ADMIN,
    project_id: int | None = None,
    client_id: int | None = None,
) -> dict | None:
    """Assign a chat to a project and/or client.

    Returns result dict on success, None if not found.
    Raises PermissionError if role cannot write.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")
    result = db.update_chat_relationships(
        conn,
        chat_id,
        project_id=project_id,
        client_id=client_id,
    )
    if result is None:
        return None
    resp: dict = {"id": chat_id}
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
    query: Optional[str] = None,
    sort_by: str = "modified_at",
    order: str = "desc",
    status: Optional[str | list[str]] = None,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List chats with pagination, filtered by role."""
    response = db.list_chats(
        conn,
        account=account,
        query=query,
        sort_by=sort_by,
        order=order,
        status=status,
        limit=limit,
        page=page,
    )
    if role.sees_all:
        return response
    filtered, suppressed = filter_results_list("chat", response["chats"])
    filtered = strip_content_for_denied("chat", filtered)
    response["chats"] = filtered
    response["suppressed"] = suppressed
    return response
