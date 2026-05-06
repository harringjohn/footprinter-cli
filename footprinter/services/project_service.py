"""Project service — get/list with role-based visibility, upsert and soft delete."""

import sqlite3
from typing import Optional

from footprinter.db import projects as db
from footprinter.services.access_service import (
    _read_visibility,
    filter_result,
    filter_results_list,
)
from footprinter.services.includes import validate_include
from footprinter.services.roles import Role

VALID_INCLUDES = frozenset({"files", "folders"})


def get(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    role: Role = Role.ADMIN,
    include: list[str] | None = None,
) -> dict | None:
    """Fetch a single project by ID, filtered by role.

    Pass ``include`` to attach nested data:
    - ``"files"`` — paginated list of files in this project
    - ``"folders"`` — list of folders in this project
    """
    includes = validate_include(include, VALID_INCLUDES)
    result = db.get_project_detail(conn, project_id)
    if result is None:
        return None

    # Attach includes only when caller has full access to this entity
    is_full = role.sees_all or _read_visibility(result) == "visible"
    if is_full and includes:
        if "files" in includes:
            from footprinter.services import file_service

            resp = file_service.list_(conn, role=role, project_id=project_id)
            result["files"] = resp["files"]
        if "folders" in includes:
            from footprinter.services import folder_service

            resp = folder_service.list_(conn, role=role, project_id=project_id, depth=None)
            result["folders"] = resp["folders"]

    if role.sees_all:
        return result
    return filter_result("project", result)


def list_(
    conn: sqlite3.Connection,
    *,
    role: Role = Role.ADMIN,
    include: list[str] | None = None,
    status: Optional[str | list[str]] = None,
    client: Optional[str] = None,
    project_type: Optional[str] = None,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List projects with pagination, filtered by role."""
    includes = validate_include(include, VALID_INCLUDES)
    response = db.list_projects(
        conn,
        status=status,
        client=client,
        project_type=project_type,
        limit=limit,
        page=page,
    )

    # Track which items are fully visible before filtering strips fields
    visible_ids: set[int] = set()
    if includes and not role.sees_all:
        visible_ids = {p["id"] for p in response["projects"] if _read_visibility(p) == "visible"}

    if not role.sees_all:
        filtered, suppressed = filter_results_list("project", response["projects"])
        response["projects"] = filtered
        response["suppressed"] = suppressed

    if includes:
        for project in response["projects"]:
            if not role.sees_all and project["id"] not in visible_ids:
                continue
            if "files" in includes:
                from footprinter.services import file_service

                resp = file_service.list_(conn, role=role, project_id=project["id"])
                project["files"] = resp["files"]
            if "folders" in includes:
                from footprinter.services import folder_service

                resp = folder_service.list_(
                    conn,
                    role=role,
                    project_id=project["id"],
                    depth=None,
                )
                project["folders"] = resp["folders"]

    return response


def resolve_by_name(
    conn: sqlite3.Connection,
    name: str,
    *,
    role: Role = Role.ADMIN,
) -> dict | None:
    """Resolve a project by fuzzy name match, with navigation data.

    Returns:
        Full navigation dict for single match.
        Disambiguation dict for multiple ambiguous matches.
        None for no match (or hidden-only matches for VIEWER).
    """
    rows = db.find_by_name_fuzzy(conn, name)
    if not rows:
        return None

    # Filter hidden for VIEWER
    if not role.sees_all:
        rows = [r for r in rows if _read_visibility(r) != "hidden"]
    if not rows:
        return None

    if len(rows) == 1:
        return _build_project_navigation(conn, rows[0], role=role)

    # Check exact match (case-insensitive)
    exact = [r for r in rows if r["project_name"].lower() == name.lower()]
    if len(exact) == 1:
        return _build_project_navigation(conn, exact[0], role=role)

    # Disambiguation
    return _build_disambiguation(rows, "project_name", name, role)


def _build_project_navigation(conn: sqlite3.Connection, row: dict, *, role: Role) -> dict:
    """Build full project navigation dict from a project row."""
    visibility = _read_visibility(row)
    if not role.sees_all and visibility == "opaque":
        return filter_result("project", row)

    nav = db.get_project_navigation(conn, row["id"])
    result = {**row, **nav}

    if role.sees_all:
        return result

    # Filter child folders by visibility
    result["folders"], _ = filter_results_list("folder", result["folders"])
    return result


def _build_disambiguation(rows: list[dict], name_col: str, query: str, role: Role) -> dict:
    """Build a disambiguation dict from multiple matches."""
    from footprinter.services.access_service import resolve_inherit_visibility

    matches = []
    for r in rows:
        vis = resolve_inherit_visibility(r.get("mcp_view"))
        if vis == "opaque":
            matches.append({"id": r["id"], "visibility": "restricted"})
        else:
            matches.append({"id": r["id"], "name": r[name_col]})
    return {
        "disambiguation": True,
        "message": f"Multiple matches for '{query}'. Please be more specific.",
        "matches": matches,
    }


def upsert(
    conn: sqlite3.Connection,
    *,
    project_name: str,
    role: Role = Role.ADMIN,
    root_path: Optional[str] = None,
    client_id: Optional[int] = None,
    project_type: Optional[str] = None,
    description: Optional[str] = None,
    github_url: Optional[str] = None,
    status: Optional[str] = None,
    status_reason: Optional[str] = None,
) -> dict:
    """Insert or update a project. Matches on root_path first, then project_name.

    Returns dict with ``id`` and ``action`` ("created"|"updated").
    Raises PermissionError if role cannot write, ValueError on bad input.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")

    project_name = (project_name or "").strip()
    if not project_name:
        raise ValueError("project_name is required")

    existing_id = db.find_project_id_by_key(
        conn,
        root_path=root_path,
        project_name=project_name,
    )

    if existing_id is None:
        result = db.create_project(
            conn,
            project_name=project_name,
            root_path=root_path,
            client_id=client_id,
            project_type=project_type,
            description=description,
            github_url=github_url,
            status=status or "listed",
        )
        return {"id": result["id"], "action": "created"}

    update_fields: dict = {}
    if project_type is not None:
        update_fields["project_type"] = project_type
    if root_path is not None:
        update_fields["root_path"] = root_path
    if description is not None:
        update_fields["description"] = description
    if github_url is not None:
        update_fields["github_url"] = github_url
    if client_id is not None:
        update_fields["client_id"] = client_id
    if status is not None:
        update_fields["status"] = status
        if status_reason is not None:
            update_fields["status_reason"] = status_reason
    # Always update name — desired-state semantics
    update_fields["project_name"] = project_name
    db.update_project(conn, existing_id, **update_fields)
    return {"id": existing_id, "action": "updated"}


def delete(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    role: Role = Role.ADMIN,
) -> dict | None:
    """Hard-delete a project row.

    Returns ``{"id", "deleted": True}`` on success, ``None`` if not found.
    Raises ``ValueError`` (with a per-table dependent count summary) when the
    project has dependent records — callers must reassign or remove children
    first. Soft-delete is available via ``fp upsert --status removed``.
    Raises ``PermissionError`` if role cannot write.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")

    result = db.delete_project(conn, project_id)
    if result is None:
        return None
    if result.get("blocked"):
        deps = result["dependents"]
        summary = ", ".join(f"{n} {table}" for table, n in deps.items() if n > 0)
        raise ValueError(
            f"project {project_id} has dependents ({summary}); reassign or delete them first"
        )
    return {"id": project_id, "deleted": True}
