"""Tests for footprinter.db.status exception handling."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from footprinter.db.status import (
    _safe_count,
    get_entity_status_breakdown,
    get_mcp_status,
    get_system_status,
)

# --- _safe_count ---


def test_safe_count_returns_zero_on_operational_error():
    """Query against nonexistent table returns 0."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    result = _safe_count(cur, "SELECT COUNT(*) FROM nonexistent_table")
    assert result == 0
    conn.close()


def test_safe_count_propagates_non_operational_error():
    """Non-sqlite errors (e.g. TypeError) must propagate, not be swallowed."""
    cur = MagicMock()
    cur.execute.side_effect = TypeError("unexpected type")
    with pytest.raises(TypeError, match="unexpected type"):
        _safe_count(cur, "SELECT COUNT(*) FROM files")


# --- get_system_status ---


def test_get_system_status_returns_none_last_indexed_on_op_error():
    """DB without files table returns last_indexed=None."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    config_path = Path("/nonexistent/config.yaml")
    status = get_system_status(conn, config_path)
    assert status["last_indexed"] is None
    assert status["total"] == 0
    conn.close()


def test_get_system_status_propagates_non_operational_error():
    """Non-sqlite errors on the MAX query must propagate."""
    # Build a mock cursor that raises ValueError only on the MAX query
    call_count = 0

    def execute_side_effect(sql, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "MAX(indexed_at)" in sql:
            raise ValueError("simulated bug")
        # Return a row with count 0 for all _safe_count calls
        return None

    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = execute_side_effect
    mock_cursor.fetchone.return_value = (0,)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    config_path = Path("/nonexistent/config.yaml")
    with pytest.raises(ValueError, match="simulated bug"):
        get_system_status(mock_conn, config_path)


# --- folder status filter ---


def test_get_system_status_excludes_removed_folders():
    """Folders with status='removed' must not be counted."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO folders (path, relative_path, name, source, status) VALUES ('/tmp/a', 'a', 'a', 'local', 'listed')"
    )
    conn.execute(
        "INSERT INTO folders (path, relative_path, name, source, status) "
        "VALUES ('/tmp/b', 'b', 'b', 'local', 'removed')"
    )
    conn.commit()

    config_path = Path("/nonexistent/config.yaml")
    status = get_system_status(conn, config_path)
    assert status["counts"]["folders"] == 1
    conn.close()


# --- MCP chat filter NULL-safety ---


def test_get_mcp_status_counts_null_status_chats():
    """Legacy chats with status=NULL must be counted by MCP, matching the
    intent 'not removed'. Bare ``status != 'removed'`` fails on NULL."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO chats (external_id, account, title, status) "
        "VALUES ('ext-active', 'claude', 'active chat', 'listed')"
    )
    conn.execute(
        "INSERT INTO chats (external_id, account, title) "
        "VALUES ('ext-null', 'claude', 'legacy chat')"
    )
    conn.execute("UPDATE chats SET status = NULL WHERE external_id = 'ext-null'")
    conn.commit()

    status = get_mcp_status(conn)
    assert status["sources"]["chats"]["count"] == 2
    conn.close()


# --- MCP messages status filter ---


def test_get_mcp_status_excludes_removed_messages():
    """Messages with status='removed' must not be counted by MCP, matching
    the chats/emails/browser pattern."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO chats (external_id, account, title, status) "
        "VALUES ('chat-1', 'claude', 'parent chat', 'listed')"
    )
    chat_id = conn.execute(
        "SELECT id FROM chats WHERE external_id = 'chat-1'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, status) "
        "VALUES (?, 'user', 'kept', 'listed')",
        (chat_id,),
    )
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, status) "
        "VALUES (?, 'user', 'dropped', 'removed')",
        (chat_id,),
    )
    conn.commit()

    status = get_mcp_status(conn)
    assert status["sources"]["messages"]["count"] == 1
    conn.close()


def test_get_mcp_status_counts_null_status_messages():
    """Legacy messages with status=NULL must be counted by MCP, matching the
    chats COALESCE pattern. Bare ``status != 'removed'`` fails on NULL."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO chats (external_id, account, title, status) "
        "VALUES ('chat-1', 'claude', 'parent chat', 'listed')"
    )
    chat_id = conn.execute(
        "SELECT id FROM chats WHERE external_id = 'chat-1'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, status) "
        "VALUES (?, 'user', 'active', 'listed')",
        (chat_id,),
    )
    conn.execute(
        "INSERT INTO messages (chat_id, role, content) "
        "VALUES (?, 'user', 'legacy')",
        (chat_id,),
    )
    conn.execute(
        "UPDATE messages SET status = NULL WHERE content = 'legacy'"
    )
    conn.commit()

    status = get_mcp_status(conn)
    assert status["sources"]["messages"]["count"] == 2
    conn.close()


# --- get_entity_status_breakdown -------------------------------------------

_ENTITY_ORDER = (
    "clients",
    "projects",
    "folders",
    "files",
    "chats",
    "messages",
    "emails",
    "visits",
)


def test_entity_status_breakdown_basic():
    """Returns per-entity total and by_status dict; totals match by_status sums."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO clients (name, slug, client_type, status) "
        "VALUES ('Acme', 'acme', 'external', 'listed')"
    )
    conn.execute(
        "INSERT INTO projects (name, status) "
        "VALUES ('Alpha', 'listed')"
    )
    conn.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('a.md', '/tmp/a.md', 'local', 'listed', 'markdown', 100)"
    )
    conn.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('b.md', '/tmp/b.md', 'local', 'removed', 'markdown', 200)"
    )
    conn.commit()

    breakdown = get_entity_status_breakdown(conn)

    assert tuple(breakdown.keys()) == _ENTITY_ORDER
    for entity, info in breakdown.items():
        assert set(info.keys()) == {"total", "by_status"}
        assert info["total"] == sum(info["by_status"].values())

    assert breakdown["clients"]["total"] == 1
    assert breakdown["projects"]["total"] == 1
    assert breakdown["files"]["total"] == 2
    assert breakdown["files"]["by_status"] == {"listed": 1, "removed": 1}
    conn.close()


def test_entity_status_breakdown_columns_data_driven():
    """by_status contains only statuses present in data, not zero-padded."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('a.md', '/tmp/a.md', 'local', 'listed', 'markdown', 100)"
    )
    conn.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('b.md', '/tmp/b.md', 'local', 'removed', 'markdown', 200)"
    )
    conn.commit()

    breakdown = get_entity_status_breakdown(conn)

    assert set(breakdown["files"]["by_status"].keys()) == {"listed", "removed"}
    assert "unlisted" not in breakdown["files"]["by_status"]
    conn.close()


def test_entity_status_breakdown_surfaces_legacy_values():
    """Legacy 'active'/'hidden' values appear in by_status when present.

    Schema CHECK constraints reject these values now, so we build raw tables
    matching the schema shape but without the constraint to simulate a
    pre-constraint database.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO files (status) VALUES ('active'), ('listed')")
    conn.execute("INSERT INTO folders (status) VALUES ('hidden'), ('listed')")
    conn.commit()

    breakdown = get_entity_status_breakdown(conn)

    assert breakdown["files"]["by_status"].get("active") == 1
    assert breakdown["files"]["by_status"].get("listed") == 1
    assert breakdown["folders"]["by_status"].get("hidden") == 1
    assert breakdown["folders"]["by_status"].get("listed") == 1
    conn.close()


def test_entity_status_breakdown_coalesces_null_status():
    """NULL status rows are bucketed as 'listed' (matches MCP query convention)."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn

    conn.execute(
        "INSERT INTO chats (external_id, account, title, status) "
        "VALUES ('ext-listed', 'claude', 'visible', 'listed')"
    )
    conn.execute(
        "INSERT INTO chats (external_id, account, title) "
        "VALUES ('ext-null', 'claude', 'legacy')"
    )
    conn.execute("UPDATE chats SET status = NULL WHERE external_id = 'ext-null'")
    conn.commit()

    breakdown = get_entity_status_breakdown(conn)

    assert breakdown["chats"]["total"] == 2
    assert breakdown["chats"]["by_status"]["listed"] == 2
    assert None not in breakdown["chats"]["by_status"]
    conn.close()


def test_entity_status_breakdown_missing_table():
    """Returns {total: 0, by_status: {}} for missing tables, not raise."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    breakdown = get_entity_status_breakdown(conn)

    assert tuple(breakdown.keys()) == _ENTITY_ORDER
    for entity, info in breakdown.items():
        assert info == {"total": 0, "by_status": {}}
    conn.close()
