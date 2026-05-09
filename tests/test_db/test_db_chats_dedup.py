"""Tests for footprinter.db.chats.detect_duplicates."""

import sqlite3

import pytest


@pytest.fixture
def conn():
    """In-memory SQLite with chats and messages tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE chats ("
        "  id INTEGER PRIMARY KEY,"
        "  external_id TEXT,"
        "  account TEXT,"
        "  title TEXT,"
        "  message_count INTEGER,"
        "  created_at TEXT,"
        "  modified_at TEXT,"
        "  status TEXT,"
        "  merged_into_id INTEGER"
        ")"
    )
    db.execute(
        "CREATE TABLE messages ("
        "  id INTEGER PRIMARY KEY,"
        "  chat_id INTEGER,"
        "  message_id TEXT,"
        "  role TEXT,"
        "  content TEXT,"
        "  created_at TEXT"
        ")"
    )
    db.commit()
    return db


def _insert_chat(conn, chat_id, title, account="claude", message_count=0, status="listed"):
    conn.execute(
        "INSERT INTO chats (id, external_id, account, title, message_count, status) VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, f"ext-{chat_id}", account, title, message_count, status),
    )
    conn.commit()


def _insert_message(conn, msg_id, chat_id, content, role="user"):
    conn.execute(
        "INSERT INTO messages (id, chat_id, role, content) VALUES (?, ?, ?, ?)",
        (msg_id, chat_id, role, content),
    )
    conn.commit()


class TestDetectDuplicates:
    def test_exact_title(self, conn):
        from footprinter.db.chats import detect_duplicates

        _insert_chat(conn, 1, "My Chat", message_count=3)
        _insert_chat(conn, 2, "My Chat", message_count=5)

        groups = detect_duplicates(conn)
        assert len(groups) == 1
        assert groups[0]["reason"] == "exact_title"
        assert groups[0]["confidence"] == "high"
        assert len(groups[0]["chats"]) == 2

    def test_fuzzy_title(self, conn):
        from footprinter.db.chats import detect_duplicates

        _insert_chat(conn, 1, "Implementing user authentication")
        _insert_chat(conn, 2, "Implementing user authenticaton")  # typo

        groups = detect_duplicates(conn)
        assert len(groups) == 1
        assert groups[0]["reason"] == "fuzzy_title"
        assert groups[0]["confidence"] == "medium"

    def test_message_overlap(self, conn):
        from footprinter.db.chats import detect_duplicates

        _insert_chat(conn, 1, "Chat A", account="claude", message_count=3)
        _insert_chat(conn, 2, "Chat B", account="claude", message_count=3)

        # Shared messages
        _insert_message(conn, 1, 1, "Hello world")
        _insert_message(conn, 2, 1, "How are you?")
        _insert_message(conn, 3, 1, "Unique to chat 1")

        _insert_message(conn, 4, 2, "Hello world")
        _insert_message(conn, 5, 2, "How are you?")
        _insert_message(conn, 6, 2, "Unique to chat 2")

        groups = detect_duplicates(conn)
        assert len(groups) == 1
        assert groups[0]["reason"] == "message_overlap"

    def test_no_chats(self, conn):
        from footprinter.db.chats import detect_duplicates

        groups = detect_duplicates(conn)
        assert groups == []

    def test_single_chat(self, conn):
        from footprinter.db.chats import detect_duplicates

        _insert_chat(conn, 1, "Only chat")
        groups = detect_duplicates(conn)
        assert groups == []

    def test_returns_plain_dicts(self, conn):
        from footprinter.db.chats import detect_duplicates

        _insert_chat(conn, 1, "Same Title")
        _insert_chat(conn, 2, "Same Title")

        groups = detect_duplicates(conn)
        assert len(groups) == 1
        group = groups[0]
        assert isinstance(group, dict)
        assert set(group.keys()) == {"reason", "confidence", "chats", "detail"}
        for chat in group["chats"]:
            assert isinstance(chat, dict)
