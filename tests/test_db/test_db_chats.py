"""Tests for footprinter.db.chats.insert_chat status + indexed_at population."""

import sqlite3
import time

import pytest

from footprinter.db.chats import insert_chat


@pytest.fixture
def conn():
    """In-memory SQLite with a chats schema matching legacy production DBs.

    Deliberately omits DEFAULTs on ``status`` and ``indexed_at`` to reproduce
    the legacy production state where ``insert_chat`` produced NULL columns
    that the MCP status filter silently excluded.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            account TEXT,
            title TEXT,
            summary TEXT,
            created_at DATETIME,
            modified_at DATETIME,
            message_count INTEGER DEFAULT 0,
            indexed_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            status TEXT
        )
        """
    )
    db.commit()
    return db


def _minimal_payload(external_id: str = "ext-1", **overrides):
    payload = {
        "external_id": external_id,
        "account": "claude",
        "title": "Chat title",
        "summary": "",
        "created_at": "2026-04-17 12:00:00",
        "updated_at": "2026-04-17 12:05:00",
        "message_count": 1,
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def test_insert_chat_populates_status_and_indexed_at(conn):
    """New rows must get status='active' and a non-null indexed_at."""
    insert_chat(conn, _minimal_payload())

    row = conn.execute(
        "SELECT status, indexed_at FROM chats WHERE external_id = 'ext-1'"
    ).fetchone()
    assert row["status"] == "active"
    assert row["indexed_at"] is not None


def test_insert_chat_preserves_existing_status_on_conflict(conn):
    """Re-importing a chat must not overwrite a user-set status like 'hidden'."""
    insert_chat(conn, _minimal_payload())
    conn.execute("UPDATE chats SET status = 'hidden' WHERE external_id = 'ext-1'")
    conn.commit()

    insert_chat(conn, _minimal_payload(title="Updated title"))

    row = conn.execute(
        "SELECT status, title FROM chats WHERE external_id = 'ext-1'"
    ).fetchone()
    assert row["status"] == "hidden"
    assert row["title"] == "Updated title"


def test_insert_chat_preserves_indexed_at_on_conflict(conn):
    """indexed_at is a first-seen invariant — re-imports must not bump it."""
    insert_chat(conn, _minimal_payload())
    first_indexed_at = conn.execute(
        "SELECT indexed_at FROM chats WHERE external_id = 'ext-1'"
    ).fetchone()["indexed_at"]

    time.sleep(1.1)  # CURRENT_TIMESTAMP has 1-second resolution
    insert_chat(conn, _minimal_payload(title="Re-imported"))

    second_indexed_at = conn.execute(
        "SELECT indexed_at FROM chats WHERE external_id = 'ext-1'"
    ).fetchone()["indexed_at"]
    assert second_indexed_at == first_indexed_at


def test_insert_chat_accepts_explicit_status(conn):
    """An explicit status in the payload must be honored on insert."""
    insert_chat(conn, _minimal_payload(status="hidden"))

    row = conn.execute(
        "SELECT status FROM chats WHERE external_id = 'ext-1'"
    ).fetchone()
    assert row["status"] == "hidden"
