"""Search queries — keyword, FTS5, and enrichment.

All search SQL lives here. Services call these functions and handle
role-based business logic (visibility filtering, fallback orchestration).
Some SQL-level pre-filtering (e.g. exclude_hidden) is parameterised here
for query efficiency; the service decides the parameter value.
"""

import sqlite3
from pathlib import Path
from typing import Optional

from footprinter.db.sql_utils import (
    build_fts5_query,
    build_term_conditions,
    paginate,
    paginated_response,
    split_query_terms,
)

HOME = str(Path.home())


def search_files(
    conn: sqlite3.Connection,
    query: str,
    source: str = "all",
    limit: int = 100,
    file_ext: str | None = None,
    page: int = 1,
) -> dict:
    """Search files by name using FTS5.

    Parameters
    ----------
    conn : sqlite3.Connection
    query : str
        Search term (minimum 2 characters).
    source : str
        Filter: "all", "local", or "remote".
    limit : int
        Maximum results per page (default: 100).
    file_ext : str or None
        Filter by file extension (e.g. ".pdf"). Case-insensitive.
    page : int
        1-based page number.

    Returns
    -------
    dict with keys: results (list of dicts with ``fts_score``), pagination
    """
    if len(query) < 2:
        return paginated_response("results", [], {"page": page, "limit": limit, "total": 0, "total_pages": 1})

    if source == "local":
        source_filter = "source = 'local'"
    elif source == "remote":
        source_filter = "source IN (SELECT name FROM sources WHERE source_type = 'remote')"
    else:
        source_filter = "source IS NOT NULL"

    fts_query = f'"{query}"*'

    ext_clause = ""
    params: list = [fts_query]
    if file_ext:
        escaped_ext = file_ext.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        ext_clause = "AND lower(file.name) LIKE ? ESCAPE '\\'"
        params.append(f"%{escaped_ext}")

    count_sql = f"""
        SELECT COUNT(*)
        FROM files file
        JOIN files_fts fts ON fts.rowid = file.id
        WHERE files_fts MATCH ?
        AND file.{source_filter}
        AND file.status != 'removed'
        {ext_clause}
    """
    fetch_sql = f"""
        SELECT file.id, file.source, file.name, file.path, file.content_type, file.size_bytes,
               file.modified_at, fts.rank as fts_rank
        FROM files file
        JOIN files_fts fts ON fts.rowid = file.id
        WHERE files_fts MATCH ?
        AND file.{source_filter}
        AND file.status != 'removed'
        {ext_clause}
        ORDER BY fts.rank
        LIMIT ? OFFSET ?
    """

    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    results = []
    for row in rows:
        # FTS5 rank is negative (more negative = better match)
        fts_rank = row["fts_rank"] if row["fts_rank"] is not None else 0.0
        fts_score = min(1.0, abs(fts_rank) / 10.0)

        results.append(
            {
                "id": row["id"],
                "source": row["source"],
                "name": row["name"],
                "path": row["path"] or "",
                "content_type": row["content_type"] or "",
                "size_bytes": row["size_bytes"],
                "modified_at": row["modified_at"] or "",
                "fts_score": fts_score,
            }
        )

    return paginated_response("results", results, pagination)


# ---------------------------------------------------------------------------
# Keyword search (extracted from search_service)
# ---------------------------------------------------------------------------


def search_files_keyword(
    conn: sqlite3.Connection,
    *,
    terms: list[str] = (),
    has_query: bool = False,
    project: Optional[str] = None,
    client: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account: Optional[str] = None,
    folder: Optional[str] = None,
    mime_type: Optional[str] = None,
    limit: int = 50,
    exclude_hidden: bool = True,
) -> list[dict]:
    """Keyword search for files with optional filters.

    Returns list of dicts with file metadata including project/client joins.
    """
    params: list = []
    where = ["file.status != 'removed'"]
    fts_join = ""

    if has_query:
        fts5_str = build_fts5_query(list(terms))
        if fts5_str:
            fts_join = "JOIN files_fts fts ON fts.rowid = file.id"
            where.append("files_fts MATCH ?")
            params.append(fts5_str)

    if project:
        where.append("project.project_name = ?")
        params.append(project)
    if client:
        where.append("client.name = ?")
        params.append(client)
    if date_from:
        where.append("file.modified_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("file.modified_at <= ?")
        params.append(date_to)
    if account:
        where.append("file.account = ?")
        params.append(account)
    if folder:
        folder_path = folder.replace("~", HOME, 1) if folder.startswith("~") else folder
        where.append("file.path LIKE ?")
        params.append(f"{folder_path}%")
    if mime_type:
        where.append("file.mime_type = ?")
        params.append(mime_type)
    if exclude_hidden:
        where.append("file.mcp_view != 'hidden'")
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT file.id, file.source, file.name, file.path, file.content_type,
               file.size_bytes, file.modified_at, file.account, file.mime_type,
               file.mcp_view,
               project.project_name, client.name AS client
        FROM files file
        {fts_join}
        LEFT JOIN projects project ON file.project_id = project.id
        LEFT JOIN clients client ON project.client_id = client.id
        WHERE {" AND ".join(where)}
        ORDER BY file.modified_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        {
            "id": r["id"],
            "source": r["source"],
            "name": r["name"],
            "path": r["path"],
            "content_type": r["content_type"],
            "size_bytes": r["size_bytes"],
            "modified_at": r["modified_at"],
            "account": r["account"],
            "mime_type": r["mime_type"],
            "project": r["project_name"],
            "client": r["client"],
            "mcp_view": r["mcp_view"],
        }
        for r in rows
    ]


def search_emails_keyword(
    conn: sqlite3.Connection,
    *,
    terms: list[str] = (),
    has_query: bool = False,
    project: Optional[str] = None,
    client: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account: Optional[str] = None,
    sender: Optional[str] = None,
    days_back: Optional[int] = None,
    limit: int = 50,
    exclude_hidden: bool = True,
) -> list[dict]:
    """Keyword search for emails with optional filters.

    Returns list of dicts with email metadata including project/client joins.
    """
    params: list = []
    where: list[str] = ["email.status != 'removed'"]
    fts_join = ""

    if has_query:
        fts5_str = build_fts5_query(list(terms))
        if fts5_str:
            fts_join = "JOIN emails_fts fts ON fts.rowid = email.id"
            where.append("emails_fts MATCH ?")
            params.append(fts5_str)

    if project:
        where.append("project.project_name = ?")
        params.append(project)
    if client:
        where.append("client.name = ?")
        params.append(client)
    if date_from:
        where.append("email.received_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("email.received_at <= ?")
        params.append(date_to)
    if account:
        where.append("email.account = ?")
        params.append(account)
    if sender:
        where.append("(email.from_address LIKE ? OR email.from_name LIKE ?)")
        params.extend([f"%{sender}%", f"%{sender}%"])
    if days_back is not None and int(days_back) > 0:
        where.append(f"email.received_at >= datetime('now', '-{int(days_back)} days')")
    if exclude_hidden:
        where.append("email.mcp_view != 'hidden'")
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT email.id, email.message_id, email.subject, email.from_address,
               email.from_name, email.to_addresses, email.received_at,
               email.account, email.labels, email.body_preview,
               email.mcp_view, email.mcp_read,
               project.project_name, client.name AS client_name
        FROM emails email
        {fts_join}
        LEFT JOIN projects project ON email.project_id = project.id
        LEFT JOIN clients client ON email.client_id = client.id
        WHERE {" AND ".join(where)}
        ORDER BY email.received_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        {
            "id": r["id"],
            "subject": r["subject"],
            "from": r["from_name"] or r["from_address"],
            "from_address": r["from_address"],
            "to": r["to_addresses"],
            "received_at": r["received_at"],
            "account": r["account"],
            "labels": r["labels"],
            "snippet": r["body_preview"],
            "project_name": r["project_name"],
            "client_name": r["client_name"],
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]


def search_chats_keyword(
    conn: sqlite3.Connection,
    *,
    terms: list[str] = (),
    has_query: bool = False,
    project: Optional[str] = None,
    client: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    exclude_hidden: bool = True,
) -> list[dict]:
    """Keyword search for chats with optional filters.

    Returns list of dicts with chat metadata including project/client joins.
    """
    params: list = []
    where: list[str] = ["chat.status != 'removed'"]

    if has_query:
        cond, cond_params = build_term_conditions(["chat.title"], list(terms))
        where.append(cond)
        params.extend(cond_params)

    if project:
        where.append("project.project_name = ?")
        params.append(project)
    if client:
        where.append("client.name = ?")
        params.append(client)
    if date_from:
        where.append("chat.created_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("chat.created_at <= ?")
        params.append(date_to)
    if exclude_hidden:
        where.append("chat.mcp_view != 'hidden'")
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT chat.id, chat.external_id, chat.account, chat.title,
               chat.summary, chat.created_at, chat.modified_at,
               chat.message_count, chat.mcp_view, chat.mcp_read,
               project.project_name, client.name AS client_name
        FROM chats chat
        LEFT JOIN projects project ON chat.project_id = project.id
        LEFT JOIN clients client ON chat.client_id = client.id
        WHERE {" AND ".join(where)}
        ORDER BY chat.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        {
            "id": r["id"],
            "external_id": r["external_id"],
            "account": r["account"],
            "title": r["title"],
            "summary": r["summary"],
            "created_at": r["created_at"],
            "message_count": r["message_count"],
            "project_name": r["project_name"],
            "client_name": r["client_name"],
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]


def search_browser_keyword(
    conn: sqlite3.Connection,
    *,
    terms: list[str] = (),
    has_query: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Keyword search for browser visits with optional filters.

    Returns list of dicts with visit metadata. Source-level visibility
    gating is handled by the service layer, not here.
    """
    params: list = []
    where: list[str] = ["status != 'removed'"]

    if has_query:
        cond, cond_params = build_term_conditions(["url", "title"], list(terms))
        where.append(cond)
        params.extend(cond_params)

    if date_from:
        where.append("visit_time >= ?")
        params.append(date_from)
    if date_to:
        where.append("visit_time <= ?")
        params.append(date_to)
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT id, url, title, visit_time, browser
        FROM visits
        WHERE {" AND ".join(where)}
        ORDER BY visit_time DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    return [
        {
            "id": r["id"],
            "url": r["url"],
            "title": r["title"],
            "visit_time": r["visit_time"],
            "browser": r["browser"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# FTS5 fallback (extracted from semantic_service)
# ---------------------------------------------------------------------------


def chat_fts5_fallback(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[dict]:
    """FTS5 keyword fallback for chat search.

    Returns dicts shaped for semantic_service consumption: chat_id, chat_title,
    snippet, relevance_score, source, created_at, message_id.
    """
    safe_query = query.replace('"', '""')
    fts_query = f'"{safe_query}"*'

    rows = conn.execute(
        """SELECT chat.id, chat.title, chat.summary, chat.account,
                  chat.created_at, chat.message_count, fts.rank as fts_rank
           FROM chats_fts fts
           JOIN chats chat ON chat.id = fts.rowid
           WHERE chats_fts MATCH ?
             AND chat.status != 'removed'
           ORDER BY fts.rank
           LIMIT ?""",
        (fts_query, limit),
    ).fetchall()

    results = []
    for r in rows:
        fts_rank = r["fts_rank"] if r["fts_rank"] is not None else 0.0
        score = round(min(1.0, abs(fts_rank) / 10.0), 3)
        results.append(
            {
                "chat_id": r["id"],
                "chat_title": r["title"],
                "message_id": None,
                "source": r["account"],
                "created_at": r["created_at"],
                "snippet": r["summary"] or "",
                "relevance_score": score,
            }
        )
    return results


def file_fts5_fallback(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[dict]:
    """FTS5 keyword fallback for file search.

    Returns dicts shaped for semantic_service consumption: id, source, name,
    path, content_type, size_bytes, modified_at, relevance_score, snippet,
    mcp_view, mcp_read.
    """
    terms = split_query_terms(query)
    match_str = build_fts5_query(terms)
    if not match_str:
        return []

    rows = conn.execute(
        "SELECT file.id, file.source, file.name, file.path, "
        "file.content_type, file.size_bytes, "
        "file.modified_at, file.mcp_view, file.mcp_read, "
        "file.content_preview "
        "FROM files file "
        "JOIN files_fts fts ON fts.rowid = file.id "
        "WHERE files_fts MATCH ? AND file.status != 'removed' "
        "LIMIT ?",
        (match_str, limit),
    ).fetchall()

    results = []
    for row in rows:
        if row["content_preview"] and row["mcp_read"] != "deny":
            snippet = row["content_preview"][:200]
        else:
            snippet = f"{row['name']} — {row['path']}"
        results.append(
            {
                "id": row["id"],
                "source": row["source"],
                "name": row["name"],
                "path": row["path"],
                "content_type": row["content_type"],
                "size_bytes": row["size_bytes"],
                "modified_at": row["modified_at"],
                "relevance_score": 0.5,
                "snippet": snippet,
                "mcp_view": row["mcp_view"],
                "mcp_read": row["mcp_read"],
            }
        )
    return results


# ---------------------------------------------------------------------------
# Enrichment queries (extracted from semantic_service)
# ---------------------------------------------------------------------------


def enrich_chat_visibility(
    conn: sqlite3.Connection,
    chat_ids: list[int],
) -> dict[int, dict]:
    """Fetch visibility fields for a set of chat IDs.

    Returns {chat_id: {account, mcp_view, mcp_read}} lookup dict.
    """
    if not chat_ids:
        return {}
    ph = ",".join("?" * len(chat_ids))
    rows = conn.execute(
        f"SELECT id, account, mcp_view, mcp_read FROM chats WHERE id IN ({ph})",
        chat_ids,
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def enrich_file_metadata(
    conn: sqlite3.Connection,
    file_ids: list[int],
) -> dict[int, dict]:
    """Fetch metadata for a set of file IDs (excludes removed).

    Returns {file_id: {id, source, name, path, ...}} lookup dict.
    """
    if not file_ids:
        return {}
    ph = ",".join("?" * len(file_ids))
    rows = conn.execute(
        f"SELECT id, source, name, path, content_type, size_bytes, "
        f"modified_at, mcp_view, mcp_read "
        f"FROM files WHERE id IN ({ph}) AND status != 'removed'",
        file_ids,
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}
