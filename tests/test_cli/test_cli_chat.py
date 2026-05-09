"""Tests for chat queries and fp view chats/chat CLI commands.

Query-layer tests use a raw sqlite3.Connection; CLI integration tests
use run_fp() with FOOTPRINTER_DB_PATH pointed at a temporary database.
"""

import json
import sqlite3

import pytest
from conftest import run_fp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_chat(
    conn, *, id, external_id, account="claude", title="Untitled", message_count=0, status="active", merged_into_id=None
):
    """Insert a chats row."""
    conn.execute(
        """INSERT INTO chats
           (id, external_id, account, title, message_count,
            created_at, updated_at, status, merged_into_id)
           VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?, ?)""",
        (id, external_id, account, title, message_count, status, merged_into_id),
    )
    conn.commit()


def _insert_message(conn, *, chat_id, role="user", content="hello", message_id=None):
    """Insert a messages row."""
    conn.execute(
        """INSERT INTO messages (chat_id, message_id, role, content, created_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        (chat_id, message_id, role, content),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_db(tmp_path):
    """Database instance with full schema for chat tests."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "chat_test.db"
    db = Database(str(db_path))
    yield db
    db.close()


@pytest.fixture
def chat_conn(chat_db):
    """Raw sqlite3.Connection for query-layer tests."""
    conn = sqlite3.connect(chat_db.db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def chat_db_env(tmp_path, monkeypatch):
    """Temp DB with test data, FOOTPRINTER_DB_PATH pointed at it for run_fp()."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "chat_cli.db"
    db = Database(str(db_path))

    # Seed chats
    _insert_chat(db.conn, id=1, external_id="conv-1", account="claude", title="First Chat", message_count=2)
    _insert_chat(db.conn, id=2, external_id="conv-2", account="chatgpt", title="Second Chat", message_count=1)

    # Seed messages
    _insert_message(db.conn, chat_id=1, role="user", content="Hello")
    _insert_message(db.conn, chat_id=1, role="assistant", content="Hi there!")
    _insert_message(db.conn, chat_id=2, role="user", content="Hey GPT")

    db.close()
    monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
    yield db_path


# ---------------------------------------------------------------------------
# Query layer tests
# ---------------------------------------------------------------------------


class TestListChats:
    """Tests for queries.chat.list_chats()."""

    def test_returns_active_chats(self, chat_conn):
        from footprinter.db.chats import list_chats

        _insert_chat(chat_conn, id=1, external_id="c1", title="Active Chat", message_count=3)
        chats = list_chats(chat_conn)["chats"]
        assert len(chats) == 1
        assert chats[0]["title"] == "Active Chat"

    def test_insert_merged_status_rejected(self, chat_conn):
        """The chats CHECK constraint must reject status='merged'."""
        with pytest.raises(sqlite3.IntegrityError):
            _insert_chat(chat_conn, id=1, external_id="c1", title="Merged", status="merged")

    def test_filters_by_account(self, chat_conn):
        from footprinter.db.chats import list_chats

        _insert_chat(chat_conn, id=1, external_id="c1", account="claude", title="Claude Chat")
        _insert_chat(chat_conn, id=2, external_id="c2", account="chatgpt", title="GPT Chat")
        chats = list_chats(chat_conn, account="claude")["chats"]
        assert len(chats) == 1
        assert chats[0]["account"] == "claude"

    def test_respects_limit(self, chat_conn):
        from footprinter.db.chats import list_chats

        for i in range(5):
            _insert_chat(chat_conn, id=i + 1, external_id=f"c{i}", title=f"Chat {i}")
        chats = list_chats(chat_conn, limit=3)["chats"]
        assert len(chats) == 3

    def test_excludes_removed_by_default(self, chat_conn):
        """Default (status=None) excludes removed chats."""
        from footprinter.db.chats import list_chats

        _insert_chat(chat_conn, id=1, external_id="c1", title="Active", message_count=1)
        _insert_chat(chat_conn, id=2, external_id="c2", title="Removed", message_count=0, status="removed")
        chats = list_chats(chat_conn)["chats"]
        assert len(chats) == 1
        assert chats[0]["title"] == "Active"

    def test_status_all_includes_removed(self, chat_conn):
        """status="all" includes removed chats."""
        from footprinter.db.chats import list_chats

        _insert_chat(chat_conn, id=1, external_id="c1", title="Active", message_count=1)
        _insert_chat(chat_conn, id=2, external_id="c2", title="Removed", message_count=0, status="removed")
        chats = list_chats(chat_conn, status="all")["chats"]
        assert len(chats) == 2

    def test_account_filter(self, chat_conn):
        """Account filter returns only matching chats."""
        from footprinter.db.chats import list_chats

        _insert_chat(chat_conn, id=10, external_id="c10", account="claude", title="Claude Chat")
        _insert_chat(chat_conn, id=11, external_id="c11", account="chatgpt", title="GPT Chat")
        result = list_chats(chat_conn, account="claude")
        assert len(result["chats"]) == 1
        assert result["chats"][0]["account"] == "claude"

    def test_empty_db_returns_empty_list(self, chat_conn):
        from footprinter.db.chats import list_chats

        chats = list_chats(chat_conn)["chats"]
        assert chats == []


class TestGetActiveChats:
    """Tests for _get_active_chats() helper."""

    def test_excludes_removed(self, chat_conn):
        """Active chats only — removed are excluded."""
        from footprinter.db.chats import _get_active_chats

        _insert_chat(chat_conn, id=1, external_id="c1", title="Active", message_count=1)
        _insert_chat(chat_conn, id=3, external_id="c3", title="Removed", message_count=0, status="removed")
        chats = _get_active_chats(chat_conn)
        assert len(chats) == 1
        assert chats[0]["title"] == "Active"


class TestGetChatDetail:
    """Tests for queries.chat.get_chat_detail()."""

    def test_returns_chat_and_messages(self, chat_conn):
        from footprinter.db.chats import get_chat_detail

        _insert_chat(chat_conn, id=1, external_id="c1", title="Test Chat", message_count=2)
        _insert_message(chat_conn, chat_id=1, role="user", content="Hello")
        _insert_message(chat_conn, chat_id=1, role="assistant", content="Hi!")

        result = get_chat_detail(chat_conn, 1)
        assert result is not None
        assert result["title"] == "Test Chat"
        assert len(result["messages"]) == 2

    def test_returns_none_for_missing_id(self, chat_conn):
        from footprinter.db.chats import get_chat_detail

        result = get_chat_detail(chat_conn, 999)
        assert result is None

    def test_messages_ordered_by_id(self, chat_conn):
        from footprinter.db.chats import get_chat_detail

        _insert_chat(chat_conn, id=1, external_id="c1", title="Chat", message_count=2)
        _insert_message(chat_conn, chat_id=1, role="user", content="First")
        _insert_message(chat_conn, chat_id=1, role="assistant", content="Second")

        result = get_chat_detail(chat_conn, 1)
        assert result["messages"][0]["content"] == "First"
        assert result["messages"][1]["content"] == "Second"

    def test_returns_summary_and_relationship_fields(self, chat_conn):
        """get_chat_detail() must include summary, client/project IDs and names."""
        from footprinter.db.chats import get_chat_detail

        chat_conn.execute(
            """INSERT INTO clients (id, name, slug, client_type, status)
               VALUES (1, 'Acme Corp', 'acme', 'external', 'active')"""
        )
        chat_conn.execute(
            """INSERT INTO projects (id, project_name, project_type, status, client_id)
               VALUES (1, 'Alpha', 'python', 'active', 1)"""
        )
        chat_conn.execute(
            """INSERT INTO chats
               (id, external_id, account, title, summary, message_count,
                client_id, project_id, created_at, updated_at, status)
               VALUES (10, 'c-rel', 'claude', 'Relationship Chat', 'test summary',
                       0, 1, 1, datetime('now'), datetime('now'), 'active')"""
        )
        chat_conn.commit()

        result = get_chat_detail(chat_conn, 10)
        assert result is not None
        assert result["summary"] == "test summary"
        assert result["client_id"] == 1
        assert result["project_id"] == 1
        assert result["project_name"] == "Alpha"
        assert result["client_name"] == "Acme Corp"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestChatListCLI:
    """Tests for fp view chats."""

    def test_table_output(self, chat_db_env):
        stdout, stderr, code = run_fp("view", "chats")
        assert code == 0
        output = stdout + stderr
        assert "First Chat" in output
        assert "Second Chat" in output

    def test_json_output(self, chat_db_env):
        stdout, _stderr, code = run_fp("view", "chats", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert isinstance(data, dict)
        assert "chats" in data
        assert "pagination" in data
        assert len(data["chats"]) == 2

    def test_limit(self, chat_db_env):
        stdout, _stderr, code = run_fp("view", "chats", "--limit", "1", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert len(data["chats"]) == 1

    def test_page_flag(self, chat_db_env):
        stdout, _stderr, code = run_fp("view", "chats", "--page", "1", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert data["pagination"]["page"] == 1


class TestChatViewCLI:
    """Tests for fp view chat <id>."""

    def test_shows_detail(self, chat_db_env):
        stdout, stderr, code = run_fp("view", "chat", "1")
        assert code == 0
        output = stdout + stderr
        assert "First Chat" in output

    def test_json_output(self, chat_db_env):
        stdout, _stderr, code = run_fp("view", "chat", "1", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert data["title"] == "First Chat"
        assert len(data["messages"]) == 2

    def test_missing_id_errors(self, chat_db_env):
        stdout, stderr, code = run_fp("view", "chat", "999")
        assert code != 0

    def test_requires_id_arg(self, chat_db_env):
        _stdout, _stderr, code = run_fp("view", "chat")
        assert code != 0
