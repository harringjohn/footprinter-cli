"""Tests for footprinter.db.status exception handling."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from footprinter.db.status import _safe_count, get_mcp_status, get_system_status

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
        "INSERT INTO folders (path, relative_path, name, source, status) VALUES ('/tmp/a', 'a', 'a', 'local', 'active')"
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
        "VALUES ('ext-active', 'claude', 'active chat', 'active')"
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
