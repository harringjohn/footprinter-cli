"""Chat queries, write operations, and duplicate detection."""

import hashlib
import json
import sqlite3
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response

SORT_WHITELIST = {"title", "account", "message_count", "created_at", "modified_at"}


def list_chats(
    conn: sqlite3.Connection,
    *,
    account: Optional[str] = None,
    query: Optional[str] = None,
    sort_by: str = "modified_at",
    order: str = "desc",
    limit: int = 50,
    page: int = 1,
    status: Optional[str | list[str]] = None,
) -> dict:
    """List chats with filtering, sorting, and pagination.

    Parameters
    ----------
    conn : sqlite3.Connection
    account : optional account filter (e.g. 'claude', 'chatgpt')
    query : optional title search (LIKE match)
    sort_by : column to sort by (from SORT_WHITELIST)
    order : 'asc' or 'desc'
    limit : max rows per page
    page : 1-based page number
    status : str, list[str], or None
        ``None`` → exclude removed (default).
        ``"all"`` → no status filter.
        Single string → exact match.
        List of strings → ``WHERE status IN (...)``.

    Returns
    -------
    dict with keys: chats, pagination
    """
    sort_col = sort_by if sort_by in SORT_WHITELIST else "modified_at"
    sort_col_sql = f"chat.{sort_col}"
    order_sql = "ASC" if order.lower() == "asc" else "DESC"

    conditions: list[str] = []
    params: list = []

    status_conds, status_params = build_status_filter(
        status,
        column="chat.status",
        default_exclude=["removed"],
    )
    conditions.extend(status_conds)
    params.extend(status_params)

    if account:
        conditions.append("chat.account = ?")
        params.append(account)

    if query:
        conditions.append("chat.title LIKE ?")
        params.append(f"%{query}%")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) FROM chats chat {where}"
    fetch_sql = f"""
        SELECT chat.id, chat.external_id, chat.account, chat.title, chat.message_count,
               chat.created_at, chat.modified_at, chat.status, chat.merged_into_id,
               chat.mcp_view, chat.mcp_read
        FROM chats chat
        {where}
        ORDER BY {sort_col_sql} {order_sql}
        LIMIT ? OFFSET ?
    """
    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    chats = [
        {
            "id": r["id"],
            "external_id": r["external_id"],
            "account": r["account"],
            "title": r["title"],
            "message_count": r["message_count"],
            "created_at": r["created_at"],
            "modified_at": r["modified_at"],
            "status": r["status"],
            "merged_into_id": r["merged_into_id"],
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]

    return paginated_response("chats", chats, pagination)


def get_chat_detail(
    conn: sqlite3.Connection,
    chat_id: int,
) -> Optional[dict]:
    """Return chat metadata and messages for a single chat.

    Parameters
    ----------
    conn : sqlite3.Connection
    chat_id : internal integer ID

    Returns
    -------
    dict with chat fields at top level and ``messages`` list, or None if not found
    """
    cursor = conn.execute(
        """
        SELECT chat.id, chat.external_id, chat.account, chat.title,
               chat.message_count,
               chat.created_at, chat.modified_at, chat.status, chat.merged_into_id,
               chat.client_id, chat.project_id,
               chat.mcp_view, chat.mcp_read,
               project.project_name, client.name AS client_name
        FROM chats chat
        LEFT JOIN projects project ON chat.project_id = project.id
        LEFT JOIN clients client ON chat.client_id = client.id
        WHERE chat.id = ?
        """,
        (chat_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    chat = {
        "id": row["id"],
        "external_id": row["external_id"],
        "account": row["account"],
        "title": row["title"],
        "message_count": row["message_count"],
        "created_at": row["created_at"],
        "modified_at": row["modified_at"],
        "status": row["status"],
        "merged_into_id": row["merged_into_id"],
        "client_id": row["client_id"],
        "project_id": row["project_id"],
        "project_name": row["project_name"],
        "client_name": row["client_name"],
        "mcp_view": row["mcp_view"] or "inherit",
        "mcp_read": row["mcp_read"] or "inherit",
    }

    msg_cursor = conn.execute(
        """
        SELECT id, chat_id, message_id, role, content, created_at
        FROM messages
        WHERE chat_id = ?
        ORDER BY id
        """,
        (chat_id,),
    )
    messages = [
        {
            "id": r["id"],
            "chat_id": r["chat_id"],
            "message_id": r["message_id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"],
        }
        for r in msg_cursor.fetchall()
    ]

    chat["messages"] = messages
    return chat


def update_chat_relationships(
    conn: sqlite3.Connection,
    chat_id: int,
    *,
    project_id: Optional[int] = None,
    client_id: Optional[int] = None,
) -> Optional[bool]:
    """Update project and/or client assignment on a chat.

    Only updates fields that are passed (not None). Pass ``0`` to clear
    a field (set to NULL). Stamps ``assignment_source = 'user'``
    when the column exists (app-scope DBs only).
    Returns True on success, None if chat not found.
    """
    from footprinter.db.sql_utils import update_entity_relationships

    return update_entity_relationships(
        conn, "chats", chat_id, project_id=project_id, client_id=client_id
    )


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

_FUZZY_THRESHOLD = 0.85
_MESSAGE_OVERLAP_THRESHOLD = 0.50


def _get_active_chats(conn: sqlite3.Connection) -> list[dict]:
    """All non-removed chats for dedup scan."""
    rows = conn.execute(
        "SELECT id, external_id, account, title, message_count,"
        "       created_at, modified_at"
        " FROM chats"
        " WHERE status = 'listed'"
        " ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _get_message_hashes(conn: sqlite3.Connection, chat_id: int) -> list[str]:
    """SHA-256 content hashes for a chat's messages."""
    rows = conn.execute(
        "SELECT content FROM messages WHERE chat_id = ? ORDER BY id",
        (chat_id,),
    ).fetchall()
    return [hashlib.sha256((r["content"] or "").encode("utf-8")).hexdigest() for r in rows]


def _normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return title.strip().lower()


def detect_duplicates(
    conn: sqlite3.Connection,
    *,
    fuzzy_threshold: float = _FUZZY_THRESHOLD,
    overlap_threshold: float = _MESSAGE_OVERLAP_THRESHOLD,
) -> list[dict]:
    """Detect potential duplicate chats via three passes.

    1. Exact title match (normalized)
    2. Fuzzy title match (SequenceMatcher >= threshold)
    3. Message content overlap (SHA-256 hash intersection)

    Returns list of dicts with keys: reason, confidence, chats, detail.
    """
    active_chats = _get_active_chats(conn)
    if len(active_chats) < 2:
        return []

    groups: list[dict] = []
    paired: set[tuple[int, int]] = set()
    hash_cache: dict[int, list[str]] = {}

    def _get_hashes(chat_id: int) -> list[str]:
        if chat_id not in hash_cache:
            hash_cache[chat_id] = _get_message_hashes(conn, chat_id)
        return hash_cache[chat_id]

    # Pass 1: Exact title
    by_title: dict[str, list[dict]] = defaultdict(list)
    for conv in active_chats:
        norm = _normalize_title(conv["title"])
        if norm:
            by_title[norm].append(conv)

    for title, convs in by_title.items():
        if len(convs) >= 2:
            groups.append(
                {
                    "reason": "exact_title",
                    "confidence": "high",
                    "chats": convs,
                    "detail": f'Title: "{convs[0]["title"]}"',
                }
            )
            for i in range(len(convs)):
                for j in range(i + 1, len(convs)):
                    pair = (min(convs[i]["id"], convs[j]["id"]), max(convs[i]["id"], convs[j]["id"]))
                    paired.add(pair)

    # Pass 2: Fuzzy title
    for i in range(len(active_chats)):
        for j in range(i + 1, len(active_chats)):
            a, b = active_chats[i], active_chats[j]
            pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
            if pair in paired:
                continue
            title_a = _normalize_title(a["title"])
            title_b = _normalize_title(b["title"])
            if not title_a or not title_b:
                continue
            ratio = SequenceMatcher(None, title_a, title_b).ratio()
            if ratio >= fuzzy_threshold:
                groups.append(
                    {
                        "reason": "fuzzy_title",
                        "confidence": "medium",
                        "chats": [a, b],
                        "detail": f'"{a["title"]}" ≈ "{b["title"]}" ({ratio:.0%})',
                    }
                )
                paired.add(pair)

    # Pass 3: Message overlap (same account only)
    by_account: dict[str, list[dict]] = defaultdict(list)
    for conv in active_chats:
        by_account[conv["account"]].append(conv)

    for account, convs in by_account.items():
        for i in range(len(convs)):
            for j in range(i + 1, len(convs)):
                a, b = convs[i], convs[j]
                pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if pair in paired:
                    continue
                hashes_a = set(_get_hashes(a["id"]))
                hashes_b = set(_get_hashes(b["id"]))
                if not hashes_a or not hashes_b:
                    continue
                intersection = hashes_a & hashes_b
                min_count = min(len(hashes_a), len(hashes_b))
                overlap = len(intersection) / min_count
                if overlap >= overlap_threshold:
                    groups.append(
                        {
                            "reason": "message_overlap",
                            "confidence": "high",
                            "chats": [a, b],
                            "detail": (f"{len(intersection)} shared messages ({overlap:.0%} overlap)"),
                        }
                    )
                    paired.add(pair)

    return groups


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def insert_chat(conn: sqlite3.Connection, conv_data: Dict[str, Any]) -> int:
    """Insert or update a chat record, preserving the row id on conflict.

    ``status`` and ``indexed_at`` come from schema DEFAULTs (``'listed'`` and
    ``CURRENT_TIMESTAMP``) for new rows. An explicit ``status`` in ``conv_data``
    is honored on insert. On conflict, ``status`` and ``indexed_at`` are
    preserved — a re-import must not reset a user-set ``'unlisted'`` or bump
    the first-seen timestamp.
    """
    cursor = conn.cursor()
    columns = [
        "external_id",
        "account",
        "title",
        "created_at",
        "modified_at",
        "message_count",
        "metadata",
    ]
    params: list = [
        conv_data["external_id"],
        conv_data.get("account"),
        conv_data.get("title"),
        conv_data.get("created_at"),
        conv_data.get("updated_at"),
        conv_data.get("message_count", 0),
        json.dumps(conv_data.get("metadata", {})),
    ]
    if "status" in conv_data:
        columns.append("status")
        params.append(conv_data["status"])

    placeholders = ", ".join(["?"] * len(params))
    cursor.execute(
        f"""
        INSERT INTO chats ({', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(external_id) DO UPDATE SET
            account = excluded.account,
            title = excluded.title,
            created_at = excluded.created_at,
            modified_at = excluded.modified_at,
            message_count = excluded.message_count,
            metadata = excluded.metadata,
            updated_at = CURRENT_TIMESTAMP
        """,
        params,
    )
    cursor.execute(
        "SELECT id FROM chats WHERE external_id = ?",
        (conv_data["external_id"],),
    )
    return cursor.fetchone()[0]


def insert_message(conn: sqlite3.Connection, msg_data: Dict[str, Any]) -> int:
    """Insert a chat message record."""
    cursor = conn.cursor()
    columns = [
        "chat_id", "message_id", "role", "content", "created_at", "metadata",
        "status",
    ]
    params: list = [
        msg_data["chat_id"],
        msg_data.get("message_id"),
        msg_data["role"],
        msg_data.get("content"),
        msg_data.get("created_at"),
        json.dumps(msg_data.get("metadata", {})),
        msg_data.get("status", "listed"),
    ]

    columns.extend(["indexed_at", "updated_at"])
    value_parts = ["?"] * len(params) + ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]
    cursor.execute(
        f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join(value_parts)})",
        params,
    )
    return cursor.lastrowid


def get_chat_id_by_uuid(conn: sqlite3.Connection, chat_uuid: str) -> Optional[int]:
    """Get internal chat ID by external UUID."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM chats WHERE external_id = ?", (chat_uuid,))
    row = cursor.fetchone()
    return row["id"] if row else None


def delete_chat_messages(conn: sqlite3.Connection, chat_id: int) -> int:
    """Delete all messages for a chat (before re-import)."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    return cursor.rowcount


def get_all_active_chats(conn: sqlite3.Connection) -> List[Dict]:
    """All non-removed chats. Use ``detect_duplicates`` for dedup."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, external_id, account, title, message_count,
               created_at, modified_at
        FROM chats
        WHERE status = 'listed'
        ORDER BY id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def get_chat_message_hashes(conn: sqlite3.Connection, chat_id: int) -> List[str]:
    """SHA-256 content hashes for overlap detection."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT content FROM messages WHERE chat_id = ? ORDER BY id",
        (chat_id,),
    )
    return [hashlib.sha256((row["content"] or "").encode("utf-8")).hexdigest() for row in cursor.fetchall()]


def get_chat_messages(conn: sqlite3.Connection, chat_id: int) -> List[Dict]:
    """All messages for a chat."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, chat_id, message_id, role, content, created_at
        FROM messages
        WHERE chat_id = ?
        ORDER BY id
        """,
        (chat_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_chat_by_id(conn: sqlite3.Connection, chat_id: int) -> Optional[Dict]:
    """Single chat lookup by internal ID."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, external_id, account, title, message_count,
               created_at, modified_at, status, merged_into_id
        FROM chats
        WHERE id = ?
        """,
        (chat_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def move_messages_to_chat(conn: sqlite3.Connection, source_id: int, target_id: int, message_ids: List[int]) -> int:
    """Move specific messages from source to target chat."""
    if not message_ids:
        return 0
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in message_ids)
    cursor.execute(
        f"""
        UPDATE messages
        SET chat_id = ?
        WHERE id IN ({placeholders}) AND chat_id = ?
        """,
        [target_id] + message_ids + [source_id],
    )
    return cursor.rowcount


def update_chat_message_count(conn: sqlite3.Connection, chat_id: int) -> int:
    """Recount messages from messages table and update chat record."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id = ?",
        (chat_id,),
    )
    count = cursor.fetchone()[0]
    cursor.execute(
        "UPDATE chats SET message_count = ? WHERE id = ?",
        (count, chat_id),
    )
    return count


def list_chats_simple(
    conn: sqlite3.Connection,
    account: Optional[str] = None,
    limit: int = 50,
    status: Optional[str | list[str]] = None,
) -> List[Dict]:
    """List chats with optional account filter, excludes removed by default.

    Returns a flat list of chat dicts (unlike the paginated ``list_chats``
    used by the read API).
    """
    cursor = conn.cursor()
    conditions: list[str] = []
    params: list = []
    if status is None:
        conditions.append("status = 'listed'")
    elif status == "all":
        pass
    elif isinstance(status, list) and status:
        placeholders = ",".join("?" for _ in status)
        conditions.append(f"status IN ({placeholders})")
        params.extend(status)
    else:
        conditions.append("status = ?")
        params.append(status)
    if account:
        conditions.append("account = ?")
        params.append(account)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    cursor.execute(
        f"""
        SELECT id, external_id, account, title, message_count,
               created_at, modified_at, status, merged_into_id
        FROM chats
        {where}
        ORDER BY modified_at DESC
        LIMIT ?
        """,
        params,
    )
    return [dict(row) for row in cursor.fetchall()]
