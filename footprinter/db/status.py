"""System status queries — table counts, config presence, last-indexed timestamp.

Includes both MCP-oriented status and the legacy get_stats.
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional


def _safe_count(cursor: sqlite3.Cursor, query: str) -> int:
    """Execute a COUNT query, returning 0 if the table doesn't exist."""
    try:
        cursor.execute(query)
        return cursor.fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _safe_query(conn: sqlite3.Connection, query: str, *, default: Any = None) -> Any:
    """Execute a query and return the first column of the first row.

    Returns ``default`` if the table doesn't exist or the query returns no rows.
    """
    try:
        row = conn.execute(query).fetchone()
        return row[0] if row else default
    except sqlite3.OperationalError:
        return default


def _safe_fetchall(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """Execute a query and return all rows, or [] on missing table."""
    try:
        return conn.execute(query).fetchall()
    except sqlite3.OperationalError:
        return []


# -- Hidden-client NOT EXISTS clause (reused across source queries) -----------
_NOT_HIDDEN_CLIENT = (
    "NOT EXISTS (  SELECT 1 FROM clients client  WHERE client.id = {alias}.client_id AND client.mcp_view = 'hidden')"
)


def get_mcp_status(conn: sqlite3.Connection) -> dict:
    """Return MCP-oriented status: per-source counts excluding hidden clients.

    Designed for ``role=Role.VIEWER`` callers — hidden-client rows are
    excluded from emails, chats, messages, and browser counts.
    """
    # -- Per-table counts and last-sync timestamps ----------------------------
    tables: Dict[str, Dict[str, Optional[str]]] = {
        "files": {
            "count": (
                "SELECT COUNT(*) FROM files WHERE status = 'listed' AND COALESCE(mcp_view, 'inherit') != 'hidden'"
            ),
            "latest": (
                "SELECT MAX(indexed_at) FROM files "
                "WHERE status = 'listed' "
                "AND COALESCE(mcp_view, 'inherit') != 'hidden'"
            ),
        },
        "emails": {
            "count": (
                "SELECT COUNT(*) FROM emails email "
                "WHERE email.status = 'listed' "
                f"AND {_NOT_HIDDEN_CLIENT.format(alias='email')}"
            ),
            "latest": (
                "SELECT MAX(indexed_at) FROM emails email "
                "WHERE email.status = 'listed' "
                f"AND {_NOT_HIDDEN_CLIENT.format(alias='email')}"
            ),
        },
        "chats": {
            "count": (
                "SELECT COUNT(*) FROM chats chat "
                "WHERE COALESCE(chat.status, 'listed') != 'removed' "
                f"AND {_NOT_HIDDEN_CLIENT.format(alias='chat')}"
            ),
            "latest": (
                "SELECT MAX(modified_at) FROM chats chat "
                "WHERE COALESCE(chat.status, 'listed') != 'removed' "
                f"AND {_NOT_HIDDEN_CLIENT.format(alias='chat')}"
            ),
        },
        "messages": {
            "count": (
                "SELECT COUNT(*) FROM messages message "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM chats chat"
                "  JOIN clients client ON client.id = chat.client_id"
                "  WHERE chat.id = message.chat_id AND client.mcp_view = 'hidden'"
                ")"
            ),
            "latest": (
                "SELECT MAX(created_at) FROM messages message "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM chats chat"
                "  JOIN clients client ON client.id = chat.client_id"
                "  WHERE chat.id = message.chat_id AND client.mcp_view = 'hidden'"
                ")"
            ),
        },
        "browser": {
            "count": (
                "SELECT COUNT(*) FROM visits bv "
                "WHERE bv.status = 'listed' "
                f"AND {_NOT_HIDDEN_CLIENT.format(alias='bv')}"
            ),
            "latest": (
                "SELECT MAX(visit_time) FROM visits bv "
                "WHERE bv.status = 'listed' "
                f"AND {_NOT_HIDDEN_CLIENT.format(alias='bv')}"
            ),
        },
        "projects": {
            "count": "SELECT COUNT(*) FROM projects",
            "latest": "SELECT MAX(created_at) FROM projects",
        },
        "clients": {
            "count": ("SELECT COUNT(*) FROM clients WHERE COALESCE(mcp_view, 'inherit') != 'hidden'"),
            "latest": None,
        },
    }

    sources: Dict[str, dict] = {}
    for table, queries in tables.items():
        count = _safe_query(conn, queries["count"], default=0)
        latest = _safe_query(conn, queries["latest"]) if queries["latest"] else None
        if count == 0 and latest is None:
            # Confirm count isn't masking a missing-table error
            try:
                conn.execute(queries["count"])
            except sqlite3.OperationalError:
                sources[table] = {"count": 0, "last_sync": None, "error": "table not found"}
                continue
        sources[table] = {"count": count, "last_sync": latest}

    # -- File breakdown by source ---------------------------------------------
    rows = _safe_fetchall(
        conn,
        """
        SELECT source, COUNT(*) as count, COALESCE(SUM(size_bytes), 0) as size
        FROM files WHERE status = 'listed'
        AND COALESCE(mcp_view, 'inherit') != 'hidden'
        GROUP BY source
    """,
    )
    files_by_source = {r["source"]: {"count": r["count"], "size_bytes": r["size"]} for r in rows}

    # -- File breakdown by status ---------------------------------------------
    rows = _safe_fetchall(
        conn,
        """
        SELECT status, COUNT(*) as count FROM files GROUP BY status
    """,
    )
    files_by_status = {r["status"]: r["count"] for r in rows}

    # -- Project count by status ----------------------------------------------
    rows = _safe_fetchall(
        conn,
        """
        SELECT status, COUNT(*) as count FROM projects GROUP BY status
    """,
    )
    projects_by_status = {r["status"]: r["count"] for r in rows}

    # -- Emails by client (excludes hidden clients and removed emails) --------
    rows = _safe_fetchall(
        conn,
        """
        SELECT COALESCE(client.name, '(unassigned)') AS client_name, COUNT(*) as count
        FROM emails email LEFT JOIN clients client ON email.client_id = client.id
        WHERE email.status = 'listed'
          AND (client.mcp_view IS NULL OR client.mcp_view != 'hidden')
        GROUP BY client_name
    """,
    )
    emails_by_client = {r["client_name"]: r["count"] for r in rows}

    # -- Chats by client (excludes hidden clients and removed chats) ----------
    rows = _safe_fetchall(
        conn,
        """
        SELECT COALESCE(client.name, '(unassigned)') AS client_name, COUNT(*) as count
        FROM chats chat LEFT JOIN clients client ON chat.client_id = client.id
        WHERE chat.status = 'listed'
          AND (client.mcp_view IS NULL OR client.mcp_view != 'hidden')
        GROUP BY client_name
    """,
    )
    chats_by_client = {r["client_name"]: r["count"] for r in rows}

    return {
        "sources": sources,
        "files_by_source": files_by_source,
        "files_by_status": files_by_status,
        "projects_by_status": projects_by_status,
        "emails_by_client": emails_by_client,
        "chats_by_client": chats_by_client,
    }


def get_system_status(conn: sqlite3.Connection, config_path: Path) -> dict:
    """Return system status dict with table counts, data presence, and config check.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database connection with row_factory = sqlite3.Row.
    config_path : Path
        Absolute path to config.yaml (caller decides where it lives).

    Returns
    -------
    dict with keys: has_data, config_exists, counts, total, last_indexed
    """
    cur = conn.cursor()

    counts = {
        "files": _safe_count(cur, "SELECT COUNT(*) FROM files WHERE status = 'listed'"),
        "folders": _safe_count(cur, "SELECT COUNT(*) FROM folders WHERE status = 'listed'"),
        "visits": _safe_count(cur, "SELECT COUNT(*) FROM visits"),
        "emails": _safe_count(cur, "SELECT COUNT(*) FROM emails"),
        "messages": _safe_count(cur, "SELECT COUNT(*) FROM messages"),
        "projects": _safe_count(cur, "SELECT COUNT(*) FROM projects"),
    }

    try:
        cur.execute("SELECT MAX(indexed_at) FROM files")
        last_indexed = cur.fetchone()[0]
    except sqlite3.OperationalError:
        last_indexed = None

    total = sum(counts.values())

    return {
        "has_data": total > 0,
        "config_exists": config_path.exists(),
        "counts": counts,
        "total": total,
        "last_indexed": last_indexed,
    }


# ---------------------------------------------------------------------------
# Write/aggregate operations
# ---------------------------------------------------------------------------


def get_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Get database statistics."""
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as count FROM files WHERE status = 'listed'")
    files_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(DISTINCT content_type) as count FROM files WHERE status = 'listed'")
    content_types_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM visits")
    urls_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(DISTINCT browser) as count FROM visits")
    browsers_count = cursor.fetchone()["count"]

    cursor.execute(
        """
        SELECT MAX(modified_at) as latest_file
        FROM files
        WHERE modified_at IS NOT NULL AND status = 'listed'
    """
    )
    latest_file = cursor.fetchone()["latest_file"]

    cursor.execute("SELECT MAX(visit_time) as latest_visit FROM visits")
    latest_visit = cursor.fetchone()["latest_visit"]

    # Query remote source names directly from the sources table
    rows = cursor.execute("SELECT name FROM sources WHERE source_type = 'remote'").fetchall()
    remote_sources = [r["name"] for r in rows]

    if remote_sources:
        remote_ph = ",".join("?" * len(remote_sources))
        cursor.execute(
            f"SELECT COUNT(*) as count FROM files WHERE source IN ({remote_ph}) AND status = 'listed'",
            remote_sources,
        )
        remote_files_count = cursor.fetchone()["count"]

        cursor.execute(
            f"SELECT COUNT(DISTINCT source) as count FROM files WHERE source IN ({remote_ph}) AND status = 'listed'",
            remote_sources,
        )
        remote_sources_count = cursor.fetchone()["count"]
    else:
        remote_files_count = 0
        remote_sources_count = 0

    cursor.execute("SELECT COUNT(*) as count FROM emails")
    emails_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(DISTINCT account) as count FROM emails")
    email_accounts_count = cursor.fetchone()["count"]

    return {
        "total_files": files_count,
        "content_types": content_types_count,
        "total_urls": urls_count,
        "browsers": browsers_count,
        "latest_file_modified": latest_file,
        "latest_visit": latest_visit,
        "remote_files": remote_files_count,
        "remote_sources": remote_sources_count,
        "total_emails": emails_count,
        "email_accounts": email_accounts_count,
    }
