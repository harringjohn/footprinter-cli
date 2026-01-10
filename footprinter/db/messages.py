"""Cross-chat message queries.

Provides list, get, and search functions for messages across all chats.
All functions take sqlite3.Connection and return plain dicts.
"""

import sqlite3
from typing import Optional

from footprinter.db.sql_utils import paginate, paginated_response


def list_messages(
    conn: sqlite3.Connection,
    *,
    role: Optional[str] = None,
    account: Optional[str] = None,
    chat_id: Optional[int] = None,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List messages across chats with optional filters and pagination.

    Parameters
    ----------
    conn : sqlite3.Connection
    role : optional role filter (e.g. 'user', 'assistant')
    account : optional account filter (e.g. 'claude', 'chatgpt')
    chat_id : optional chat ID to filter to a single chat
    limit : max rows per page
    page : 1-based page number

    Returns
    -------
    dict with keys: messages, pagination
    """
    conditions: list[str] = []
    params: list = []

    if role:
        conditions.append("message.role = ?")
        params.append(role)

    if account:
        conditions.append("chat.account = ?")
        params.append(account)

    if chat_id is not None:
        conditions.append("message.chat_id = ?")
        params.append(chat_id)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"""
        SELECT COUNT(*)
        FROM messages message
        JOIN chats chat ON message.chat_id = chat.id
        {where}
    """
    fetch_sql = f"""
        SELECT message.id, message.chat_id, message.message_id, message.role, message.content, message.created_at,
               chat.title AS chat_title, chat.account AS chat_account,
               message.mcp_view, message.mcp_read
        FROM messages message
        JOIN chats chat ON message.chat_id = chat.id
        {where}
        ORDER BY message.id DESC
        LIMIT ? OFFSET ?
    """

    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    messages = [
        {
            "id": r["id"],
            "chat_id": r["chat_id"],
            "message_id": r["message_id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "chat_title": r["chat_title"],
            "chat_account": r["chat_account"],
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]

    return paginated_response("messages", messages, pagination)


def get_message(conn: sqlite3.Connection, message_id: int) -> Optional[dict]:
    """Get a single message with chat context.

    Parameters
    ----------
    conn : sqlite3.Connection
    message_id : internal integer ID

    Returns
    -------
    dict with message fields and chat context, or None if not found
    """
    cursor = conn.execute(
        """
        SELECT message.id, message.chat_id, message.message_id, message.role, message.content, message.created_at,
               chat.title AS chat_title, chat.account AS chat_account,
               message.mcp_view, message.mcp_read
        FROM messages message
        JOIN chats chat ON message.chat_id = chat.id
        WHERE message.id = ?
        """,
        (message_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "chat_title": row["chat_title"],
        "chat_account": row["chat_account"],
        "mcp_view": row["mcp_view"],
        "mcp_read": row["mcp_read"],
    }


def search_messages(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """Search message content across all chats.

    Parameters
    ----------
    conn : sqlite3.Connection
    query : search term
    limit : max results per page
    page : 1-based page number

    Returns
    -------
    dict with keys: results, pagination
    """
    if len(query) < 2:
        return paginated_response("results", [], {"page": page, "limit": limit, "total": 0, "total_pages": 1})

    query_param = f"%{query}%"
    count_sql = """
        SELECT COUNT(*)
        FROM messages message
        JOIN chats chat ON message.chat_id = chat.id
        WHERE message.content LIKE ?
    """
    fetch_sql = """
        SELECT message.id, message.chat_id, message.message_id, message.role, message.content, message.created_at,
               chat.title AS chat_title, chat.account AS chat_account,
               message.mcp_view, message.mcp_read
        FROM messages message
        JOIN chats chat ON message.chat_id = chat.id
        WHERE message.content LIKE ?
        ORDER BY message.id DESC
        LIMIT ? OFFSET ?
    """

    rows, pagination = paginate(conn, count_sql, fetch_sql, [query_param], page=page, limit=limit)

    results = [
        {
            "id": r["id"],
            "chat_id": r["chat_id"],
            "message_id": r["message_id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
            "chat_title": r["chat_title"],
            "chat_account": r["chat_account"],
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]

    return paginated_response("results", results, pagination)
