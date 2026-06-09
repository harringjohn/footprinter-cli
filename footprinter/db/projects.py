"""Project listing, file queries, and CRUD operations.

Public API for project data — no restricted dependencies
(permissions, visibility, source_registry).
"""

import sqlite3
from typing import Optional

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response
from footprinter.utils.text import _make_slug

VALID_STATUSES = frozenset({"listed", "unlisted", "removed"})


# ---------------------------------------------------------------------------
# Shared helpers (used by app_projects.py)
# ---------------------------------------------------------------------------


def fetch_project(conn: sqlite3.Connection, project_id: int):
    """Return a project row or None."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    return cursor.fetchone()


def resolve_client_name(conn: sqlite3.Connection, client_id: int) -> Optional[str]:
    """Look up client name by id. Returns name or None if not found."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    return row["name"] if row else None


def find_project_id_by_key(
    conn: sqlite3.Connection,
    *,
    name: Optional[str] = None,
) -> Optional[int]:
    """Find a project ID by name (first match).

    Project names are not unique, so this returns the first matching row.
    Returns the project ID or None.
    """
    if not name:
        return None
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE name = ?", (name,))
    row = cursor.fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Name resolution and navigation (used by MCP tools via service layer)
# ---------------------------------------------------------------------------


def find_by_name_fuzzy(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Find projects matching name with LIKE %name%.

    Returns all columns including visibility. Does NOT filter by visibility
    — the service layer handles that.
    """
    rows = conn.execute(
        """SELECT id, name, status, client,
                  description, visibility, access,
                  visibility_source, access_source
           FROM projects
           WHERE name LIKE ?""",
        (f"%{name}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def count_hidden_by_name(conn: sqlite3.Connection, name: str) -> int:
    """Count hidden projects matching a fuzzy name query (for diagnostics)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE name LIKE ? AND COALESCE(visibility, 'inherit') = 'hidden'",
        (f"%{name}%",),
    ).fetchone()
    return row[0]


def get_project_navigation(conn: sqlite3.Connection, project_id: int) -> dict:
    """Return navigation aggregates for an MCP project view.

    Includes file stats, content type breakdown, folders, and entity counts.
    Folder rows include visibility and status for service-layer filtering.
    """
    _not_hidden = "AND COALESCE(visibility, 'inherit') != 'hidden'"

    # File stats (removed excluded)
    stats = conn.execute(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(size_bytes), 0) as size,
                  SUM(CASE WHEN source = 'local' THEN 1 ELSE 0 END) as local_count,
                  SUM(CASE WHEN source != 'local' THEN 1 ELSE 0 END) as drive_count
           FROM files
           WHERE project_id = ? AND status != 'removed' {_not_hidden}""",
        (project_id,),
    ).fetchone()

    # Top content types (removed excluded)
    types = conn.execute(
        f"""SELECT content_type, COUNT(*) as count
           FROM files
           WHERE project_id = ? AND status != 'removed' AND content_type IS NOT NULL
                 {_not_hidden}
           GROUP BY content_type ORDER BY count DESC LIMIT 10""",
        (project_id,),
    ).fetchall()

    # Folders (include all — service layer filters by visibility and status)
    folders = conn.execute(
        """SELECT id, path, name, direct_file_count, total_size_bytes, source,
                  status, visibility, access,
                  visibility_source, access_source
           FROM folders
           WHERE project_id = ?
           ORDER BY path""",
        (project_id,),
    ).fetchall()

    # Entity counts (removed excluded)
    email_count = conn.execute(
        f"SELECT COUNT(*) FROM emails WHERE project_id = ? AND status != 'removed' {_not_hidden}",
        (project_id,),
    ).fetchone()[0]
    chat_count = conn.execute(
        f"SELECT COUNT(*) FROM chats WHERE project_id = ? AND status != 'removed' {_not_hidden}",
        (project_id,),
    ).fetchone()[0]
    browser_count = conn.execute(
        f"SELECT COUNT(*) FROM visits WHERE project_id = ? AND status != 'removed' {_not_hidden}",
        (project_id,),
    ).fetchone()[0]

    return {
        "file_count": stats["count"],
        "file_size_bytes": stats["size"],
        "local_count": stats["local_count"] or 0,
        "drive_count": stats["drive_count"] or 0,
        "top_content_types": {r["content_type"]: r["count"] for r in types},
        "folders": [dict(f) for f in folders],
        "entity_counts": {
            "emails": email_count,
            "chats": chat_count,
            "visits": browser_count,
        },
    }


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def list_projects(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    limit: int = 50,
    status: Optional[str | list[str]] = None,
    client: Optional[str] = None,
) -> dict:
    """List projects with file counts, pagination, and SQL-side filtering.

    Parameters
    ----------
    conn : sqlite3.Connection
    page, limit : int
        Pagination.
    status : str, list[str], or None
        Filter by status value(s). ``None`` → exclude removed (default).
        ``"all"`` → no status filter.
    client : str or None
        Filter by client name (exact match).

    Returns
    -------
    dict with keys: projects, pagination, clients,
                    no_project_count, no_project_size_bytes
    """
    cursor = conn.cursor()

    # Build dynamic WHERE clause
    conditions: list[str] = []
    params: list = []

    status_conds, status_params = build_status_filter(
        status,
        column="project.status",
        default_exclude=["removed"],
    )
    conditions.extend(status_conds)
    params.extend(status_params)

    if client is not None:
        conditions.append("client.name = ?")
        params.append(client)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    # The count query needs the same JOIN as the fetch for client filtering
    count_sql = f"""
        SELECT COUNT(*) FROM projects project
        LEFT JOIN clients client ON project.client_id = client.id
        {where}
    """
    fetch_sql = f"""
        SELECT project.id, project.name,
               project.status, client.name AS client, project.description,
               project.visibility, project.access,
               project.visibility_source, project.access_source,
               (SELECT COUNT(*) FROM folders folder
                   WHERE folder.project_id = project.id) as folder_count
        FROM projects project
        LEFT JOIN clients client ON project.client_id = client.id
        {where}
        ORDER BY project.name
        LIMIT ? OFFSET ?
    """
    project_rows, pagination = paginate(
        conn,
        count_sql,
        fetch_sql,
        params,
        page=page,
        limit=limit,
    )

    # Batch query: file stats per project
    cursor.execute(
        """
        SELECT project_id, COUNT(*) as count, COALESCE(SUM(size_bytes), 0) as size
        FROM files WHERE status != 'removed'
        GROUP BY project_id
        """
    )
    stats_by_project = {r["project_id"]: {"count": r["count"], "size": r["size"]} for r in cursor.fetchall()}

    projects = []
    for row in project_rows:
        project_id = row["id"]
        stats = stats_by_project.get(project_id, {"count": 0, "size": 0})

        projects.append(
            {
                "id": project_id,
                "name": row["name"],
                "client": row["client"] or "",
                "status": row["status"] or "listed",
                "description": row["description"] or "",
                "file_count": stats["count"],
                "size_bytes": stats["size"],
                "folder_count": row["folder_count"] or 0,
                "visibility": row["visibility"] or "inherit",
                "access": row["access"] or "inherit",
                "visibility_source": row["visibility_source"],
                "access_source": row["access_source"],
            }
        )

    # Extra: distinct client names across ALL projects (for filter dropdowns)
    cursor.execute("""
        SELECT DISTINCT client.name FROM clients client
        INNER JOIN projects project ON project.client_id = client.id
        ORDER BY client.name
    """)
    clients = [r["name"] for r in cursor.fetchall()]

    # Count files with no project (excluding removed)
    cursor.execute(
        """
        SELECT COUNT(*) as count, COALESCE(SUM(size_bytes), 0) as size
        FROM files
        WHERE project_id IS NULL AND status != 'removed'
        """
    )
    no_project_stats = cursor.fetchone()

    return paginated_response(
        "projects",
        projects,
        pagination,
        clients=clients,
        no_project_count=no_project_stats["count"],
        no_project_size_bytes=no_project_stats["size"],
    )


def get_project_detail(conn: sqlite3.Connection, project_id: int) -> Optional[dict]:
    """Return enriched project dict.

    Adds client name, file/folder counts, and total size on top of
    the raw ``projects`` row.  Returns ``None`` if the project doesn't exist.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT project.id, project.name, project.description,
               project.status,
               project.client_id, project.client,
               project.access, project.visibility,
               project.visibility_source, project.access_source,
               project.created_at, project.updated_at,
               client.name AS client_name,
               (SELECT COUNT(*) FROM files file
                WHERE file.project_id = project.id AND file.status != 'removed') AS file_count,
               (SELECT COALESCE(SUM(file.size_bytes), 0) FROM files file
                WHERE file.project_id = project.id AND file.status != 'removed') AS total_size,
               (SELECT COUNT(*) FROM folders folder
                WHERE folder.project_id = project.id) AS folder_count
        FROM projects project
        LEFT JOIN clients client ON project.client_id = client.id
        WHERE project.id = ?
        """,
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    result = {
        "id": row["id"],
        "name": row["name"],
        "client": row["client_name"] or row["client"] or "",
        "status": row["status"] or "listed",
        "description": row["description"] or "",
        "file_count": row["file_count"],
        "total_size": row["total_size"],
        "folder_count": row["folder_count"],
        "visibility": row["visibility"] or "inherit",
        "access": row["access"] or "inherit",
        "visibility_source": row["visibility_source"],
        "access_source": row["access_source"],
    }
    return result


def list_project_files(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    sort: str = "modified_at",
    order: str = "desc",
    page: int = 1,
    limit: int = 50,
) -> Optional[dict]:
    """List files for a specific project.

    Lightweight version — no SourceRegistry or permission resolution.

    Parameters
    ----------
    conn : sqlite3.Connection
    project_id : int
    sort, order, page, limit : standard pagination/sort params

    Returns
    -------
    dict | None
        None if project not found.
        Otherwise dict with keys: project, files, pagination
    """
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, status, description, client
        FROM projects WHERE id = ?
        """,
        (project_id,),
    )
    project = cursor.fetchone()
    if not project:
        return None

    order_sql = "DESC" if order == "desc" else "ASC"
    sort_col = sort if sort in ("modified_at", "name", "size_bytes", "content_type") else "modified_at"

    count_sql = "SELECT COUNT(*) FROM files WHERE project_id = ? AND status = 'listed'"
    fetch_sql = f"""
        SELECT id, source, account, name, path, content_type, size_bytes,
               modified_at, status, status_reason
        FROM files
        WHERE project_id = ? AND status = 'listed'
        ORDER BY {sort_col} {order_sql}
        LIMIT ? OFFSET ?
    """
    rows, pagination = paginate(conn, count_sql, fetch_sql, (project_id,), page=page, limit=limit)

    files = []
    for row in rows:
        files.append(
            {
                "id": row["id"],
                "name": row["name"],
                "content_type": row["content_type"] or "",
                "path": row["path"] or "",
                "size_bytes": row["size_bytes"],
                "modified_at": row["modified_at"] or "",
                "source": row["source"],
                "account": row["account"] or "",
                "status": row["status"] or "listed",
                "status_reason": row["status_reason"] or "",
            }
        )

    project_dict = {
        "id": project["id"],
        "name": project["name"],
        "status": project["status"] or "listed",
        "description": project["description"] or "",
        "client": project["client"] or "",
    }
    return paginated_response("files", files, pagination, project=project_dict)


# ---------------------------------------------------------------------------
# CRUD functions
# ---------------------------------------------------------------------------


def create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    client_id: Optional[int] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    """Create a new project.

    A ``slug`` is derived from ``name`` via ``_make_slug``. Project names are
    not unique, so the slug is non-unique too (no UNIQUE constraint).

    ``status`` is included in the INSERT only when the caller passes a value;
    otherwise the schema DEFAULT ('listed') applies. The column list is built
    dynamically so the schema stays the single source of truth.

    Returns a dict of the full project row.
    Raises ValueError on invalid input.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")

    cursor = conn.cursor()

    # Resolve client name
    client_name = None
    if client_id is not None:
        client_name = resolve_client_name(conn, client_id)
        if client_name is None:
            raise ValueError("Client not found")

    columns = ["name", "slug", "client_id", "client", "description"]
    values: list = [name, _make_slug(name), client_id, client_name, description]
    if status is not None:
        columns.append("status")
        values.append(status)

    placeholders = ", ".join(["?"] * len(values))
    cursor.execute(
        f"INSERT INTO projects ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    new_id = cursor.lastrowid

    cursor.execute("SELECT * FROM projects WHERE id = ?", (new_id,))
    return dict(cursor.fetchone())


def update_project(conn: sqlite3.Connection, project_id: int, **fields) -> Optional[bool]:
    """Update a project's fields.

    Stamps ``updated_at`` on any change and ``status_changed_at`` on a status
    change. Regenerates ``slug`` from ``name`` whenever the name changes
    (names are non-unique, so slugs are too).

    Returns True on success, None if not found.
    Raises ValueError on invalid input.
    """
    if not fetch_project(conn, project_id):
        return None

    cursor = conn.cursor()
    updatable = {"name", "slug", "description", "status", "status_reason"}
    sql_fields: list[str] = []
    values: list = []

    # Special handling for client_id: sync denormalized client name
    if "client_id" in fields:
        client_id = fields["client_id"]
        if client_id is not None:
            client_name = resolve_client_name(conn, client_id)
            if client_name is None:
                raise ValueError("Client not found")
            sql_fields.append("client_id = ?")
            values.append(client_id)
            sql_fields.append("client = ?")
            values.append(client_name)
        else:
            sql_fields.append("client_id = ?")
            values.append(None)
            sql_fields.append("client = ?")
            values.append(None)

    new_name = None
    for key in updatable:
        if key in fields:
            val = fields[key]
            if key == "name":
                new_name = (val or "").strip()
                if not new_name:
                    raise ValueError("Name cannot be empty")
                val = new_name
            sql_fields.append(f"{key} = ?")
            values.append(val)

    # Regenerate slug on name change unless an explicit slug was supplied
    if new_name and "slug" not in fields:
        sql_fields.append("slug = ?")
        values.append(_make_slug(new_name))

    # Stamp status_changed_at on a status change (mirrors folders/files)
    if "status" in fields:
        sql_fields.append("status_changed_at = CURRENT_TIMESTAMP")

    if not sql_fields:
        return True

    sql_fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(project_id)
    cursor.execute(
        f"UPDATE projects SET {', '.join(sql_fields)} WHERE id = ?",
        values,
    )
    conn.commit()
    return True


PROJECT_DEPENDENT_TABLES = ("files", "folders", "chats", "emails", "visits")


def count_project_dependents(conn: sqlite3.Connection, project_id: int) -> dict[str, int]:
    """Return per-table counts of records with ``project_id = ?``.

    Tables included: ``PROJECT_DEPENDENT_TABLES``. Zero counts are kept so
    callers can render "0 files, 2 folders" verbatim.
    """
    counts: dict[str, int] = {}
    for table in PROJECT_DEPENDENT_TABLES:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        counts[table] = row[0] if row else 0
    return counts


def delete_project(conn: sqlite3.Connection, project_id: int) -> Optional[dict]:
    """Hard-delete a project row.

    Returns ``None`` if the project does not exist. If any dependent records
    point at the project, returns ``{"blocked": True, "dependents": {...}}``
    without deleting. Otherwise issues ``DELETE FROM projects`` and returns
    ``{"deleted": True}``.
    """
    if not fetch_project(conn, project_id):
        return None

    counts = count_project_dependents(conn, project_id)
    if any(c > 0 for c in counts.values()):
        return {"blocked": True, "dependents": counts}

    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    return {"deleted": True}


def link_files(conn: sqlite3.Connection, project_id: int, file_ids: list[int]) -> Optional[dict]:
    """Link files to a project.

    Returns dict with linked count, or None if project not found.
    Skips removed files.
    """
    if not fetch_project(conn, project_id):
        return None

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(file_ids))
    cursor.execute(
        f"UPDATE files SET project_id = ? WHERE id IN ({placeholders}) AND status = 'listed'",
        [project_id] + list(file_ids),
    )
    conn.commit()
    return {"linked": cursor.rowcount}


def unlink_files(conn: sqlite3.Connection, project_id: int, file_ids: list[int]) -> Optional[dict]:
    """Unlink files from a project.

    Returns dict with unlinked count, or None if project not found.
    Only unlinks files that belong to this project.
    """
    if not fetch_project(conn, project_id):
        return None

    cursor = conn.cursor()
    placeholders = ",".join("?" * len(file_ids))
    cursor.execute(
        f"UPDATE files SET project_id = NULL WHERE id IN ({placeholders}) AND project_id = ?",
        list(file_ids) + [project_id],
    )
    conn.commit()
    return {"unlinked": cursor.rowcount}
