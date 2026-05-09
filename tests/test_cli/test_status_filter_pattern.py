"""Tests for consistent status filtering across chat query layers.

Verifies that list_chats and _get_active_chats use the same bare
status != 'removed' / NOT IN pattern (no IS NULL guard).
"""

import sqlite3

import pytest


@pytest.fixture
def conn():
    """In-memory SQLite with chats schema including mcp columns."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE chats ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  external_id TEXT UNIQUE NOT NULL,"
        "  account TEXT NOT NULL,"
        "  title TEXT,"
        "  summary TEXT,"
        "  created_at DATETIME,"
        "  modified_at DATETIME,"
        "  updated_at DATETIME,"
        "  message_count INTEGER DEFAULT 0,"
        "  indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
        "  metadata TEXT,"
        "  metadata_vectorized_at DATETIME,"
        "  status TEXT DEFAULT 'listed',"
        "  mcp_read TEXT DEFAULT 'inherit',"
        "  mcp_view TEXT DEFAULT 'inherit',"
        "  client_id INTEGER,"
        "  assignment_source TEXT,"
        "  project_id INTEGER,"
        "  merged_into_id INTEGER,"
        "  status_reason TEXT"
        ")"
    )
    db.commit()
    return db


def _insert(conn, chat_id, title="Chat", account="claude", status="listed"):
    conn.execute(
        "INSERT INTO chats (id, external_id, account, title, status) VALUES (?, ?, ?, ?, ?)",
        (chat_id, f"ext-{chat_id}", account, title, status),
    )
    conn.commit()


class TestListChatsStatusFilter:
    """list_chats with default status=None excludes removed and NULL."""

    def test_includes_active(self, conn):
        from footprinter.db.chats import list_chats

        _insert(conn, 1, "Active chat", status="listed")
        result = list_chats(conn)
        assert len(result["chats"]) == 1
        assert result["chats"][0]["title"] == "Active chat"

    def test_excludes_removed(self, conn):
        from footprinter.db.chats import list_chats

        _insert(conn, 1, "Active chat", status="listed")
        _insert(conn, 2, "Removed chat", status="removed")
        result = list_chats(conn)
        titles = [c["title"] for c in result["chats"]]
        assert "Active chat" in titles
        assert "Removed chat" not in titles

    def test_excludes_null_status(self, conn):
        """NULL status should be excluded by default — not silently included."""
        from footprinter.db.chats import list_chats

        _insert(conn, 1, "Active chat", status="listed")
        # Force NULL status to bypass the DEFAULT
        conn.execute(
            "INSERT INTO chats (id, external_id, account, title, status) VALUES (?, ?, ?, ?, ?)",
            (2, "ext-2", "claude", "Null chat", None),
        )
        conn.commit()

        result = list_chats(conn)
        titles = [c["title"] for c in result["chats"]]
        assert "Active chat" in titles
        assert "Null chat" not in titles

    def test_status_all_includes_null(self, conn):
        """status='all' bypasses all filtering, including NULL rows."""
        from footprinter.db.chats import list_chats

        _insert(conn, 1, "Active chat", status="listed")
        conn.execute(
            "INSERT INTO chats (id, external_id, account, title, status) VALUES (?, ?, ?, ?, ?)",
            (2, "ext-2", "claude", "Null chat", None),
        )
        conn.commit()

        result = list_chats(conn, status="all")
        titles = [c["title"] for c in result["chats"]]
        assert "Active chat" in titles
        assert "Null chat" in titles


class TestGetActiveChatsStatusFilter:
    """_get_active_chats excludes merged, removed, and NULL."""

    def test_excludes_null_status(self, conn):
        """NULL status should be excluded from dedup scan."""
        from footprinter.db.chats import _get_active_chats

        _insert(conn, 1, "Active chat", status="listed")
        conn.execute(
            "INSERT INTO chats (id, external_id, account, title, status) VALUES (?, ?, ?, ?, ?)",
            (2, "ext-2", "claude", "Null chat", None),
        )
        conn.commit()

        chats = _get_active_chats(conn)
        titles = [c["title"] for c in chats]
        assert "Active chat" in titles
        assert "Null chat" not in titles
