"""Browser visit queries and write operations.

Provides list, detail lookups, and insert functions for the visits table.
"""

import sqlite3
from typing import Any, Dict, Optional, Union

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response


def list_visits(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    page: int = 1,
    status: Optional[str | list[str]] = None,
) -> dict:
    """List browser visit entries ordered by visit_time descending.

    Parameters
    ----------
    conn : sqlite3.Connection
    limit : int
        Maximum rows per page (default 50).
    page : int
        1-based page number (default 1).
    status : str, list[str], or None
        ``None`` → all except ``removed`` (default).
        ``"all"`` → no status filter.
        Single string → exact match.
        List of strings → ``WHERE status IN (...)``.

    Returns
    -------
    dict
        ``{"visits": [...], "pagination": {page, limit, total, total_pages}}``
    """
    status_conds, status_params = build_status_filter(
        status, column="bv.status", default_exclude=["removed"]
    )
    where_clause = "WHERE " + " AND ".join(status_conds) if status_conds else ""

    rows, pagination = paginate(
        conn,
        f"SELECT COUNT(*) FROM visits bv {where_clause}",
        f"""
        SELECT bv.id, bv.url, bv.title, bv.visit_time, bv.browser, bv.visit_count,
               bv.client_id, bv.project_id,
               client.name AS client_name, project.project_name,
               bv.mcp_view, bv.mcp_read
        FROM visits bv
        LEFT JOIN clients client ON bv.client_id = client.id
        LEFT JOIN projects project ON bv.project_id = project.id
        {where_clause}
        ORDER BY bv.visit_time DESC
        LIMIT ? OFFSET ?
        """,
        list(status_params),
        page=page,
        limit=limit,
    )
    visits = [
        {
            "id": r["id"],
            "url": r["url"],
            "title": r["title"],
            "visit_time": r["visit_time"],
            "browser": r["browser"],
            "visit_count": r["visit_count"],
            "client_id": r["client_id"],
            "project_id": r["project_id"],
            "client_name": r["client_name"],
            "project_name": r["project_name"],
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]

    return paginated_response("visits", visits, pagination)


def get_visit(conn: sqlite3.Connection, entry_id: int) -> dict | None:
    """Get a single browser history entry by ID.

    Returns
    -------
    dict or None
        Includes indexed_at. None if not found.
    """
    cursor = conn.execute(
        """
        SELECT bv.id, bv.url, bv.title, bv.visit_time, bv.browser, bv.visit_count,
               bv.indexed_at, bv.status,
               bv.client_id, bv.project_id,
               client.name AS client_name, project.project_name,
               bv.mcp_view, bv.mcp_read
        FROM visits bv
        LEFT JOIN clients client ON bv.client_id = client.id
        LEFT JOIN projects project ON bv.project_id = project.id
        WHERE bv.id = ?
        """,
        (entry_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "visit_time": row["visit_time"],
        "browser": row["browser"],
        "visit_count": row["visit_count"],
        "indexed_at": row["indexed_at"],
        "status": row["status"],
        "client_id": row["client_id"],
        "project_id": row["project_id"],
        "client_name": row["client_name"],
        "project_name": row["project_name"],
        "mcp_view": row["mcp_view"],
        "mcp_read": row["mcp_read"],
    }


def update_visit_relationships(
    conn: sqlite3.Connection,
    visit_id: int,
    *,
    project_id: Optional[int] = None,
    client_id: Optional[int] = None,
) -> Optional[bool]:
    """Update project and/or client assignment on a visit.

    Only updates fields that are passed (not None). Pass ``0`` to clear
    a field (set to NULL). Stamps ``assignment_source = 'user'``
    when the column exists (app-scope DBs only).
    Returns True on success, None if visit not found.
    """
    cursor = conn.execute("SELECT id FROM visits WHERE id = ?", (visit_id,))
    if cursor.fetchone() is None:
        return None

    if project_id is not None and project_id != 0:
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise ValueError(f"No project with id {project_id}")
    if client_id is not None and client_id != 0:
        cli = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not cli:
            raise ValueError(f"No client with id {client_id}")

    sets: list[str] = []
    params: list = []
    if project_id is not None:
        if project_id == 0:
            sets.append("project_id = NULL")
        else:
            sets.append("project_id = ?")
            params.append(project_id)
    if client_id is not None:
        if client_id == 0:
            sets.append("client_id = NULL")
        else:
            sets.append("client_id = ?")
            params.append(client_id)
    if not sets:
        return True

    sets.append("assignment_source = 'user'")
    params.append(visit_id)
    try:
        conn.execute(f"UPDATE visits SET {', '.join(sets)} WHERE id = ?", params)
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e):
            raise
        # assignment_source not present (tool-only DB)
        sets.pop()
        conn.execute(f"UPDATE visits SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def insert_visit(conn: sqlite3.Connection, history_data: Dict[str, Any]) -> Union[int, bool]:
    """Insert a browser visit record.

    Returns the row ID on success, or False if the visit already exists
    (duplicate on url + visit_time + browser).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO visits
        (url, title, visit_time, browser, visit_count,
         indexed_at, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """,
        (
            history_data["url"],
            history_data.get("title"),
            history_data["visit_time"],
            history_data["browser"],
            history_data.get("visit_count", 1),
        ),
    )
    if cursor.rowcount == 0:
        return False
    return cursor.lastrowid
