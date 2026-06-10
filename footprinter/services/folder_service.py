"""Folder read service — get/list with role-based visibility filtering."""

import sqlite3
from typing import Optional

from footprinter.db import folders as db
from footprinter.services.access_service import (
    _read_visibility,
    filter_result,
    filter_results_list,
)
from footprinter.services.includes import status_arg_for_role
from footprinter.services.roles import Role


def get(conn: sqlite3.Connection, folder_id: int, *, role: Role = Role.ADMIN) -> dict | None:
    """Fetch a single folder by ID, filtered by role."""
    result = db.get_folder(conn, folder_id)
    if result is None:
        return None
    if role.sees_all:
        return result
    return filter_result("folder", result)


def assign(
    conn: sqlite3.Connection,
    folder_id: int,
    *,
    role: Role = Role.ADMIN,
    project_id: int | None = None,
    client_id: int | None = None,
) -> dict | None:
    """Assign a folder to a project and/or client.

    Returns result dict on success, None if not found.
    Raises PermissionError if role cannot write, ValueError if project doesn't exist.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")
    result = db.update_folder_relationships(
        conn,
        folder_id,
        project_id=project_id,
        client_id=client_id,
    )
    if result is None:
        return None
    resp: dict = {"id": folder_id}
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
    depth: Optional[int] = None,
    include_hidden: bool = False,
    status: Optional[str | list[str]] = None,
    sort_by: str = "size",
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List folders with pagination, filtered by role."""
    response = db.list_folders(
        conn,
        project_id=project_id,
        depth=depth,
        include_hidden=include_hidden,
        status=status,
        sort_by=sort_by,
        limit=limit,
        page=page,
    )
    if role.sees_all:
        return response
    filtered, suppressed = filter_results_list("folder", response["folders"])
    response["folders"] = filtered
    response["suppressed"] = suppressed
    return response


def get_by_path(
    conn: sqlite3.Connection,
    path: str,
    *,
    role: Role = Role.ADMIN,
    include_unlisted: bool = False,
    include_removed: bool = False,
) -> dict | None:
    """Look up a folder by exact path with navigation data, filtered by role.

    Returns None if folder doesn't exist or is hidden (for VIEWER).
    Returns opaque dict for opaque folders (for VIEWER) — fetches unlisted
    counts only, then strips to opaque-allowed fields.
    Returns full navigation dict for visible folders.

    ``include_unlisted`` / ``include_removed`` are ADMIN-only — VIEWER callers
    accept them but the listed-only default still applies.
    """
    row = db.get_folder_by_path(conn, path)
    if row is None:
        return None

    visibility = _read_visibility(row)

    if not role.sees_all:
        # Listing-status gate: an unlisted/removed folder must not be resolvable
        # by direct exact-path lookup, even when its visibility would otherwise
        # allow it. Runs before the visibility branch so opaque + unlisted
        # folders are fully suppressed rather than leaked as a stub.
        if (row.get("status") or "listed") != "listed":
            return None
        if visibility == "hidden":
            return None
        if visibility == "opaque":
            counts = db.get_unlisted_counts(conn, row["id"], path)
            row["unlisted_file_count"] = counts["unlisted_file_count"]
            row["unlisted_recursive_file_count"] = counts["unlisted_recursive_file_count"]
            return filter_result("folder", row)

    status_arg = status_arg_for_role(
        role,
        include_unlisted=include_unlisted,
        include_removed=include_removed,
    )

    # Fetch navigation data (files, subfolders, recursive count)
    nav = db.get_folder_navigation(conn, row["id"], path, status=status_arg)
    result = {**row, **nav}

    if role.sees_all:
        return result

    # Filter children by visibility
    result["files"], file_sup = filter_results_list("file", result["files"])
    result["subfolders"], sub_sup = filter_results_list("folder", result["subfolders"])
    total_sup = file_sup + sub_sup
    if total_sup:
        result["suppressed"] = total_sup
    return result
