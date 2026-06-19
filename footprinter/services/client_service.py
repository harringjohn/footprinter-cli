"""Client service — get/list with role-based visibility, upsert and soft delete."""

import sqlite3
from typing import Optional

from footprinter.db import clients as db
from footprinter.services.access_service import (
    _read_visibility,
    attach_curated_context,
    filter_result,
    filter_results_list,
)
from footprinter.services.includes import validate_include
from footprinter.services.roles import Role

VALID_INCLUDES = frozenset({"projects", "aggregates"})


def _project_status_for_role(role: Role) -> Optional[str]:
    """Listing-status filter for a client's project enumeration.

    VIEWER (non-``sees_all``) navigation enumerates ``listed`` projects only;
    unlisted ones are surfaced as an aggregate count instead. ADMIN keeps the
    default (``None`` → listed + unlisted, removed excluded).

    Note: this is deliberately *not* ``status_arg_for_role`` — that helper
    returns ``None`` for VIEWER, which on the projects table means listed +
    unlisted (projects default to ``default_exclude=["removed"]``). An explicit
    ``"listed"`` is the targeted fix for that status-vs-visibility gap.
    """
    return None if role.sees_all else "listed"


def _get_client_aggregates(client_name: str, conn: sqlite3.Connection, *, role: Role) -> dict:
    """Compute per-project file counts for a client, respecting visibility.

    Derives aggregates from the role-filtered project list rather than raw SQL,
    so hidden/opaque projects are excluded for non-admin roles, and (via
    ``_project_status_for_role``) unlisted projects are excluded for VIEWER.
    """
    from footprinter.services import project_service

    resp = project_service.list_(
        conn, role=role, client=client_name, status=_project_status_for_role(role)
    )
    per_project = [
        {
            "project_id": p["id"],
            "project_name": p["name"],
            "file_count": p.get("file_count", 0),
        }
        for p in resp["projects"]
        if "name" in p  # Exclude opaque projects (minimal dicts lack "name")
    ]
    return {
        "project_count": len(per_project),
        "file_count": sum(p["file_count"] for p in per_project),
        "per_project": per_project,
    }


def get(
    conn: sqlite3.Connection,
    client_id: int,
    *,
    role: Role = Role.ADMIN,
    include: list[str] | None = None,
) -> dict | None:
    """Fetch a single client by ID, filtered by role.

    Pass ``include`` to attach nested data:
    - ``"projects"`` — list of projects belonging to this client
    - ``"aggregates"`` — file counts per project
    """
    includes = validate_include(include, VALID_INCLUDES)
    result = db.get_client(conn, client_id)
    if result is None:
        return None

    # Strip nested data that db layer embeds by default
    result.pop("projects", None)
    result.pop("file_count", None)

    # Attach includes only when caller has full access to this entity
    is_full = role.sees_all or _read_visibility(result) == "full"
    if is_full and includes:
        if "projects" in includes:
            from footprinter.services import project_service

            resp = project_service.list_(
                conn, role=role, client=result["name"], status=_project_status_for_role(role)
            )
            result["projects"] = resp["projects"]
        if "aggregates" in includes:
            result["aggregates"] = _get_client_aggregates(
                result["name"],
                conn,
                role=role,
            )

    if role.sees_all:
        return result
    return filter_result("client", result)


def list_(
    conn: sqlite3.Connection,
    *,
    role: Role = Role.ADMIN,
    include: list[str] | None = None,
    status: Optional[str | list[str]] = None,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List clients with pagination, filtered by role."""
    includes = validate_include(include, VALID_INCLUDES)
    response = db.list_clients(conn, status=status, limit=limit, page=page)

    # Track which items are fully visible before filtering strips fields
    visible_ids: set[int] = set()
    if includes and not role.sees_all:
        visible_ids = {c["id"] for c in response["clients"] if _read_visibility(c) == "full"}

    if not role.sees_all:
        filtered, suppressed = filter_results_list("client", response["clients"])
        response["clients"] = filtered
        response["suppressed"] = suppressed

    if includes:
        for client in response["clients"]:
            # Only attach to fully-visible items (admin sees all)
            if not role.sees_all and client["id"] not in visible_ids:
                continue
            if "projects" in includes:
                from footprinter.services import project_service

                resp = project_service.list_(
                    conn, role=role, client=client["name"], status=_project_status_for_role(role)
                )
                client["projects"] = resp["projects"]
            if "aggregates" in includes:
                client["aggregates"] = _get_client_aggregates(
                    client["name"],
                    conn,
                    role=role,
                )

    return response


def resolve_by_name(
    conn: sqlite3.Connection,
    name: str,
    *,
    role: Role = Role.ADMIN,
) -> dict | None:
    """Resolve a client by fuzzy name match, with navigation data.

    Returns full client dict with projects and aggregates for single match,
    disambiguation dict for multiple ambiguous matches, or None. For VIEWER the
    projects list is listed-only and the dict carries ``unlisted_project_count``.
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
        return _build_client_navigation(conn, rows[0], role=role)

    # Check exact match
    exact = [r for r in rows if r["name"].lower() == name.lower()]
    if len(exact) == 1:
        return _build_client_navigation(conn, exact[0], role=role)

    # Disambiguation
    from footprinter.services.access_service import resolve_inherit_visibility

    matches = []
    for r in rows:
        vis = resolve_inherit_visibility(r.get("visibility"))
        if vis == "opaque":
            matches.append({"id": r["id"], "visibility": "restricted"})
        else:
            matches.append({"id": r["id"], "name": r["name"]})
    return {
        "disambiguation": True,
        "message": f"Multiple matches for '{name}'. Please be more specific.",
        "matches": matches,
    }


def _build_client_navigation(conn: sqlite3.Connection, row: dict, *, role: Role) -> dict:
    """Build full client navigation dict from a client row."""
    visibility = _read_visibility(row)
    if not role.sees_all and visibility == "opaque":
        return filter_result("client", row)

    # Get projects for this client (hidden filtered; VIEWER sees listed-only)
    from footprinter.services import project_service

    proj_resp = project_service.list_(
        conn, role=role, client=row["name"], status=_project_status_for_role(role)
    )
    projects = proj_resp["projects"]

    result = {**row}
    result["projects"] = projects
    attach_curated_context(result, "client")

    # VIEWER: unlisted projects are collapsed to a count, not enumerated.
    # Computed unconditionally — a client with only unlisted projects has no
    # listed project_ids, so the count must not depend on the nav query below.
    if not role.sees_all:
        result["unlisted_project_count"] = db.count_unlisted_projects(conn, row["id"])

    # Aggregate stats across all projects. VIEWER counts listed-only folders so
    # total_folders matches the listed-only folder list in project navigation.
    project_ids = [p["id"] for p in projects if "id" in p]
    nav = db.get_client_navigation(conn, row["id"], project_ids, listed_only=not role.sees_all)
    result.update(nav)

    return result


def upsert(
    conn: sqlite3.Connection,
    *,
    name: str,
    client_type: str,
    role: Role = Role.ADMIN,
    status: Optional[str] = None,
    status_reason: Optional[str] = None,
) -> dict:
    """Insert or update a client by name.

    Matches on ``name`` (UNIQUE constraint). Returns dict with ``id``,
    ``action`` ("created"|"updated"), and ``slug`` on create.
    Raises PermissionError if role cannot write, ValueError on bad input.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")

    name = (name or "").strip()
    if not name:
        raise ValueError("Name cannot be empty")

    existing_id = db.find_client_id_by_name(conn, name)

    if existing_id is None:
        result = db.create_client(
            conn,
            name=name,
            client_type=client_type,
        )
        new_id = result["id"]
        # Apply optional fields that create_client doesn't accept
        post_update: dict = {}
        if status is not None:
            post_update["status"] = status
        if post_update:
            db.update_client(conn, new_id, **post_update)
        return {"id": new_id, "slug": result["slug"], "action": "created"}

    update_fields: dict = {"client_type": client_type}
    if status is not None:
        update_fields["status"] = status
        if status_reason is not None:
            update_fields["status_reason"] = status_reason
    db.update_client(conn, existing_id, **update_fields)
    return {"id": existing_id, "action": "updated"}


def delete(
    conn: sqlite3.Connection,
    client_id: int,
    *,
    role: Role = Role.ADMIN,
) -> dict | None:
    """Hard-delete a client row.

    Returns ``{"id", "deleted": True}`` on success, ``None`` if not found.
    Raises ``ValueError`` (with a per-table dependent count summary) when the
    client has dependent records — callers must reassign or remove children
    first. Soft-delete is available via ``fp update client <id> --status removed``.
    Raises ``PermissionError`` if role cannot write.
    """
    if not role.can_write:
        raise PermissionError("Role does not permit write operations")

    result = db.delete_client(conn, client_id)
    if result is None:
        return None
    if result.get("blocked"):
        deps = result["dependents"]
        summary = ", ".join(f"{n} {table}" for table, n in deps.items() if n > 0)
        raise ValueError(
            f"client {client_id} has dependents ({summary}); reassign or delete them first"
        )
    return {"id": client_id, "deleted": True}
