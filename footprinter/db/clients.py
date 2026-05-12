"""Client queries.

Provides list, detail, create, and update functions for clients.
"""

import json
import sqlite3
from typing import Optional

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response
from footprinter.utils.text import _make_slug

VALID_CLIENT_TYPES = {"external", "internal", "personal"}
VALID_STATUSES = frozenset({"listed", "unlisted", "removed"})


def list_clients(
    conn: sqlite3.Connection, *, status: Optional[str | list[str]] = None, limit: int = 50, page: int = 1
) -> dict:
    """Return clients with project and file counts.

    Parameters
    ----------
    conn : sqlite3.Connection
    status : str, list[str], or None
        ``None`` → all except ``removed`` (default).
        ``"all"`` → no status filter.
        Single string → exact match.
        List of strings → ``WHERE status IN (...)``.
    limit : int
        Maximum rows per page (default 50).
    page : int
        1-based page number (default 1).

    Returns
    -------
    dict
        ``{"clients": [...], "pagination": {page, limit, total, total_pages}}``
    """
    conditions: list[str] = []
    params: list = []

    status_conds, status_params = build_status_filter(
        status,
        column="client.status",
        default_exclude=["removed"],
    )
    conditions.extend(status_conds)
    params.extend(status_params)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) FROM clients client{where}"
    fetch_sql = f"""
        SELECT client.id, client.name, client.slug, client.client_type, client.status,
               client.mcp_view, client.mcp_read, client.path_pattern,
               (SELECT COUNT(*) FROM projects project WHERE project.client_id = client.id) as project_count,
               (SELECT COUNT(*) FROM files file
                JOIN projects project ON file.project_id = project.id
                WHERE project.client_id = client.id AND file.status != 'removed') as file_count
        FROM clients client
        {where}
        ORDER BY client.name
        LIMIT ? OFFSET ?
    """
    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    clients = [
        {
            "id": row["id"],
            "name": row["name"],
            "slug": row["slug"],
            "client_type": row["client_type"],
            "status": row["status"],
            "project_count": row["project_count"],
            "file_count": row["file_count"],
            "mcp_view": row["mcp_view"] or "inherit",
            "mcp_read": row["mcp_read"] or "inherit",
            "path_pattern": row["path_pattern"] or "",
        }
        for row in rows
    ]

    return paginated_response("clients", clients, pagination)


CLIENT_DEPENDENT_TABLES = ("projects", "files", "folders", "chats", "emails", "visits")


def count_client_dependents(conn: sqlite3.Connection, client_id: int) -> dict[str, int]:
    """Return per-table counts of records with ``client_id = ?``.

    Tables included: ``CLIENT_DEPENDENT_TABLES``. Zero counts are kept so
    callers can render "0 projects, 2 files" verbatim.
    """
    counts: dict[str, int] = {}
    for table in CLIENT_DEPENDENT_TABLES:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        counts[table] = row[0] if row else 0
    return counts


def delete_client(conn: sqlite3.Connection, client_id: int) -> Optional[dict]:
    """Hard-delete a client row.

    Returns ``None`` if the client does not exist. If any dependent records
    point at the client, returns ``{"blocked": True, "dependents": {...}}``
    without deleting. Otherwise issues ``DELETE FROM clients`` and returns
    ``{"deleted": True}``.
    """
    cursor = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
    if cursor.fetchone() is None:
        return None

    counts = count_client_dependents(conn, client_id)
    if any(c > 0 for c in counts.values()):
        return {"blocked": True, "dependents": counts}

    conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    return {"deleted": True}


def update_client(conn: sqlite3.Connection, client_id: int, **fields) -> Optional[bool]:
    """Update a client's fields.

    Returns True on success, None if client not found.
    Raises ValueError on invalid input.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM clients WHERE id = ?", (client_id,))
    if not cursor.fetchone():
        return None

    updatable = {"name", "client_type", "path_pattern", "status", "status_reason", "metadata"}
    sql_fields = []
    values = []
    new_name = None

    for key in updatable:
        if key in fields:
            val = fields[key]
            if key == "metadata" and val is not None:
                val = json.dumps(val)
            if key == "client_type" and val not in VALID_CLIENT_TYPES:
                valid = ", ".join(sorted(VALID_CLIENT_TYPES))
                raise ValueError(f"Invalid client_type. Must be one of: {valid}")
            if key == "name":
                new_name = (val or "").strip()
                if not new_name:
                    raise ValueError("Name cannot be empty")
                val = new_name
            sql_fields.append(f"{key} = ?")
            values.append(val)

    if not sql_fields:
        return True

    if new_name:
        new_slug = _make_slug(new_name)
        sql_fields.append("slug = ?")
        values.append(new_slug)

    values.append(client_id)
    try:
        cursor.execute(
            f"UPDATE clients SET {', '.join(sql_fields)} WHERE id = ?",
            values,
        )
    except sqlite3.IntegrityError:
        raise ValueError("A client with that name or slug already exists")

    if new_name:
        cursor.execute(
            "UPDATE projects SET client = ? WHERE client_id = ?",
            (new_name, client_id),
        )

    conn.commit()
    return True


def create_client(conn: sqlite3.Connection, *, name: str, client_type: str, path_pattern: Optional[str] = None) -> dict:
    """Create a new client.

    Returns dict with ``id`` and ``slug``.
    Raises ValueError on invalid input or duplicate.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required")
    if client_type not in VALID_CLIENT_TYPES:
        valid = ", ".join(sorted(VALID_CLIENT_TYPES))
        raise ValueError(f"Invalid client_type. Must be one of: {valid}")

    slug = _make_slug(name)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO clients (name, slug, client_type, path_pattern)
               VALUES (?, ?, ?, ?)""",
            (name, slug, client_type, path_pattern),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"A client with name '{name}' or slug '{slug}' already exists")
    return {"id": cursor.lastrowid, "slug": slug}


def get_client(conn: sqlite3.Connection, client_id: int) -> Optional[dict]:
    """Fetch a single client with its projects and file count.

    Returns a dict with client fields, ``projects`` list, and
    ``file_count``, or ``None`` if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, name, slug, client_type, status, path_pattern,
                  mcp_view, mcp_read
           FROM clients WHERE id = ?""",
        (client_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    client = {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "client_type": row["client_type"],
        "status": row["status"],
        "path_pattern": row["path_pattern"] or "",
        "mcp_view": row["mcp_view"] or "inherit",
        "mcp_read": row["mcp_read"] or "inherit",
    }

    # Attached projects
    cursor.execute(
        """SELECT id, project_name, project_type, status
           FROM projects WHERE client_id = ? ORDER BY project_name""",
        (client_id,),
    )
    client["projects"] = [
        {
            "id": r["id"],
            "project_name": r["project_name"],
            "project_type": r["project_type"],
            "status": r["status"],
        }
        for r in cursor.fetchall()
    ]

    # File count across all projects for this client
    cursor.execute(
        """SELECT COUNT(*) as cnt FROM files file
           JOIN projects project ON file.project_id = project.id
           WHERE project.client_id = ? AND file.status != 'removed'""",
        (client_id,),
    )
    client["file_count"] = cursor.fetchone()["cnt"]

    return client


def find_by_name_fuzzy(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Find clients matching name with LIKE %name%.

    Returns all columns including mcp_view. Does NOT filter by visibility.
    """
    rows = conn.execute(
        """SELECT id, name, slug, client_type, path_pattern, status,
                  created_at, mcp_view, mcp_read
           FROM clients WHERE name LIKE ?""",
        (f"%{name}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def count_hidden_by_name(conn: sqlite3.Connection, name: str) -> int:
    """Count hidden clients matching a fuzzy name query (for diagnostics)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM clients WHERE name LIKE ? AND COALESCE(mcp_view, 'inherit') = 'hidden'",
        (f"%{name}%",),
    ).fetchone()
    return row[0]


def get_client_navigation(conn: sqlite3.Connection, client_id: int, project_ids: list[int]) -> dict:
    """Return navigation aggregates for an MCP client view."""
    if not project_ids:
        return {
            "total_files": 0,
            "total_size_bytes": 0,
            "total_folders": 0,
            "total_entities": {"emails": 0, "chats": 0, "visits": 0},
        }

    ph = ",".join("?" * len(project_ids))
    _nh = "AND COALESCE(mcp_view, 'inherit') != 'hidden'"

    stats = conn.execute(
        f"""SELECT COUNT(*) as count, COALESCE(SUM(size_bytes), 0) as size
            FROM files
            WHERE project_id IN ({ph}) AND status != 'removed' {_nh}""",
        project_ids,
    ).fetchone()

    folder_count = conn.execute(
        f"SELECT COUNT(*) FROM folders WHERE project_id IN ({ph}) {_nh}",
        project_ids,
    ).fetchone()[0]

    email_count = conn.execute(
        f"SELECT COUNT(*) FROM emails WHERE project_id IN ({ph}) AND status != 'removed' {_nh}",
        project_ids,
    ).fetchone()[0]
    chat_count = conn.execute(
        f"SELECT COUNT(*) FROM chats WHERE project_id IN ({ph}) AND status != 'removed' {_nh}",
        project_ids,
    ).fetchone()[0]
    browser_count = conn.execute(
        f"SELECT COUNT(*) FROM visits WHERE project_id IN ({ph}) AND status != 'removed' {_nh}",
        project_ids,
    ).fetchone()[0]

    return {
        "total_files": stats["count"],
        "total_size_bytes": stats["size"],
        "total_folders": folder_count,
        "total_entities": {
            "emails": email_count,
            "chats": chat_count,
            "visits": browser_count,
        },
    }


def find_client_id_by_name(conn: sqlite3.Connection, name: str) -> Optional[int]:
    """Return the client ID for the given name, or None if not found."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM clients WHERE name = ?", (name,))
    row = cursor.fetchone()
    return row["id"] if row else None
