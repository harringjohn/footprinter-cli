"""Email queries, write operations — list, detail, and insert.

Query and write layer for email data.
"""

import json
import sqlite3
from typing import Any, Dict, Optional

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response

SORT_WHITELIST = {"subject", "from_address", "account", "received_at", "has_attachments"}


def list_emails(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "received_at",
    order: str = "desc",
    account: Optional[str] = None,
    client_id: Optional[int] = None,
    project_id: Optional[int] = None,
    query: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    status: Optional[str | list[str]] = None,
) -> dict:
    """List emails with pagination, filtering, and sorting.

    ``status`` follows the standard contract: ``None`` excludes ``removed``;
    ``"all"`` bypasses; otherwise exact match or IN.

    Returns dict with keys: emails, pagination.
    """
    sort_col = sort_by if sort_by in SORT_WHITELIST else "received_at"
    sort_col_sql = f"email.{sort_col}"
    order_sql = "ASC" if order.lower() == "asc" else "DESC"

    # Build dynamic WHERE clause
    status_conds, status_params = build_status_filter(
        status, column="email.status", default_exclude=["removed"]
    )
    conditions: list[str] = list(status_conds)
    params: list = list(status_params)

    if account:
        acct_list = [a.strip() for a in account.split(",") if a.strip()]
        if len(acct_list) == 1:
            conditions.append("email.account = ?")
            params.append(acct_list[0])
        elif acct_list:
            placeholders = ",".join("?" for _ in acct_list)
            conditions.append(f"email.account IN ({placeholders})")
            params.extend(acct_list)

    if client_id:
        conditions.append("email.client_id = ?")
        params.append(client_id)

    if project_id:
        conditions.append("email.project_id = ?")
        params.append(project_id)

    if has_attachments is not None:
        if has_attachments:
            conditions.append("email.has_attachments = 1")
        else:
            conditions.append("email.has_attachments = 0")

    fts_join = ""
    if query:
        fts_join = "JOIN emails_fts fts ON fts.rowid = email.id"
        fts_query = f'"{query}"*'
        conditions.append("emails_fts MATCH ?")
        params.append(fts_query)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) FROM emails email {fts_join} {where_clause}"
    fetch_sql = f"""
        SELECT email.id, email.message_id, email.thread_id, email.account,
               email.from_address, email.from_name, email.to_addresses, email.cc_addresses,
               email.subject, email.body_preview, email.received_at,
               email.labels, email.has_attachments, email.is_read, email.status,
               email.client_id, email.project_id,
               client.name AS client_name, project.name AS project_name,
               email.visibility, email.access,
               email.visibility_source, email.access_source
        FROM emails email
        {fts_join}
        LEFT JOIN clients client ON email.client_id = client.id
        LEFT JOIN projects project ON email.project_id = project.id
        {where_clause}
        ORDER BY {sort_col_sql} {order_sql}
        LIMIT ? OFFSET ?
    """
    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    emails = []
    for row in rows:
        emails.append(
            {
                "id": row["id"],
                "message_id": row["message_id"],
                "thread_id": row["thread_id"],
                "account": row["account"] or "unknown",
                "from_address": row["from_address"] or "",
                "from_name": row["from_name"] or "",
                "to_addresses": row["to_addresses"] or "",
                "cc_addresses": row["cc_addresses"] or "",
                "subject": row["subject"] or "(no subject)",
                "body_preview": (row["body_preview"] or "")[:200],
                "received_at": row["received_at"] or "",
                "labels": row["labels"] or "",
                "has_attachments": bool(row["has_attachments"]),
                "is_read": bool(row["is_read"]),
                "status": row["status"] or "listed",
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "visibility": row["visibility"],
                "access": row["access"],
                "visibility_source": row["visibility_source"],
                "access_source": row["access_source"],
            }
        )

    return paginated_response("emails", emails, pagination)


def get_email(conn: sqlite3.Connection, email_id: int) -> Optional[dict]:
    """Get full details for a single email.

    Returns dict or None if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT email.*, client.name AS client_name, project.name AS project_name
        FROM emails email
        LEFT JOIN clients client ON email.client_id = client.id
        LEFT JOIN projects project ON email.project_id = project.id
        WHERE email.id = ?
        """,
        (email_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "thread_id": row["thread_id"],
        "account": row["account"] or "unknown",
        "from_address": row["from_address"] or "",
        "from_name": row["from_name"] or "",
        "to_addresses": row["to_addresses"] or "",
        "cc_addresses": row["cc_addresses"] or "",
        "subject": row["subject"] or "(no subject)",
        "body_preview": row["body_preview"] or "",
        "received_at": row["received_at"] or "",
        "labels": row["labels"] or "",
        "has_attachments": bool(row["has_attachments"]),
        "is_read": bool(row["is_read"]),
        "client_id": row["client_id"],
        "client_name": row["client_name"],
        "project_id": row["project_id"],
        "project_name": row["project_name"],
        "status": row["status"],
        "metadata": row["metadata"] or "",
        "visibility": row["visibility"] or "inherit",
        "access": row["access"] or "inherit",
        "visibility_source": row["visibility_source"],
        "access_source": row["access_source"],
    }


def update_email_relationships(
    conn: sqlite3.Connection,
    email_id: int,
    *,
    project_id: Optional[int] = None,
    client_id: Optional[int] = None,
) -> Optional[bool]:
    """Update project and/or client assignment on an email.

    Only updates fields that are passed (not None). Pass ``0`` to clear
    a field (set to NULL). Stamps ``assignment_source = 'user'``
    when the column exists (app-scope DBs only).
    Returns True on success, None if email not found.
    """
    from footprinter.db.sql_utils import update_entity_relationships

    return update_entity_relationships(
        conn, "emails", email_id, project_id=project_id, client_id=client_id
    )


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def insert_email(conn: sqlite3.Connection, email_data: Dict[str, Any]) -> int:
    """Insert or update an email record, preserving the row id on conflict."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO emails
        (message_id, thread_id, account, from_address, from_name,
         to_addresses, cc_addresses, subject, body_preview, received_at,
         labels, has_attachments, is_read, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id, account) DO UPDATE SET
            thread_id = excluded.thread_id,
            from_address = excluded.from_address,
            from_name = excluded.from_name,
            to_addresses = excluded.to_addresses,
            cc_addresses = excluded.cc_addresses,
            subject = excluded.subject,
            body_preview = excluded.body_preview,
            received_at = excluded.received_at,
            labels = excluded.labels,
            has_attachments = excluded.has_attachments,
            is_read = excluded.is_read,
            metadata = excluded.metadata,
            updated_at = CURRENT_TIMESTAMP
    """,
        (
            email_data["message_id"],
            email_data["thread_id"],
            email_data["account"],
            email_data.get("from_address"),
            email_data.get("from_name"),
            email_data.get("to_addresses"),
            email_data.get("cc_addresses"),
            email_data.get("subject"),
            email_data.get("body_preview"),
            email_data["received_at"],
            email_data.get("labels"),
            email_data.get("has_attachments", False),
            email_data.get("is_read", True),
            json.dumps(email_data.get("metadata", {})),
        ),
    )
    cursor.execute(
        "SELECT id FROM emails WHERE message_id = ? AND account = ?",
        (email_data["message_id"], email_data["account"]),
    )
    return cursor.fetchone()[0]
