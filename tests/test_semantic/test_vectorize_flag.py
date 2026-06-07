"""Tests for per-record vectorization control via metadata flag.

Validates that metadata.vectorize=0 causes records to be skipped in the
shared vectorization helpers (_vectorize_messages, _vectorize_chat_info).
File-ingest coverage is in
tests/test_ingest/test_processing.py::test_skips_metadata_vectorize_zero.
"""

import json
import sqlite3
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rebuild_db(tmp_path):
    """Create a minimal DB with chats + messages tables for rebuild tests."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute("""
        CREATE TABLE chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            title TEXT,
            account TEXT DEFAULT 'test',
            created_at TEXT,
            message_count INTEGER DEFAULT 0,
            metadata TEXT,
            metadata_vectorized_at TEXT,
            status TEXT DEFAULT 'listed',
            vectorize INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT DEFAULT 'user',
            content TEXT,
            created_at TEXT,
            metadata TEXT,
            vectorized_at TEXT,
            vectorized_chunks INTEGER,
            status TEXT DEFAULT 'listed',
            vectorize INTEGER DEFAULT 1,
            FOREIGN KEY (chat_id) REFERENCES chats(id)
        )
    """)
    return conn


def _make_mock_store():
    """Create a mock VectorStore with batch-compatible methods."""
    store = MagicMock()
    store.ef.return_value = [[0.1] * 384]  # fake embeddings
    store._chats = MagicMock()
    return store


# ---------------------------------------------------------------------------
# RED 1: _vectorize_messages() respects metadata.vectorize flag
# ---------------------------------------------------------------------------


class TestRebuildMessagesRespectsFlag:
    """_vectorize_messages() should skip messages with metadata.vectorize=0."""

    def test_flagged_message_skipped(self, tmp_path):
        conn = _make_rebuild_db(tmp_path)

        # Chat to hold messages
        conn.execute("INSERT INTO chats (id, external_id, title, account) VALUES (1, 'chat-1', 'Test Chat', 'test')")

        # Message 1: normal (should be vectorized)
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at) "
            "VALUES (1, 1, 'user', 'Include me in vectors', '2026-01-01')"
        )
        # Message 2: flagged with vectorize=0 (should be skipped)
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at, metadata, vectorize) "
            "VALUES (2, 1, 'assistant', 'Exclude me from vectors', '2026-01-02', ?, 0)",
            (json.dumps({"vectorize": 0}),),
        )
        conn.commit()

        store = _make_mock_store()
        cursor = conn.cursor()

        from footprinter.ingest.vector_ops import _vectorize_messages

        result = _vectorize_messages(conn, cursor, store, console=None)

        # Only 1 message should be vectorized (the unflagged one)
        assert result["done"] == 1

        # Verify msg 2 was NOT updated with vectorized_at
        cursor.execute("SELECT vectorized_at FROM messages WHERE id = 2")
        assert cursor.fetchone()["vectorized_at"] is None

        conn.close()

    def test_unflagged_message_included(self, tmp_path):
        """Messages without the flag (or with vectorize=1) should be vectorized."""
        conn = _make_rebuild_db(tmp_path)
        conn.execute("INSERT INTO chats (id, external_id, title, account) VALUES (1, 'chat-1', 'Test Chat', 'test')")
        # Message with explicit vectorize=1
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at, metadata) "
            "VALUES (1, 1, 'user', 'Include me', '2026-01-01', ?)",
            (json.dumps({"vectorize": 1}),),
        )
        # Message with no metadata at all
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at) "
            "VALUES (2, 1, 'assistant', 'Also include me', '2026-01-02')"
        )
        conn.commit()

        store = _make_mock_store()
        cursor = conn.cursor()

        from footprinter.ingest.vector_ops import _vectorize_messages

        result = _vectorize_messages(conn, cursor, store, console=None)

        assert result["done"] == 2
        conn.close()


# ---------------------------------------------------------------------------
# RED 2: _vectorize_chat_info() respects metadata.vectorize flag
# ---------------------------------------------------------------------------


class TestRebuildChatInfoRespectsFlag:
    """_vectorize_chat_info() should skip chats with metadata.vectorize=0."""

    def test_flagged_chat_skipped(self, tmp_path):
        conn = _make_rebuild_db(tmp_path)

        # Chat 1: normal
        conn.execute(
            "INSERT INTO chats (id, external_id, title, account, message_count) "
            "VALUES (1, 'chat-1', 'Normal Chat', 'test', 5)"
        )
        # Chat 2: flagged
        conn.execute(
            "INSERT INTO chats (id, external_id, title, account, message_count, metadata, vectorize) "
            "VALUES (2, 'chat-2', 'Flagged Chat', 'test', 3, ?, 0)",
            (json.dumps({"vectorize": 0}),),
        )
        conn.commit()

        store = _make_mock_store()
        cursor = conn.cursor()

        from footprinter.ingest.vector_ops import _vectorize_chat_info

        result = _vectorize_chat_info(conn, cursor, store, console=None)

        # Only chat 1 should be vectorized
        assert result["done"] == 1

        # Chat 2 should NOT have metadata_vectorized_at set
        cursor.execute("SELECT metadata_vectorized_at FROM chats WHERE id = 2")
        assert cursor.fetchone()["metadata_vectorized_at"] is None

        conn.close()


