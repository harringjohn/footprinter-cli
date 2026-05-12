"""Tests for per-record vectorization control via metadata flag.

Validates that metadata.vectorize=0 causes records to be skipped across
the chat vectorization paths: rebuild (cli.py) and ingest-time
(chat_indexer.py). File-ingest coverage moved to
tests/test_ingest/test_processing.py::test_skips_metadata_vectorize_zero
when FPR-1721 split file vectorization into its own follow-up stage.
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
            summary TEXT,
            account TEXT DEFAULT 'test',
            created_at TEXT,
            message_count INTEGER DEFAULT 0,
            metadata TEXT,
            metadata_vectorized_at TEXT,
            status TEXT DEFAULT 'listed'
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
            "INSERT INTO messages (id, chat_id, role, content, created_at, metadata) "
            "VALUES (2, 1, 'assistant', 'Exclude me from vectors', '2026-01-02', ?)",
            (json.dumps({"vectorize": 0}),),
        )
        conn.commit()

        store = _make_mock_store()
        cursor = conn.cursor()

        from footprinter.ingest.cli import _vectorize_messages

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

        from footprinter.ingest.cli import _vectorize_messages

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
            "INSERT INTO chats (id, external_id, title, account, message_count, metadata) "
            "VALUES (2, 'chat-2', 'Flagged Chat', 'test', 3, ?)",
            (json.dumps({"vectorize": 0}),),
        )
        conn.commit()

        store = _make_mock_store()
        cursor = conn.cursor()

        from footprinter.ingest.cli import _vectorize_chat_info

        result = _vectorize_chat_info(conn, cursor, store, console=None)

        # Only chat 1 should be vectorized
        assert result["done"] == 1

        # Chat 2 should NOT have metadata_vectorized_at set
        cursor.execute("SELECT metadata_vectorized_at FROM chats WHERE id = 2")
        assert cursor.fetchone()["metadata_vectorized_at"] is None

        conn.close()


# ---------------------------------------------------------------------------
# ChatIndexer._vectorize_message() respects metadata.vectorize flag
#
# (The FileIndexer equivalent was removed when vectorization moved out of
# file ingest in FPR-1721. Coverage is now in
# tests/test_ingest/test_processing.py::test_skips_metadata_vectorize_zero.)
# ---------------------------------------------------------------------------


class TestChatIndexerRespectsFlag:
    """ChatIndexer._vectorize_message() should skip messages with metadata.vectorize=0."""

    def test_flagged_message_skipped(self, tmp_path):
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.conn.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, adapter, label, icon, enabled) "
            "VALUES ('local', 'file', 'local_fs', 'Local', 'folder', 1)"
        )
        # Insert a chat and flagged message
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title, message_count) "
            "VALUES (1, 'chat-1', 'test', 'Test Chat', 1)"
        )
        db.conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, metadata) VALUES (1, 1, 'user', 'Exclude this', ?)",
            (json.dumps({"vectorize": 0}),),
        )
        db.conn.commit()

        mock_store = MagicMock()

        with patch("footprinter.ingest.chat_indexer._chat_vectorization_enabled", return_value=True):
            from footprinter.ingest.chat_indexer import ChatIndexer

            indexer = ChatIndexer(db)
            indexer._vector_store = mock_store

            msg = {"content": "Exclude this", "role": "user", "created_at": "2026-01-01"}
            conv_data = {"source": "test", "title": "Test Chat"}
            indexer._vectorize_message(1, 1, msg, conv_data)

        mock_store.upsert_chat_message.assert_not_called()

        db.close()

    def test_unflagged_message_vectorized(self, tmp_path):
        """Messages without the flag should be vectorized normally."""
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.conn.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, adapter, label, icon, enabled) "
            "VALUES ('local', 'file', 'local_fs', 'Local', 'folder', 1)"
        )
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title, message_count) "
            "VALUES (1, 'chat-1', 'test', 'Test Chat', 1)"
        )
        db.conn.execute("INSERT INTO messages (id, chat_id, role, content) VALUES (1, 1, 'user', 'Include this')")
        db.conn.commit()

        mock_store = MagicMock()

        with patch("footprinter.ingest.chat_indexer._chat_vectorization_enabled", return_value=True):
            from footprinter.ingest.chat_indexer import ChatIndexer

            indexer = ChatIndexer(db)
            indexer._vector_store = mock_store

            msg = {"content": "Include this", "role": "user", "created_at": "2026-01-01"}
            conv_data = {"source": "test", "title": "Test Chat"}
            indexer._vectorize_message(1, 1, msg, conv_data)

        mock_store.upsert_chat_message.assert_called_once()

        db.close()


class TestChatIndexerChatInfoRespectsFlag:
    """ChatIndexer._vectorize_chat_info() should skip chats with metadata.vectorize=0."""

    def test_flagged_chat_info_skipped(self, tmp_path):
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.conn.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, adapter, label, icon, enabled) "
            "VALUES ('local', 'file', 'local_fs', 'Local', 'folder', 1)"
        )
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title, message_count, metadata) "
            "VALUES (1, 'chat-1', 'test', 'Flagged Chat', 5, ?)",
            (json.dumps({"vectorize": 0}),),
        )
        db.conn.commit()

        mock_store = MagicMock()

        with patch("footprinter.ingest.chat_indexer._chat_vectorization_enabled", return_value=True):
            from footprinter.ingest.chat_indexer import ChatIndexer

            indexer = ChatIndexer(db)
            indexer._vector_store = mock_store

            conv_data = {
                "source": "test",
                "title": "Flagged Chat",
                "summary": "A chat to exclude",
                "created_at": "2026-01-01",
                "message_count": 5,
            }
            indexer._vectorize_chat_info(1, conv_data)

        mock_store.index_chat_info.assert_not_called()

        db.close()
