"""Tests for MCP tool handler functions with mock DB."""

import importlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixture and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_db(tool_db):
    """Full-schema database for MCP tool tests."""
    yield tool_db


def _patch_get_db(module_path, conn):
    """Return a patch that makes get_db() yield the test connection.

    Follows the existing pattern from test_security_permissions.py.
    """
    mock = patch(f"{module_path}.get_db")
    original_start = mock.start

    def patched_start():
        mock_get_db = original_start()
        mock_get_db.return_value.__enter__ = lambda s: conn
        mock_get_db.return_value.__exit__ = lambda s, *args: None
        return mock_get_db

    mock.start = patched_start
    return mock


# ---------------------------------------------------------------------------
# TestFootprinterStatus — replaces TestContexterSources + TestContexterStats
# ---------------------------------------------------------------------------
class TestFootprinterStatus:
    """Tests for footprinter_status (footprinter.mcp.tools.status)."""

    def _call(self, mcp_db):
        """Call footprinter_status with patched DB."""
        with patch("footprinter.mcp.tools.status.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            from footprinter.mcp.tools.status import footprinter_status

            return footprinter_status()

    def test_response_shape(self, mcp_db):
        result = self._call(mcp_db)
        expected_keys = {
            "sources",
            "files_by_source",
            "files_by_status",
            "projects_by_status",
            "emails_by_client",
            "chats_by_client",
        }
        assert set(result.keys()) == expected_keys

    def test_sources_returns_all_table_keys(self, mcp_db):
        result = self._call(mcp_db)
        expected = {"files", "emails", "chats", "messages", "browser", "projects", "clients"}
        assert set(result["sources"].keys()) == expected

    def test_no_duplicate_count_keys(self, mcp_db):
        """Scalar counts like email_count must NOT exist at top level."""
        result = self._call(mcp_db)
        for stale_key in ("email_count", "chat_count", "message_count", "visits_count"):
            assert stale_key not in result

    def test_counts_zero_for_empty_db(self, mcp_db):
        result = self._call(mcp_db)
        for table, info in result["sources"].items():
            assert info["count"] == 0

    def test_file_count_excludes_removed(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, source, status, indexed_at)"
            " VALUES (1, 'a.txt', 'local', 'listed', '2024-01-01')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, indexed_at)"
            " VALUES (2, 'b.txt', 'local', 'listed', '2024-01-02')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, indexed_at)"
            " VALUES (3, 'c.txt', 'local', 'removed', '2024-01-03')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["files"]["count"] == 2

    def test_last_sync_populated(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, source, status, indexed_at)"
            " VALUES (1, 'a.txt', 'local', 'listed', '2024-06-15T12:00:00')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["files"]["last_sync"] == "2024-06-15T12:00:00"

    # -- Hidden client exclusion: counts --

    def test_email_count_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Email 1', 'a@b.com', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (2, 'msg-2', 'thread-2', 'work', 'Email 2', 'c@d.com', '2024-01-01', 2)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (3, 'msg-3', 'thread-3', 'personal', 'Email 3', 'e@f.com', '2024-01-01')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["emails"]["count"] == 2

    def test_chat_count_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (1, 'conv-1', 'claude', 'Chat 1', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (2, 'conv-2', 'claude', 'Chat 2', 2)"
        )
        cursor.execute("INSERT INTO chats (id, external_id, account, title) VALUES (3, 'conv-3', 'claude', 'Chat 3')")
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["chats"]["count"] == 2

    def test_chat_count_excludes_removed(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title) VALUES (1, 'conv-1', 'claude', 'Active Chat 1')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title) VALUES (2, 'conv-2', 'claude', 'Active Chat 2')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, status) "
            "VALUES (3, 'conv-3', 'claude', 'Removed Chat', 'removed')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["chats"]["count"] == 2

    def test_message_count_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (1, 'conv-1', 'claude', 'Chat 1', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (2, 'conv-2', 'claude', 'Chat 2', 2)"
        )
        cursor.execute("INSERT INTO chats (id, external_id, account, title) VALUES (3, 'conv-3', 'claude', 'Chat 3')")
        for i, chat_id in [(1, 1), (2, 1), (3, 2), (4, 2), (5, 2), (6, 3)]:
            cursor.execute(
                f"INSERT INTO messages (id, chat_id, role, content) VALUES ({i}, {chat_id}, 'human', 'msg {i}')"
            )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["messages"]["count"] == 3

    def test_browser_count_excludes_removed(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser, visit_count, status) "
            "VALUES (1, 'https://a.com', 'A', '2024-01-01', 'safari', 1, 'listed')"
        )
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser, visit_count, status) "
            "VALUES (2, 'https://b.com', 'B', '2024-01-02', 'chrome', 1, 'listed')"
        )
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser, visit_count, status) "
            "VALUES (3, 'https://c.com', 'C', '2024-01-03', 'safari', 1, 'removed')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["browser"]["count"] == 2

    def test_browser_count_excludes_hidden_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser, visit_count, client_id) "
            "VALUES (1, 'https://a.com', 'A', '2024-01-01', 'safari', 1, 1)"
        )
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser, visit_count, client_id) "
            "VALUES (2, 'https://b.com', 'B', '2024-01-02', 'chrome', 1, 2)"
        )
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser, visit_count) "
            "VALUES (3, 'https://c.com', 'C', '2024-01-03', 'safari', 1)"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["browser"]["count"] == 2

    # -- Hidden client exclusion: timestamps --

    def test_email_lastsync_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, indexed_at, client_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Email 1',"
            " 'a@b.com', '2024-01-01', '2024-01-01T10:00:00', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, indexed_at, client_id) "
            "VALUES (2, 'msg-2', 'thread-2', 'work', 'Email 2',"
            " 'c@d.com', '2024-06-01', '2024-06-01T10:00:00', 2)"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["emails"]["last_sync"] == "2024-01-01T10:00:00"

    def test_chat_lastsync_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, modified_at, client_id) "
            "VALUES (1, 'conv-1', 'claude', 'Chat 1', '2024-01-01T10:00:00', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, modified_at, client_id) "
            "VALUES (2, 'conv-2', 'claude', 'Chat 2', '2024-06-01T10:00:00', 2)"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["chats"]["last_sync"] == "2024-01-01T10:00:00"

    def test_message_lastsync_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (1, 'conv-1', 'claude', 'Chat 1', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (2, 'conv-2', 'claude', 'Chat 2', 2)"
        )
        cursor.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at) "
            "VALUES (1, 1, 'human', 'visible msg', '2024-01-01T10:00:00')"
        )
        cursor.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at) "
            "VALUES (2, 2, 'human', 'hidden msg', '2024-06-01T10:00:00')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["messages"]["last_sync"] == "2024-01-01T10:00:00"

    def test_email_lastsync_none_when_all_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (1, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, indexed_at, client_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Email 1',"
            " 'a@b.com', '2024-01-01', '2024-01-01T10:00:00', 1)"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["emails"]["last_sync"] is None

    def test_chat_lastsync_none_when_all_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (1, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, modified_at, client_id) "
            "VALUES (1, 'conv-1', 'claude', 'Chat 1', '2024-01-01T10:00:00', 1)"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["chats"]["last_sync"] is None

    def test_message_lastsync_none_when_all_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (1, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (1, 'conv-1', 'claude', 'Chat 1', 1)"
        )
        cursor.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at) "
            "VALUES (1, 1, 'human', 'hidden msg', '2024-01-01T10:00:00')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["sources"]["messages"]["last_sync"] is None

    # -- Breakdown queries (from old TestContexterStats) --

    def test_files_by_source(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, source, status, size_bytes) VALUES (1, 'a.bin', 'local', 'listed', 100)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, size_bytes) VALUES (2, 'b.bin', 'local', 'listed', 200)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, size_bytes) VALUES (3, 'c.bin', 'drive', 'listed', 500)"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["files_by_source"]["local"]["count"] == 2
        assert result["files_by_source"]["local"]["size_bytes"] == 300
        assert result["files_by_source"]["drive"]["count"] == 1
        assert result["files_by_source"]["drive"]["size_bytes"] == 500

    # test_drive_links removed — remote_links stats moved to app-scope

    def test_emails_by_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Email 1', 'a@b.com', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (2, 'msg-2', 'thread-2', 'work', 'Email 2', 'c@d.com', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (3, 'msg-3', 'thread-3', 'personal', 'Email 3', 'e@f.com', '2024-01-01')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["emails_by_client"]["Acme Corp"] == 2
        assert result["emails_by_client"]["(unassigned)"] == 1

    def test_chats_by_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (1, 'conv-1', 'claude', 'Chat 1', 1)"
        )
        cursor.execute("INSERT INTO chats (id, external_id, account, title) VALUES (2, 'conv-2', 'claude', 'Chat 2')")
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["chats_by_client"]["Acme Corp"] == 1
        assert result["chats_by_client"]["(unassigned)"] == 1

    def test_emails_by_client_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Email 1', 'a@b.com', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (2, 'msg-2', 'thread-2', 'work', 'Email 2', 'c@d.com', '2024-01-01', 2)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (3, 'msg-3', 'thread-3', 'personal', 'Email 3', 'e@f.com', '2024-01-01')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert "Secret Corp" not in result["emails_by_client"]
        assert result["emails_by_client"]["Visible Corp"] == 1
        assert result["emails_by_client"]["(unassigned)"] == 1

    def test_chats_by_client_excludes_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Visible Corp', 'full', 'external')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, visibility) "
            "VALUES (2, 'Secret Corp', 'secret', 'external', 'hidden')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (1, 'conv-1', 'claude', 'Chat 1', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) VALUES (2, 'conv-2', 'claude', 'Chat 2', 2)"
        )
        cursor.execute("INSERT INTO chats (id, external_id, account, title) VALUES (3, 'conv-3', 'claude', 'Chat 3')")
        mcp_db.commit()
        result = self._call(mcp_db)
        assert "Secret Corp" not in result["chats_by_client"]
        assert result["chats_by_client"]["Visible Corp"] == 1
        assert result["chats_by_client"]["(unassigned)"] == 1

    def test_chats_by_client_excludes_removed(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id) "
            "VALUES (1, 'conv-1', 'claude', 'Active Chat', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, client_id, status) "
            "VALUES (2, 'conv-2', 'claude', 'Removed Chat', 1, 'removed')"
        )
        mcp_db.commit()
        result = self._call(mcp_db)
        assert result["chats_by_client"]["Acme Corp"] == 1

    def test_empty_db_zeros(self, mcp_db):
        result = self._call(mcp_db)
        assert result["emails_by_client"] == {}
        assert result["chats_by_client"] == {}


# ---------------------------------------------------------------------------
# TestContexterSearch
# ---------------------------------------------------------------------------
class TestContexterSearch:
    """Tests for footprinter_search (footprinter.mcp.tools.search)."""

    _SCOPE_TO_TABLE = {
        "source:files": "files", "source:emails": "emails",
        "source:chats": "chats", "source:browser": "visits",
    }

    def _set_visible(self, conn, *source_scopes):
        """Set visibility policies and stamp records as visible."""
        cursor = conn.cursor()
        for scope in source_scopes:
            cursor.execute(
                "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES (?, 'full')",
                (scope,),
            )
            table = self._SCOPE_TO_TABLE.get(scope)
            if table:
                cursor.execute(
                    f"UPDATE {table} SET visibility='full'"
                    " WHERE visibility IS NULL OR visibility = 'inherit'"
                )
        conn.commit()

    def test_search_files_by_name(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'readme.md', '/test/readme.md', 'listed', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("readme")

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "readme.md"

    def test_search_returns_all_source_keys(self, mcp_db):
        self._set_visible(mcp_db, "source:files", "source:emails", "source:chats")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("test")

        # Default sources: files, emails, chats, browser
        assert "files" in result
        assert "emails" in result
        assert "chats" in result
        # Browser shows up unless hidden (no policy = baseline, still included)
        assert "browser" in result

    def test_search_single_source_filter(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Test email', 'test@test.com', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Test", sources=["emails"])

        assert "emails" in result
        assert "files" not in result
        assert len(result["emails"]) == 1

    def test_search_empty_results(self, mcp_db):
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("nonexistent_xyz", sources=["files"])

        assert result["files"] == []

    def test_search_limit(self, mcp_db):
        cursor = mcp_db.cursor()
        for i in range(5):
            cursor.execute(
                "INSERT INTO files (id, source, name, path, status, modified_at) "
                f"VALUES ({i + 1}, 'local', 'file{i}.txt', '/test/file{i}.txt', 'listed', '2024-01-0{i + 1}')"
            )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("file", sources=["files"], limit=2)

        assert len(result["files"]) == 2

    def _seed_files(self, conn, count):
        cursor = conn.cursor()
        for i in range(count):
            cursor.execute(
                "INSERT INTO files (id, source, name, path, status, modified_at) "
                "VALUES (?, 'local', ?, ?, 'listed', '2024-01-01')",
                (i + 1, f"file{i}.txt", f"/test/file{i}.txt"),
            )
        self._set_visible(conn, "source:files")
        conn.commit()

    def test_search_limit_capped_at_200_when_caller_exceeds(self, mcp_db):
        self._seed_files(mcp_db, 250)

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("file", sources=["files"], limit=10000)

        assert len(result["files"]) == 200

    def test_search_limit_summary_mentions_truncation_when_capped(self, mcp_db):
        self._seed_files(mcp_db, 250)

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("file", sources=["files"], limit=10000)

        summary = result["summary"]
        assert "200" in summary
        assert "Limit capped" in summary
        assert "Narrow" in summary

    def test_search_limit_under_cap_no_truncation_message(self, mcp_db):
        self._seed_files(mcp_db, 5)

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("file", sources=["files"], limit=50)

        assert len(result["files"]) == 5
        assert "Limit capped" not in result["summary"]

    def test_search_limit_exactly_200_no_truncation_message(self, mcp_db):
        self._seed_files(mcp_db, 200)

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("file", sources=["files"], limit=200)

        assert len(result["files"]) == 200
        assert "Limit capped" not in result["summary"]

    def test_search_emails_include_project_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, client_id, project_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Acme update',"
            " 'bob@acme.com', '2024-01-01', 1, 1)"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Acme", sources=["emails"])

        assert len(result["emails"]) == 1
        assert result["emails"][0]["project_name"] == "AcmeWeb"
        assert result["emails"][0]["client_name"] == "Acme Corp"

    def test_search_emails_null_project_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'No project email', 'a@b.com', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("No project", sources=["emails"])

        assert len(result["emails"]) == 1
        assert result["emails"][0]["project_name"] is None
        assert result["emails"][0]["client_name"] is None

    def test_search_chats_include_project_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, created_at, client_id, project_id) "
            "VALUES (1, 'conv-1', 'claude', 'Acme discussion', '2024-01-01', 1, 1)"
        )
        self._set_visible(mcp_db, "source:chats")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Acme", sources=["chats"])

        assert len(result["chats"]) == 1
        assert result["chats"][0]["project_name"] == "AcmeWeb"
        assert result["chats"][0]["client_name"] == "Acme Corp"

    def test_search_emails_filter_by_project(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'Alpha')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (2, 'Beta')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, project_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Update report', 'a@b.com', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, project_id) "
            "VALUES (2, 'msg-2', 'thread-2', 'work', 'Update notes', 'c@d.com', '2024-01-02', 2)"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Update", sources=["emails"], project="Alpha")

        assert len(result["emails"]) == 1
        assert result["emails"][0]["project_name"] == "Alpha"

    def test_search_emails_filter_by_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (2, 'Globex', 'globex', 'external')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Status email', 'a@acme.com', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, client_id) "
            "VALUES (2, 'msg-2', 'thread-2', 'work', 'Status update', 'b@globex.com', '2024-01-02', 2)"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Status", sources=["emails"], client="Acme Corp")

        assert len(result["emails"]) == 1
        assert result["emails"][0]["client_name"] == "Acme Corp"

    def test_search_chats_filter_by_project(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'Alpha')")
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, created_at, project_id) "
            "VALUES (1, 'conv-1', 'claude', 'Alpha chat', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, created_at) "
            "VALUES (2, 'conv-2', 'claude', 'Other chat', '2024-01-02')"
        )
        self._set_visible(mcp_db, "source:chats")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("chat", sources=["chats"], project="Alpha")

        assert len(result["chats"]) == 1
        assert result["chats"][0]["title"] == "Alpha chat"

    def test_search_chats_filter_by_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, created_at, client_id) "
            "VALUES (1, 'conv-1', 'claude', 'Acme chat', '2024-01-01', 1)"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, created_at) "
            "VALUES (2, 'conv-2', 'claude', 'General chat', '2024-01-02')"
        )
        self._set_visible(mcp_db, "source:chats")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("chat", sources=["chats"], client="Acme Corp")

        assert len(result["chats"]) == 1
        assert result["chats"][0]["title"] == "Acme chat"

    def test_search_browser_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser) "
            "VALUES (1, 'https://example.com', 'Example', '2024-01-01', 'Safari')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'hidden')")
        mcp_db.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(mcp_db, "source:browser")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Example", sources=["browser"])

        assert len(result.get("browser", [])) == 0

    def test_search_browser_opaque(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser) "
            "VALUES (1, 'https://example.com', 'Example', '2024-01-01', 'Safari')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'opaque')")
        mcp_db.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(mcp_db, "source:browser")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Example", sources=["browser"])

        assert "browser" in result
        assert len(result["browser"]) == 1
        entry = result["browser"][0]
        assert set(entry.keys()) == {"id", "browser"}

    # --- Multi-word query tests ---

    def test_search_multi_word_matches_artifact(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'Project-Alpha-Report', '/test/Project-Alpha-Report', 'listed', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Project Report", sources=["files"])

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "Project-Alpha-Report"

    def test_search_multi_word_no_match(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'Project-Alpha-Report', '/test/Project-Alpha-Report', 'listed', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Project Beta", sources=["files"])

        assert len(result["files"]) == 0

    def test_search_short_terms_dropped(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'Project-Alpha-Report', '/test/Project-Alpha-Report', 'listed', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("a Project", sources=["files"])

        assert len(result["files"]) == 1

    def test_search_single_word_unchanged(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'readme.md', '/test/readme.md', 'listed', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("readme", sources=["files"])

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "readme.md"

    def test_search_multi_word_emails(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Monthly Status Report', 'a@b.com', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Status Report", sources=["emails"])

        assert len(result["emails"]) == 1
        assert result["emails"][0]["subject"] == "Monthly Status Report"

    def test_search_multi_word_browser(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser) "
            "VALUES (1, 'https://github.com/pulls', 'GitHub Pull Request', '2024-01-01', 'Safari')"
        )
        self._set_visible(mcp_db, "source:browser")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("GitHub Request", sources=["browser"])

        assert len(result["browser"]) == 1
        assert result["browser"][0]["title"] == "GitHub Pull Request"

    def test_search_summary_with_results(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'report.pdf', '/test/report.pdf', 'listed', '2024-01-01')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (2, 'local', 'report_v2.pdf', '/test/report_v2.pdf', 'listed', '2024-01-02')"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Report summary', 'a@b.com', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:files", "source:emails", "source:chats")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("report")

        assert "summary" in result
        assert isinstance(result["summary"], str)
        assert "2 files" in result["summary"]
        assert "1 email" in result["summary"]

    def test_search_summary_empty_results(self, mcp_db):
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("nonexistent_xyz_999", sources=["files"])

        assert "summary" in result
        assert "No results" in result["summary"]
        assert "tip" in result["summary"].lower()

    def test_search_excludes_hidden_via_sql(self, mcp_db):
        """Hidden items (visibility='hidden') are excluded by SQL WHERE,
        never reaching the results at all."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, visibility) "
            "VALUES (1, 'local', 'visible.txt', '/test/visible.txt', 'listed', '2024-01-01', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, visibility) "
            "VALUES (2, 'local', 'hidden.txt', '/test/hidden.txt', 'listed', '2024-01-02', 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"])

        # Hidden file excluded by SQL — only visible file returned
        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "visible.txt"

    def test_search_summary_single_source(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Weekly update', 'a@b.com', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("update", sources=["emails"])

        assert "summary" in result
        assert "1 email" in result["summary"]
        # Should not mention sources that weren't searched
        assert "file" not in result["summary"].lower()
        assert "chat" not in result["summary"].lower()
        assert "browser" not in result["summary"].lower()

    def test_mcp_source_labels_normalized(self):
        """_SOURCE_LABELS keys and value tuples use normalized domain names."""
        from footprinter.mcp.tools.search import _SOURCE_LABELS

        assert set(_SOURCE_LABELS.keys()) == {"files", "emails", "chats", "browser"}
        for key, (singular, plural) in _SOURCE_LABELS.items():
            assert "artifact" not in singular.lower(), f"Old name 'artifact' in {key} singular"
            assert "artifact" not in plural.lower(), f"Old name 'artifact' in {key} plural"
            assert "conversation" not in singular.lower(), f"Old name 'conversation' in {key} singular"
            assert "conversation" not in plural.lower(), f"Old name 'conversation' in {key} plural"

    def test_search_hidden_excluded_no_suppressed_key(self, mcp_db):
        """Hidden items excluded by SQL — no files_suppressed key needed."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, visibility) "
            "VALUES (1, 'local', 'visible.txt', '/test/visible.txt', 'listed', '2024-01-01', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, visibility) "
            "VALUES (2, 'local', 'hidden.txt', '/test/hidden.txt', 'listed', '2024-01-02', 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"])

        # Hidden excluded by SQL, not counted as suppressed
        assert "suppressed" not in result
        assert len(result["files"]) == 1

    def test_search_suppressed_total(self, mcp_db):
        """Multi-source search reports single 'suppressed' total, not per-source keys."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, visibility) "
            "VALUES (1, 'local', 'vis.txt', '/test/vis.txt', 'listed', '2024-01-01', 'full')"
        )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, visibility) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Test', 'a@b.com', '2024-01-01', 'full')"
        )
        self._set_visible(mcp_db, "source:files", "source:emails")
        mcp_db.commit()

        def mock_filter(item_type, results, id_key="id"):
            return results, 1  # Simulate 1 suppressed per source

        with (
            patch("footprinter.mcp.tools.search.get_db") as mock_get_db,
            patch("footprinter.services.search_service.filter_results_list", side_effect=mock_filter),
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files", "emails"])

        assert result["suppressed"] == 2
        assert "files_suppressed" not in result
        assert "emails_suppressed" not in result

    # -- Helpers for source-specific filter tests ----------------------------

    def _insert_email(
        self,
        conn,
        id,
        subject,
        from_address="sender@test.com",
        from_name="Sender",
        account="personal",
        received_at="2026-02-15",
        body_preview="",
        labels="",
    ):
        """Insert an email and its FTS5 entry for search filter tests."""
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, "
            "from_name, to_addresses, received_at, body_preview, labels, visibility, access) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'full', 'allow')",
            (
                id,
                f"msg-{id}",
                f"thread-{id}",
                account,
                subject,
                from_address,
                from_name,
                "me@test.com",
                received_at,
                body_preview,
                labels,
            ),
        )
        cursor.execute(
            "INSERT INTO emails_fts (rowid, subject, from_name, from_address, body_preview) VALUES (?, ?, ?, ?, ?)",
            (id, subject, from_name, from_address, body_preview),
        )
        conn.commit()

    def _insert_file(
        self,
        conn,
        id,
        name,
        path="/Users/test/Work/file.txt",
        source="local",
        account=None,
        mime_type="text/plain",
        size_bytes=1024,
        modified_at="2026-02-15",
    ):
        """Insert a file and its FTS5 entry for search filter tests."""
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, account, mime_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'listed', 'full', 'allow')",
            (id, source, name, path, account, mime_type, size_bytes, modified_at),
        )
        cursor.execute(
            "INSERT INTO files_fts (rowid, name, content_preview) VALUES (?, ?, '')",
            (id, name),
        )
        conn.commit()

    # -- Source-specific filter tests (RED) ----------------------------------

    def test_search_email_filter_by_sender(self, mcp_db):
        """sender param filters emails by partial match on from_address/from_name."""
        self._insert_email(mcp_db, 1, "Msg from Alice", from_address="alice@example.com", from_name="Alice")
        self._insert_email(mcp_db, 2, "Msg from Bob", from_address="bob@example.com", from_name="Bob")
        self._set_visible(mcp_db, "source:emails")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["emails"], sender="alice")

        assert len(result["emails"]) == 1
        assert result["emails"][0]["from"] == "Alice"

    def test_search_email_filter_by_days_back(self, mcp_db):
        """days_back param filters to recent emails only."""
        from datetime import datetime, timedelta

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        long_ago = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        self._insert_email(mcp_db, 1, "Recent email", received_at=yesterday)
        self._insert_email(mcp_db, 2, "Old email", received_at=long_ago)
        self._set_visible(mcp_db, "source:emails")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["emails"], days_back=7)

        subjects = [e["subject"] for e in result["emails"]]
        assert "Recent email" in subjects
        assert "Old email" not in subjects

    def test_search_email_filter_by_account(self, mcp_db):
        """account param filters emails to matching account."""
        self._insert_email(mcp_db, 1, "Personal email", account="personal")
        self._insert_email(mcp_db, 2, "Work email", account="work")
        self._set_visible(mcp_db, "source:emails")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["emails"], account="work")

        assert len(result["emails"]) == 1
        assert result["emails"][0]["account"] == "work"

    def test_search_file_filter_by_folder(self, mcp_db):
        """folder param filters files by path prefix."""
        self._insert_file(mcp_db, 1, "a.txt", path="/Users/test/Work/projects/a.txt")
        self._insert_file(mcp_db, 2, "b.txt", path="/Users/test/Personal/b.txt")
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"], folder="/Users/test/Work")

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "a.txt"

    def test_search_file_filter_by_mime_type(self, mcp_db):
        """mime_type param filters files by exact MIME type."""
        self._insert_file(mcp_db, 1, "report.pdf", path="/test/report.pdf", mime_type="application/pdf")
        self._insert_file(mcp_db, 2, "notes.txt", path="/test/notes.txt", mime_type="text/plain")
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"], mime_type="application/pdf")

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "report.pdf"

    def test_search_file_filter_by_account(self, mcp_db):
        """account param filters files to matching account."""
        self._insert_file(mcp_db, 1, "work.txt", path="/test/work.txt", account="work")
        self._insert_file(mcp_db, 2, "personal.txt", path="/test/personal.txt", account="personal")
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"], account="work")

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "work.txt"

    def test_search_email_results_include_snippet(self, mcp_db):
        """Email results include from_address and snippet keys."""
        self._insert_email(mcp_db, 1, "Test", from_address="alice@test.com", body_preview="Preview text here")
        self._set_visible(mcp_db, "source:emails")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["emails"])

        email = result["emails"][0]
        assert email["from_address"] == "alice@test.com"
        assert email["snippet"] == "Preview text here"

    def test_search_file_results_include_account(self, mcp_db):
        """File results include account and mime_type keys."""
        self._insert_file(mcp_db, 1, "report.pdf", account="work", mime_type="application/pdf")
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"])

        file = result["files"][0]
        assert file["account"] == "work"
        assert file["mime_type"] == "application/pdf"

    def test_search_source_filter_ignored_for_other_sources(self, mcp_db):
        """sender param silently ignored when searching files only."""
        self._insert_file(mcp_db, 1, "report.txt")
        self._set_visible(mcp_db, "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(sources=["files"], sender="alice")

        assert len(result["files"]) == 1

    def test_search_combined_filters(self, mcp_db):
        """sender + folder apply to their respective sources in a multi-source search."""
        self._insert_email(mcp_db, 1, "Report from Alice", from_address="alice@test.com", from_name="Alice")
        self._insert_email(mcp_db, 2, "Report from Bob", from_address="bob@test.com", from_name="Bob")
        self._insert_file(mcp_db, 1, "report.txt", path="/Users/test/Work/report.txt")
        self._insert_file(mcp_db, 2, "other.txt", path="/Users/test/Personal/other.txt")
        self._set_visible(mcp_db, "source:emails", "source:files")

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(
                query="report",
                sources=["emails", "files"],
                sender="alice",
                folder="/Users/test/Work",
            )

        # sender filters emails to Alice only
        assert len(result["emails"]) == 1
        assert result["emails"][0]["from"] == "Alice"
        # folder filters files to Work only
        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "report.txt"

    # --- Permission enforcement (2) ---

    def test_email_permission_denied_strips_snippet(self, mcp_db):
        """Visible email with access='deny' appears without body_preview snippet."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_address, received_at, body_preview, visibility, access) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Confidential', "
            "'sender@test.com', '2026-01-15', 'Secret email body', 'full', 'deny')"
        )
        cursor.execute(
            "INSERT INTO emails_fts (rowid, subject, from_name, body_preview) "
            "VALUES (1, 'Confidential', 'sender', 'Secret email body')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(query="confidential", sources=["emails"])

        assert len(result["emails"]) == 1
        email = result["emails"][0]
        assert email["subject"] == "Confidential"  # metadata preserved
        assert "snippet" not in email  # content stripped

    def test_chat_search_permission_denied_strips_snippet(self, mcp_db):
        """Visible chat with access='deny' appears without snippet."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, "
            "created_at, message_count, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Secret Chat', "
            "'2026-01-15', 5, 'full', 'deny')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search(query="secret", sources=["chats"])

        assert len(result["chats"]) == 1
        chat = result["chats"][0]
        assert chat["title"] == "Secret Chat"  # metadata preserved
        assert "snippet" not in chat  # content stripped


# ---------------------------------------------------------------------------
# TestContexterSearchFTS5 — FTS5-specific search tests
# ---------------------------------------------------------------------------
class TestContexterSearchFTS5:
    """Tests for FTS5 full-text search in footprinter_search."""

    _SCOPE_TO_TABLE = {
        "source:files": "files", "source:emails": "emails",
        "source:chats": "chats", "source:browser": "visits",
    }

    def _set_visible(self, conn, *source_scopes):
        """Set visibility policies and stamp records as visible."""
        cursor = conn.cursor()
        for scope in source_scopes:
            cursor.execute(
                "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES (?, 'full')",
                (scope,),
            )
            table = self._SCOPE_TO_TABLE.get(scope)
            if table:
                cursor.execute(
                    f"UPDATE {table} SET visibility='full'"
                    " WHERE visibility IS NULL OR visibility = 'inherit'"
                )
        conn.commit()

    def test_artifact_search_matches_content_preview(self, mcp_db):
        """File with content_preview is found via FTS5; content not in metadata fields."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, "
            "visibility, access, content_preview) "
            "VALUES (1, 'local', 'data.csv', '/test/data.csv', 'listed', '2024-01-01', "
            "'full', 'allow', 'quarterly revenue breakdown by region')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("revenue", sources=["files"])

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "data.csv"
        # MCP search returns metadata only — content_preview not in result fields
        hit = result["files"][0]
        for key, value in hit.items():
            if isinstance(value, str):
                assert "quarterly revenue" not in value, f"content_preview leaked via field '{key}'"

    def test_artifact_search_matches_content_preview_visible(self, mcp_db):
        """Artifact with matching content_preview found via FTS5."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, content_preview) "
            "VALUES (1, 'local', 'notes.txt', '/test/notes.txt', 'listed', '2024-01-01', "
            "'authentication architecture design decisions')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("authentication", sources=["files"])

        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "notes.txt"

    def test_email_search_matches_body_preview(self, mcp_db):
        """Email with matching body_preview found via FTS5."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_address, received_at, body_preview) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Meeting notes', "
            "'a@b.com', '2024-01-01', 'discussed quarterly budget allocation')"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("budget", sources=["emails"])

        assert len(result["emails"]) == 1
        assert result["emails"][0]["subject"] == "Meeting notes"

    def test_empty_query_returns_results_without_fts(self, mcp_db):
        """Empty query returns recent items without FTS5 JOIN."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at) "
            "VALUES (1, 'local', 'recent.txt', '/test/recent.txt', 'listed', '2024-06-01')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("", sources=["files"])

        assert len(result["files"]) == 1

    def test_email_search_matches_from_name(self, mcp_db):
        """Email with matching from_name found via FTS5."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_name, from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Hello', "
            "'Josephine', 'j@example.com', '2024-01-01')"
        )
        self._set_visible(mcp_db, "source:emails")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("Josephine", sources=["emails"])

        assert len(result["emails"]) == 1

    def test_build_fts5_query_strips_double_quotes(self, mcp_db):
        """build_fts5_query produces well-formed FTS5 when terms contain double quotes."""
        from footprinter.db.sql_utils import build_fts5_query

        result = build_fts5_query(['"hello"', "world"])
        # Must not contain unmatched/nested double quotes
        assert '""' not in result
        # Each term should still be present (without the user's quotes)
        assert "hello" in result
        assert "world" in result

    def test_build_fts5_query_empty_after_strip(self, mcp_db):
        """build_fts5_query returns empty string when all terms vanish after stripping."""
        from footprinter.db.sql_utils import build_fts5_query

        # Pure double-quote terms: strip to empty, filter by len >= 2 removes them
        assert build_fts5_query(['""', '""a']) == ""
        # Single char after stripping: filtered out by len >= 2
        assert build_fts5_query(['"a']) == ""

    def test_build_fts5_query_pipeline_guards_empty_match(self, mcp_db):
        """split_query_terms + build_fts5_query pipeline produces empty for quote-only input."""
        from footprinter.db.sql_utils import build_fts5_query, split_query_terms

        # '""' is len 2, passes split_query_terms, but build_fts5_query strips to empty
        terms = split_query_terms('""')
        assert terms == ['""']
        result = build_fts5_query(terms)
        assert result == ""

    def test_search_with_double_quotes_no_error(self, mcp_db):
        """footprinter_search with double-quoted query doesn't raise sqlite3 error."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, content_preview) "
            "VALUES (1, 'local', 'hello.txt', '/test/hello.txt', 'listed', '2024-01-01', "
            "'he said hello to everyone')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            # This should NOT raise sqlite3.OperationalError
            result = footprinter_search('he said "hello"', sources=["files"])

        # No crash — results may or may not match, but no exception
        assert "files" in result


class TestFtsOpaqueVisibility:
    """FTS5 search returns opaque files with minimized fields via filter_results_list()."""

    def _set_visible(self, conn, *source_scopes):
        cursor = conn.cursor()
        for scope in source_scopes:
            cursor.execute(
                "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES (?, 'full')",
                (scope,),
            )
        conn.commit()

    def test_fts_opaque_files_returned_with_minimized_fields(self, mcp_db):
        """FTS5 search by metadata column returns opaque files with minimized fields.

        FTS triggers NULL content columns for opaque files, so content-based
        searches won't match.  But metadata columns (name) are always indexed,
        so a name-based FTS search should find the opaque file and
        filter_results_list() should minimize it to opaque-allowed fields.
        """
        cursor = mcp_db.cursor()
        # Opaque file — name is a metadata column, always indexed in FTS5
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, "
            "content_type, visibility) "
            "VALUES (1, 'local', 'earnings-report.xlsx', '/test/earnings-report.xlsx', "
            "'listed', '2024-01-01', 'spreadsheet', 'opaque')"
        )
        # Visible file sharing the search term in its name
        cursor.execute(
            "INSERT INTO files (id, source, name, path, status, modified_at, "
            "content_type, visibility) "
            "VALUES (2, 'local', 'earnings-summary.txt', '/test/earnings-summary.txt', "
            "'listed', '2024-01-01', 'text', 'full')"
        )
        self._set_visible(mcp_db, "source:files")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            result = footprinter_search("earnings", sources=["files"])

        # Both files returned — opaque with minimized fields, visible with full fields
        result_ids = {f["id"] for f in result["files"]}
        assert 2 in result_ids, "Visible file should be in results"
        assert 1 in result_ids, "Opaque file should appear with minimized fields"

        opaque_entry = next(f for f in result["files"] if f["id"] == 1)
        assert set(opaque_entry.keys()) == {"id", "content_type", "source"}


# ---------------------------------------------------------------------------
# TestContexterProject
# ---------------------------------------------------------------------------
class TestContexterProject:
    """Tests for footprinter_project (footprinter.mcp.tools.navigation)."""

    def test_project_found(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'Footprinter', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (1, 'local', 'app.py', 'listed', 1000, 1)"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:1', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("Footprinter")

        assert result["name"] == "Footprinter"
        assert result["file_count"] == 1

    def test_project_not_found(self, mcp_db):
        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("nonexistent")

        assert result["error_code"] == "NOT_FOUND"

    def test_project_artifact_stats(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'TestProject', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (1, 'local', 'a.py', 'listed', 500, 1)"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (2, 'drive', 'b.py', 'listed', 300, 1)"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (3, 'local', 'c.py', 'removed', 200, 1)"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:1', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("TestProject")

        assert result["file_count"] == 2
        assert result["file_size_bytes"] == 800
        assert result["local_count"] == 1
        assert result["drive_count"] == 1

    def test_project_case_insensitive(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'MyProject', 'listed', 'full')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:1', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("myproject")

        assert result["name"] == "MyProject"

    def test_project_multiple_matches_disambiguation(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'AppExchange', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (2, 'MyApp', 'listed', 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("app")

        assert result.get("disambiguation") is True
        assert len(result["matches"]) == 2
        match_names = {m["name"] for m in result["matches"]}
        assert match_names == {"AppExchange", "MyApp"}

    def test_project_disambiguation_opaque_match(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'AppExchange', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (2, 'MyApp', 'listed', 'opaque')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("app")

        assert result.get("disambiguation") is True
        assert len(result["matches"]) == 2
        visible = [m for m in result["matches"] if "name" in m]
        opaque = [m for m in result["matches"] if "visibility" in m]
        assert len(visible) == 1
        assert visible[0]["name"] == "AppExchange"
        assert "visibility" not in visible[0]
        assert len(opaque) == 1
        assert opaque[0]["visibility"] == "restricted"
        assert "name" not in opaque[0]

    def test_project_exact_match_over_fuzzy(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'App', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (2, 'AppExchange', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (1, 'local', 'main.py', 'listed', 500, 1)"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("App")

        assert result["name"] == "App"
        assert "disambiguation" not in result

    def test_project_stats_exclude_hidden_files(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'HiddenTest', 'listed', 'full')"
        )
        # Visible local file
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id, content_type) "
            "VALUES (1, 'local', 'visible.py', 'listed', 500, 1, 'code')"
        )
        # Visible drive file
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id, content_type) "
            "VALUES (2, 'drive', 'also_visible.py', 'listed', 300, 1, 'code')"
        )
        # Hidden local file — should be excluded from all stats
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id, content_type, visibility) "
            "VALUES (3, 'local', 'secret.env', 'listed', 200, 1, 'config', 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("HiddenTest")

        assert result["file_count"] == 2
        assert result["file_size_bytes"] == 800  # 500 + 300, not 1000
        assert result["local_count"] == 1  # only visible.py, not secret.env
        assert result["drive_count"] == 1
        # Hidden file's content_type ('config') must not appear
        assert "config" not in result["top_content_types"]
        assert result["top_content_types"].get("code") == 2

    def test_project_returns_folder_children(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'NavProject', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "project_id, visibility) "
            "VALUES (1, 'src', '/test/nav/src', 'src', 'local', 5, 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "project_id, visibility) "
            "VALUES (2, 'docs', '/test/nav/docs', 'docs', 'local', 3, 1, 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("NavProject")

        assert "folders" in result
        assert len(result["folders"]) == 2
        for f in result["folders"]:
            assert "id" in f
            assert "path" in f
            assert "direct_file_count" in f

    def test_project_returns_entity_counts(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'EntityProject', 'listed', 'full')"
        )
        # 3 emails
        for i in range(1, 4):
            cursor.execute(
                "INSERT INTO emails (id, message_id, thread_id, account, received_at, project_id) "
                f"VALUES ({i}, 'msg{i}', 'thread1', 'work', '2024-01-01', 1)"
            )
        # 2 chats
        for i in range(1, 3):
            cursor.execute(
                f"INSERT INTO chats (id, external_id, account, project_id) VALUES ({i}, 'chat{i}', 'personal', 1)"
            )
        # 1 browser visit
        cursor.execute(
            "INSERT INTO visits (id, url, visit_time, browser, project_id) "
            "VALUES (1, 'https://example.com', '2024-01-01', 'safari', 1)"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("EntityProject")

        assert result["entity_counts"] == {"emails": 3, "chats": 2, "visits": 1}

    def test_project_entity_counts_zero_when_none(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'EmptyProject', 'listed', 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("EmptyProject")

        assert result["entity_counts"] == {"emails": 0, "chats": 0, "visits": 0}

    def test_project_entity_counts_exclude_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'VisProject', 'listed', 'full')"
        )
        # 2 visible emails + 1 hidden
        for i in range(1, 3):
            cursor.execute(
                "INSERT INTO emails (id, message_id, thread_id, account, received_at, project_id) "
                f"VALUES ({i}, 'msg{i}', 'thread1', 'work', '2024-01-01', 1)"
            )
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, received_at, project_id, visibility) "
            "VALUES (3, 'msg3', 'thread1', 'work', '2024-01-01', 1, 'hidden')"
        )
        # 2 visible chats + 1 hidden
        for i in range(1, 3):
            cursor.execute(
                f"INSERT INTO chats (id, external_id, account, project_id) VALUES ({i}, 'chat{i}', 'personal', 1)"
            )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, project_id, visibility) "
            "VALUES (3, 'chat3', 'personal', 1, 'hidden')"
        )
        # 2 visible visits + 1 hidden
        for i in range(1, 3):
            cursor.execute(
                "INSERT INTO visits (id, url, visit_time, browser, project_id) "
                f"VALUES ({i}, 'https://ex{i}.com', '2024-01-01', 'safari', 1)"
            )
        cursor.execute(
            "INSERT INTO visits (id, url, visit_time, browser, project_id, visibility) "
            "VALUES (3, 'https://ex3.com', '2024-01-01', 'safari', 1, 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("VisProject")

        assert result["entity_counts"] == {"emails": 2, "chats": 2, "visits": 2}

    def test_project_folder_children_exclude_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO projects (id, name, status, visibility) "
            "VALUES (1, 'HiddenFolderProject', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "project_id, visibility) "
            "VALUES (1, 'full', '/test/hfp/visible', 'full', 'local', 2, 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "project_id, visibility) "
            "VALUES (2, 'secret', '/test/hfp/secret', 'secret', 'local', 5, 1, 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("HiddenFolderProject")

        assert len(result["folders"]) == 1
        assert result["folders"][0]["name"] == "full"


# ---------------------------------------------------------------------------
# TestContexterClient
# ---------------------------------------------------------------------------
class TestContexterClient:
    """Tests for footprinter_client (footprinter.mcp.tools.navigation)."""

    def test_client_found_with_projects(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'Acme Corp', 'acme', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (1, 'AcmeWeb', 'listed', 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (1, 'local', 'index.html', 'listed', 1000, 1)"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:1', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("Acme")

        assert result["name"] == "Acme Corp"
        assert len(result["projects"]) == 1
        assert result["projects"][0]["name"] == "AcmeWeb"
        assert result["total_files"] == 1
        assert result["total_size_bytes"] == 1000

    def test_client_not_found(self, mcp_db):
        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("nonexistent")

        assert result["error_code"] == "NOT_FOUND"

    def test_client_no_projects(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'Solo Client', 'solo', 'individual', 'listed', 'full')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:1', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("Solo")

        assert result["projects"] == []
        assert result["total_files"] == 0

    def test_client_multiple_matches_disambiguation(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'Acme Corp', 'acme-corp', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (2, 'Acme Inc', 'acme-inc', 'company', 'listed', 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("Acme")

        assert result.get("disambiguation") is True
        assert len(result["matches"]) == 2
        match_names = {m["name"] for m in result["matches"]}
        assert match_names == {"Acme Corp", "Acme Inc"}

    def test_client_disambiguation_opaque_match(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'Acme Corp', 'acme-corp', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (2, 'Acme Inc', 'acme-inc', 'company', 'listed', 'opaque')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("Acme")

        assert result.get("disambiguation") is True
        assert len(result["matches"]) == 2
        visible = [m for m in result["matches"] if "name" in m]
        opaque = [m for m in result["matches"] if "visibility" in m]
        assert len(visible) == 1
        assert visible[0]["name"] == "Acme Corp"
        assert "visibility" not in visible[0]
        assert len(opaque) == 1
        assert opaque[0]["visibility"] == "restricted"
        assert "name" not in opaque[0]

    def test_client_exact_match_over_fuzzy(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'Acme', 'acme', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (2, 'Acme Corp', 'acme-corp', 'company', 'listed', 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("Acme")

        assert result["name"] == "Acme"
        assert "disambiguation" not in result

    def test_client_stats_exclude_hidden_files(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'SecretCorp', 'secret', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (1, 'SecretWeb', 'listed', 1, 'full')"
        )
        # Visible file
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id) "
            "VALUES (1, 'local', 'public.html', 'listed', 1000, 1)"
        )
        # Hidden file — should be excluded from aggregate stats
        cursor.execute(
            "INSERT INTO files (id, source, name, status, size_bytes, project_id, visibility) "
            "VALUES (2, 'local', 'secrets.env', 'listed', 500, 1, 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("SecretCorp")

        assert result["total_files"] == 1
        assert result["total_size_bytes"] == 1000  # not 1500

    def test_client_projects_opaque_visibility(self, mcp_db):
        """Opaque projects within a visible client return minimal metadata."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'VisibleCorp', 'full', 'company', 'listed', 'full')"
        )
        # Project A: visible — full details expected
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (1, 'PublicWeb', 'listed', 1, 'full')"
        )
        # Project B: opaque — minimal metadata only
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (2, 'InternalTool', 'listed', 1, 'opaque')"
        )
        # Project C: hidden — excluded entirely
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (3, 'SecretProject', 'listed', 1, 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("VisibleCorp")

        # Hidden project excluded, visible + opaque remain
        assert len(result["projects"]) == 2

        # Find visible and opaque projects in result by id
        projects_by_id = {p["id"]: p for p in result["projects"]}

        # Visible project has full details
        visible = projects_by_id[1]
        assert visible["name"] == "PublicWeb"

        # Opaque project has only minimal fields (id, status, client_id)
        opaque = projects_by_id[2]
        assert opaque["id"] == 2
        assert opaque["status"] == "listed"
        assert "name" not in opaque

    def test_client_returns_total_folders(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'FolderCorp', 'foldercorp', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (1, 'ProjA', 'listed', 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (2, 'ProjB', 'listed', 1, 'full')"
        )
        # 2 folders in ProjA
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, visibility) "
            "VALUES (1, 'src', '/test/a/src', 'src', 'local', 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, visibility) "
            "VALUES (2, 'docs', '/test/a/docs', 'docs', 'local', 1, 'full')"
        )
        # 2 folders in ProjB
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, visibility) "
            "VALUES (3, 'lib', '/test/b/lib', 'lib', 'local', 2, 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, visibility) "
            "VALUES (4, 'bin', '/test/b/bin', 'bin', 'local', 2, 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("FolderCorp")

        assert result["total_folders"] == 4

    def test_client_returns_entity_counts(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'EntityCorp', 'entitycorp', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (1, 'Proj1', 'listed', 1, 'full')"
        )
        # 5 emails
        for i in range(1, 6):
            cursor.execute(
                "INSERT INTO emails (id, message_id, thread_id, account, received_at, project_id) "
                f"VALUES ({i}, 'msg{i}', 'thread1', 'work', '2024-01-01', 1)"
            )
        # 3 chats
        for i in range(1, 4):
            cursor.execute(
                f"INSERT INTO chats (id, external_id, account, project_id) VALUES ({i}, 'chat{i}', 'personal', 1)"
            )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("EntityCorp")

        assert result["total_entities"]["emails"] == 5
        assert result["total_entities"]["chats"] == 3
        assert result["total_entities"]["visits"] == 0

    def test_client_no_projects_zero_entities(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'EmptyCorp', 'emptycorp', 'company', 'listed', 'full')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:1', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("EmptyCorp")

        assert result["total_folders"] == 0
        assert result["total_entities"] == {"emails": 0, "chats": 0, "visits": 0}

    def test_client_entity_counts_exclude_hidden(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type, status, visibility) "
            "VALUES (1, 'HiddenCorp', 'hiddencorp', 'company', 'listed', 'full')"
        )
        cursor.execute(
            "INSERT INTO projects (id, name, status, client_id, visibility) "
            "VALUES (1, 'CorpProj', 'listed', 1, 'full')"
        )
        # 3 visible emails + 2 hidden
        for i in range(1, 4):
            cursor.execute(
                "INSERT INTO emails (id, message_id, thread_id, account, received_at, project_id) "
                f"VALUES ({i}, 'msg{i}', 'thread1', 'work', '2024-01-01', 1)"
            )
        for i in range(4, 6):
            cursor.execute(
                "INSERT INTO emails (id, message_id, thread_id, account, received_at, project_id, visibility) "
                f"VALUES ({i}, 'msg{i}', 'thread1', 'work', '2024-01-01', 1, 'hidden')"
            )
        # 1 visible chat + 1 hidden
        cursor.execute("INSERT INTO chats (id, external_id, account, project_id) VALUES (1, 'chat1', 'personal', 1)")
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, project_id, visibility) "
            "VALUES (2, 'chat2', 'personal', 1, 'hidden')"
        )
        # 1 visible browser_visit + 1 hidden
        cursor.execute(
            "INSERT INTO visits (id, url, visit_time, browser, project_id) "
            "VALUES (1, 'https://ex1.com', '2024-01-01', 'safari', 1)"
        )
        cursor.execute(
            "INSERT INTO visits (id, url, visit_time, browser, project_id, visibility) "
            "VALUES (2, 'https://ex2.com', '2024-01-01', 'safari', 1, 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("HiddenCorp")

        assert result["total_entities"] == {"emails": 3, "chats": 1, "visits": 1}


# ---------------------------------------------------------------------------
# TestContexterFolder
# ---------------------------------------------------------------------------
class TestContexterFolder:
    """Tests for footprinter_folder (footprinter.mcp.tools.navigation)."""

    def test_folder_found_with_files(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source,"
            " direct_file_count, total_size_bytes, visibility) "
            "VALUES (1, 'folder', '/test/folder', 'test/folder',"
            " 'local', 2, 1500, 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, content_type, size_bytes, modified_at, source, status, folder_id) "
            "VALUES (1, 'file1.py', 'code', 500, '2024-01-01', 'local', 'listed', 1)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, content_type, size_bytes, modified_at, source, status, folder_id) "
            "VALUES (2, 'file2.txt', 'text', 1000, '2024-01-02', 'local', 'listed', 1)"
        )
        # Folder and file visibility must be set to visible (baseline is opaque)
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('folder:1', 'full')")
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'full')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/folder")

        assert result["path"] == "/test/folder"
        assert len(result["files"]) == 2

    def test_folder_not_found(self, mcp_db):
        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/nonexistent/path")

        assert result["error_code"] == "NOT_FOUND"

    def test_folder_hidden_returns_not_found(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility) "
            "VALUES (1, 'hidden', '/test/hidden', 'test/hidden', 'local', 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/hidden")

        assert result["error_code"] == "NOT_FOUND"

    def test_folder_file_visibility(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, visibility) "
            "VALUES (1, 'mixed', '/test/mixed', 'test/mixed', 'local', 2, 'full')"
        )
        # Visible file
        cursor.execute(
            "INSERT INTO files"
            " (id, name, content_type, size_bytes, modified_at, source, status, folder_id, visibility)"
            " VALUES (1, 'visible.py', 'code', 100, '2024-01-01', 'local', 'listed', 1, 'full')"
        )
        # Hidden file (excluded by SQL WHERE)
        cursor.execute(
            "INSERT INTO files"
            " (id, name, content_type, size_bytes, modified_at, source, status, folder_id, visibility)"
            " VALUES (2, 'hidden.py', 'code', 200, '2024-01-02', 'local', 'listed', 1, 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/mixed")

        # Hidden file excluded by SQL WHERE clause
        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "visible.py"

    def test_folder_suppressed_key(self, mcp_db):
        """Folder uses single 'suppressed' key, not per-type keys."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "total_size_bytes, visibility) "
            "VALUES (1, 'folder', '/test/folder', 'test/folder', 'local', 1, 500, 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, content_type, size_bytes, modified_at, source, status, "
            "folder_id, visibility) "
            "VALUES (1, 'file.py', 'code', 100, '2024-01-01', 'local', 'listed', 1, 'full')"
        )
        mcp_db.commit()

        def mock_filter(item_type, results, id_key="id"):
            return results, 1  # Simulate 1 suppressed per filter call

        with (
            patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db,
            patch("footprinter.services.folder_service.filter_results_list", side_effect=mock_filter),
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/folder")

        # Single 'suppressed' total, not per-type keys
        assert result["suppressed"] == 2
        assert "files_suppressed" not in result
        assert "subfolders_suppressed" not in result

    def test_folder_recursive_file_count(self, mcp_db):
        cursor = mcp_db.cursor()
        # Root folder with 2 files
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "visibility) VALUES (1, 'root', '/test/root', 'test/root', 'local', 2, 'full')"
        )
        # Sub folder with 3 files
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "parent_folder_id, visibility) "
            "VALUES (2, 'sub', '/test/root/sub', 'test/root/sub', 'local', 3, 1, 'full')"
        )
        # Deep folder with 1 file
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, direct_file_count, "
            "parent_folder_id, visibility) "
            "VALUES (3, 'deep', '/test/root/sub/deep', 'test/root/sub/deep', 'local', 1, 2, 'full')"
        )
        # 2 files in root
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (1, 'a.py', 'local', 'listed', 1)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (2, 'b.py', 'local', 'listed', 1)"
        )
        # 3 files in sub
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (3, 'c.py', 'local', 'listed', 2)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (4, 'd.py', 'local', 'listed', 2)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (5, 'e.py', 'local', 'listed', 2)"
        )
        # 1 file in deep
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (6, 'f.py', 'local', 'listed', 3)"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/root")

        assert result["recursive_file_count"] == 6

    def test_folder_recursive_count_excludes_removed(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility) "
            "VALUES (1, 'root', '/test/root', 'test/root', 'local', 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, parent_folder_id, visibility) "
            "VALUES (2, 'sub', '/test/root/sub', 'test/root/sub', 'local', 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (1, 'a.py', 'local', 'listed', 1)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (2, 'b.py', 'local', 'removed', 2)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (3, 'c.py', 'local', 'listed', 2)"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/root")

        assert result["recursive_file_count"] == 2  # removed file excluded

    def test_folder_recursive_count_leaf_folder(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility) "
            "VALUES (1, 'leaf', '/test/leaf', 'test/leaf', 'local', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (1, 'x.py', 'local', 'listed', 1)"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, folder_id) VALUES (2, 'y.py', 'local', 'listed', 1)"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/leaf")

        assert result["recursive_file_count"] == 2

    # -- unlisted count tests --

    def test_folder_unlisted_file_count_viewer(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source,"
            " direct_file_count, total_size_bytes, visibility) "
            "VALUES (1, 'dotfolder', '/test/.claude', 'test/.claude',"
            " 'local', 0, 0, 'full')"
        )
        cursor.executemany(
            "INSERT INTO files (id, name, source, status, folder_id, visibility)"
            " VALUES (?, ?, 'local', ?, 1, 'full')",
            [
                (1, "listed1.py", "listed"),
                (2, "listed2.py", "listed"),
                (3, "unlisted1.py", "unlisted"),
                (4, "unlisted2.py", "unlisted"),
                (5, "unlisted3.py", "unlisted"),
            ],
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/.claude")

        assert result["unlisted_file_count"] == 3
        assert result["unlisted_recursive_file_count"] == 3
        assert len(result["files"]) == 2
        file_names = {f["name"] for f in result["files"]}
        assert "unlisted1.py" not in file_names

    def test_folder_unlisted_recursive_count_with_subfolders(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility)"
            " VALUES (1, 'root', '/test/root', 'test/root', 'local', 'full')"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source,"
            " parent_folder_id, visibility)"
            " VALUES (2, 'sub', '/test/root/sub', 'test/root/sub', 'local', 1, 'full')"
        )
        cursor.executemany(
            "INSERT INTO files (id, name, source, status, folder_id, visibility)"
            " VALUES (?, ?, 'local', ?, ?, 'full')",
            [
                (1, "a.py", "listed", 1),
                (2, "b.py", "unlisted", 1),
                (3, "c.py", "listed", 2),
                (4, "d.py", "listed", 2),
                (5, "e.py", "unlisted", 2),
                (6, "f.py", "unlisted", 2),
            ],
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/root")

        assert result["recursive_file_count"] == 3
        assert result["unlisted_file_count"] == 1
        assert result["unlisted_recursive_file_count"] == 3

    def test_folder_unlisted_count_no_metadata_leak(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility)"
            " VALUES (1, 'dot', '/test/.dot', 'test/.dot', 'local', 'full')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, source, status, status_reason,"
            " folder_id, visibility)"
            " VALUES (1, 'secret.py', 'local', 'unlisted', 'user_hidden', 1, 'full')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/.dot")

        assert result["unlisted_file_count"] == 1
        assert len(result["files"]) == 0
        for f in result.get("files", []):
            assert f["name"] != "secret.py"

    def test_opaque_folder_surfaces_unlisted_counts(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility)"
            " VALUES (1, 'opaque', '/test/opaque', 'test/opaque', 'local', 'opaque')"
        )
        cursor.executemany(
            "INSERT INTO files (id, name, source, status, folder_id, visibility)"
            " VALUES (?, ?, 'local', 'unlisted', 1, 'full')",
            [(1, "a.py"), (2, "b.py"), (3, "c.py")],
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/test/opaque")

        assert result["unlisted_file_count"] == 3
        assert result["unlisted_recursive_file_count"] == 3
        assert "files" not in result
        assert "subfolders" not in result


# ---------------------------------------------------------------------------
# TestNavigationModuleRename
# ---------------------------------------------------------------------------
class TestNavigationModuleRename:
    """Test that the navigation module is importable under its new name."""

    def test_navigation_module_importable(self):
        mod = importlib.import_module("footprinter.mcp.tools.navigation")
        assert hasattr(mod, "footprinter_project")
        assert hasattr(mod, "footprinter_client")
        assert hasattr(mod, "footprinter_folder")


# ---------------------------------------------------------------------------
# TestFootprinterSemantic
# ---------------------------------------------------------------------------
class TestFootprinterSemantic:
    """Tests for footprinter_semantic (footprinter.mcp.tools.semantic)."""

    # --- Validation (2) ---

    def test_short_query_error(self, mcp_db):
        from footprinter.mcp.tools.semantic import footprinter_semantic

        result = footprinter_semantic("ab")
        assert result["error_code"] == "QUERY_INVALID"

    def test_invalid_source_error(self, mcp_db):
        from footprinter.mcp.tools.semantic import footprinter_semantic

        result = footprinter_semantic("test query", source="invalid")
        assert result["error_code"] == "INVALID_INPUT"

    # --- Source routing (3) ---

    def test_source_chats_only(self, mcp_db):
        """source='chats' returns chats key only, no files."""
        mock_store = MagicMock()
        mock_store.search_chats.return_value = []
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert "chats" in result
        assert "files" not in result
        assert "summary" in result

    def test_source_files_only(self, mcp_db):
        """source='files' returns files key only, no chats."""
        mock_store = MagicMock()
        mock_store.search_files.return_value = []
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="files")

        assert "files" in result
        assert "chats" not in result
        assert "summary" in result

    def test_source_all_returns_both(self, mcp_db):
        """source='all' (default) returns both chats and files."""
        mock_store = MagicMock()
        mock_store.search_chats.return_value = []
        mock_store.search_files.return_value = []
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query")

        assert "chats" in result
        assert "files" in result
        assert "summary" in result

    # --- Chat behavior (6) ---

    def test_chat_successful_search(self, mcp_db):
        """Successful chat vector search returns results with expected keys."""
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Test Chat",
                "snippet": "relevant text",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            }
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Test Chat', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert result["query"] == "test query"
        assert len(result["chats"]) == 1

    def test_chat_fts5_fallback(self, mcp_db):
        """When vector search fails, falls back to FTS5 with note."""
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.side_effect = Exception("ChromaDB not available")

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert "error_code" not in result
        assert "keyword-based" in result.get("note", "")

    def test_chat_visibility_filtering(self, mcp_db):
        """Hidden chats are excluded from results."""
        mock_results = [
            {"chat_id": 1, "chat_title": "Allowed", "snippet": "ok", "relevance_score": 0.9},
            {"chat_id": 2, "chat_title": "Secret", "snippet": "nope", "relevance_score": 0.8},
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Allowed', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility) "
            "VALUES (2, 'conv-2', 'claude', 'Secret', 'hidden')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert len(result["chats"]) == 1
        returned_ids = [c["chat_id"] for c in result["chats"]]
        assert 1 in returned_ids
        assert 2 not in returned_ids
        assert result.get("suppressed") == 1

    def test_chat_permission_denied_excluded(self, mcp_db):
        """Visible chat with access='deny' is excluded entirely (D2).

        Semantic matches are content-derived — presence in results reveals
        content. Denied items don't appear at all, unlike footprinter_search
        which strips content but keeps metadata.
        """
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Secret Chat",
                "snippet": "sensitive",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            },
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-secret', 'claude', 'Secret Chat', 'full', 'deny')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert len(result["chats"]) == 0

    def test_chat_internal_fields_trimmed(self, mcp_db):
        """Internal ranking/chunking fields are stripped from chat results."""
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Test Chat",
                "message_id": "msg-1",
                "role": "user",
                "source": "claude",
                "created_at": "2026-01-01",
                "snippet": "relevant text",
                "relevance_score": 0.85,
                "chunk_type": "message",
                "chunk_index": 0,
                "total_chunks": 3,
                "semantic_rank": 1,
                "keyword_rank": 2,
                "rrf_score": 0.0328,
            }
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Test Chat', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        chat = result["chats"][0]
        allowed = {"chat_id", "chat_title", "snippet", "relevance_score", "source", "created_at", "message_id"}
        assert set(chat.keys()) == allowed
        for field in (
            "chunk_type",
            "chunk_index",
            "total_chunks",
            "semantic_rank",
            "keyword_rank",
            "rrf_score",
            "role",
        ):
            assert field not in chat

    def test_chat_summary_with_results(self, mcp_db):
        """Chat results include a summary with count and top titles."""
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "CI/CD Pipeline Setup",
                "source": "claude",
                "created_at": "2026-01-01",
                "snippet": "text",
                "relevance_score": 0.9,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'CI/CD Pipeline Setup', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("deployment architecture", source="chats")

        assert "summary" in result
        assert "1 chat" in result["summary"]

    # --- File behavior (7) ---

    def test_file_successful_search(self, mcp_db):
        """Successful file vector search returns results with expected fields."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/report.txt",
                "chunk_index": 0,
                "total_chunks": 2,
                "content_snippet": "Quarterly revenue analysis",
                "distance": 0.3,
            }
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'report.txt', '/Users/test/Work/report.txt', "
            "'text', 1024, '2026-02-15', 'listed', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("revenue analysis", source="files")

        assert result["query"] == "revenue analysis"
        f = result["files"][0]
        for field in ("id", "name", "path", "content_type", "size_bytes", "modified_at", "relevance_score", "snippet"):
            assert field in f, f"Missing field: {field}"

    def test_file_fts5_fallback(self, mcp_db):
        """When vector search fails for files, falls back to FTS5 with note."""
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.side_effect = Exception("ChromaDB not available")

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, content_preview, visibility, access) "
            "VALUES (1, 'local', 'revenue.txt', '/Users/test/Work/revenue.txt', "
            "'text', 512, '2026-02-15', 'listed', 'Revenue data for Q3', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO files_fts (rowid, name, content_preview) "
            "VALUES (1, 'revenue.txt', 'Revenue data for Q3')"
        )
        mcp_db.commit()

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("revenue", source="files")

        assert "error_code" not in result
        assert "keyword-based" in result.get("note", "")

    def test_file_deduplicates_chunks(self, mcp_db):
        """Multiple chunks from same file are deduplicated to one result."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/big.txt",
                "chunk_index": 0,
                "total_chunks": 3,
                "content_snippet": "First chunk",
                "distance": 0.5,
            },
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/big.txt",
                "chunk_index": 2,
                "total_chunks": 3,
                "content_snippet": "Better match",
                "distance": 0.2,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'big.txt', '/Users/test/Work/big.txt', 'text', 2048, "
            "'2026-02-15', 'listed', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("chunk content", source="files")

        assert len(result["files"]) == 1

    def test_file_visibility_filtering(self, mcp_db):
        """Hidden files are excluded from results."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/visible.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Visible",
                "distance": 0.3,
            },
            {
                "file_id": 2,
                "file_path": "/Users/test/Work/hidden.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Hidden",
                "distance": 0.4,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'visible.txt', '/Users/test/Work/visible.txt', 'text', "
            "512, '2026-02-15', 'listed', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility) "
            "VALUES (2, 'local', 'hidden.txt', '/Users/test/Work/hidden.txt', 'text', "
            "512, '2026-02-15', 'listed', 'hidden')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("some content", source="files")

        assert len(result["files"]) == 1

    def test_file_permission_denied_excluded(self, mcp_db):
        """Visible but permission-denied file is excluded entirely (D2)."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/secret.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "confidential",
                "distance": 0.3,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'secret.txt', '/Users/test/Work/secret.txt', "
            "'text', 1024, '2026-02-15', 'listed', 'full', 'deny')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("revenue data", source="files")

        assert len(result["files"]) == 0

    def test_file_summary_with_results(self, mcp_db):
        """File results include summary with count and file names."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/report.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Content",
                "distance": 0.3,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'report.txt', '/Users/test/Work/report.txt', 'text', "
            "1024, '2026-02-15', 'listed', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("report query", source="files")

        assert "summary" in result
        assert "1" in result["summary"]
        assert "report.txt" in result["summary"]

    def test_deduplicate_returns_dropped_count(self):
        """_deduplicate_by_file returns (results, dropped_count) tuple."""
        from footprinter.services.semantic_service import _deduplicate_by_file

        results = [
            {"file_id": 1, "relevance_score": 0.9},
            {"file_id": 2, "relevance_score": 0.8},
            {"file_id": None, "relevance_score": 0.7},
        ]
        deduped, dropped = _deduplicate_by_file(results)
        assert len(deduped) == 2
        assert dropped == 1

    # --- Visibility consistency ---

    def test_chat_opaque_excluded_from_semantic(self, mcp_db):
        """Opaque chats are excluded entirely from semantic results (D2 rule).

        Spec: reference/permission-policies-and-access-control.md lines 307, 310, 348-353.
        Semantic search is stricter than metadata search — opaque items are
        dropped, not field-trimmed, because match relevance is content-derived.
        """
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Opaque Chat",
                "snippet": "content",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            },
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Opaque Chat', 'opaque', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert len(result["chats"]) == 0

    def test_file_opaque_excluded_from_semantic(self, mcp_db):
        """Opaque files are excluded entirely from semantic results (D2 rule).

        Spec: reference/permission-policies-and-access-control.md lines 307, 310, 348-353.
        """
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/opaque.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "sensitive content",
                "distance": 0.3,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'opaque.txt', '/Users/test/Work/opaque.txt', "
            "'text', 1024, '2026-02-15', 'listed', 'opaque', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="files")

        assert len(result["files"]) == 0

    def test_file_fts5_fallback_excludes_opaque(self, mcp_db):
        """FTS5 fallback also excludes opaque files (same D2 rule as vector path)."""
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.side_effect = Exception("ChromaDB not available")

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, content_preview, visibility, access) "
            "VALUES (1, 'local', 'opaque.txt', '/Users/test/Work/opaque.txt', "
            "'text', 512, '2026-02-15', 'listed', 'Opaque content here', 'opaque', 'allow')"
        )
        cursor.execute(
            "INSERT INTO files_fts (rowid, name, content_preview) "
            "VALUES (1, 'opaque.txt', 'Opaque content here')"
        )
        mcp_db.commit()

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("opaque", source="files")

        assert len(result["files"]) == 0

    def test_chat_opaque_counted_in_suppressed(self, mcp_db):
        """Opaque chats excluded from semantic results are counted in `suppressed`.

        D2 rule (reference/permission-policies-and-access-control.md lines 307, 310, 348-353) says
        opaque items are excluded from semantic search — stricter than metadata
        search where opaque items return with minimal fields. The `suppressed`
        count therefore includes opaque items alongside hidden ones in the
        semantic-search context.
        """
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Visible",
                "snippet": "ok",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            },
            {
                "chat_id": 2,
                "chat_title": "Opaque",
                "snippet": "nope",
                "relevance_score": 0.8,
                "source": "claude",
                "created_at": "2026-01-01",
            },
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Visible', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (2, 'conv-2', 'claude', 'Opaque', 'opaque', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert len(result["chats"]) == 1
        assert result.get("suppressed") == 1

    def test_file_opaque_counted_in_suppressed(self, mcp_db):
        """Opaque files excluded from semantic results are counted in `suppressed`."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/visible.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Visible",
                "distance": 0.3,
            },
            {
                "file_id": 2,
                "file_path": "/Users/test/Work/opaque.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Opaque",
                "distance": 0.4,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'visible.txt', '/Users/test/Work/visible.txt', 'text', "
            "512, '2026-02-15', 'listed', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (2, 'local', 'opaque.txt', '/Users/test/Work/opaque.txt', 'text', "
            "512, '2026-02-15', 'listed', 'opaque', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="files")

        assert len(result["files"]) == 1
        assert result.get("suppressed") == 1

    def test_chat_hidden_suppressed_count(self, mcp_db):
        """Hidden chats are excluded and counted in suppressed."""
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Visible",
                "snippet": "ok",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            },
            {
                "chat_id": 2,
                "chat_title": "Hidden",
                "snippet": "nope",
                "relevance_score": 0.8,
                "source": "claude",
                "created_at": "2026-01-01",
            },
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Visible', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility) "
            "VALUES (2, 'conv-2', 'claude', 'Hidden', 'hidden')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert len(result["chats"]) == 1
        assert result.get("suppressed") == 1

    def test_file_hidden_suppressed_count(self, mcp_db):
        """Hidden files are excluded and counted in suppressed."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/visible.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Visible",
                "distance": 0.3,
            },
            {
                "file_id": 2,
                "file_path": "/Users/test/Work/hidden.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Hidden",
                "distance": 0.4,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'visible.txt', '/Users/test/Work/visible.txt', 'text', "
            "512, '2026-02-15', 'listed', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility) "
            "VALUES (2, 'local', 'hidden.txt', '/Users/test/Work/hidden.txt', 'text', "
            "512, '2026-02-15', 'listed', 'hidden')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="files")

        assert len(result["files"]) == 1
        assert result.get("suppressed") == 1

    # --- Combined mode (2) ---

    def test_combined_returns_both_summaries(self, mcp_db):
        """source='all' combines chat and file summaries."""
        mock_store = MagicMock()
        mock_store.search_chats.return_value = [
            {
                "chat_id": 1,
                "chat_title": "Chat One",
                "snippet": "text",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            },
        ]
        mock_store.search_files.return_value = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/file.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "content",
                "distance": 0.3,
            },
        ]

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Chat One', 'full', 'allow')"
        )
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'file.txt', '/Users/test/Work/file.txt', 'text', "
            "512, '2026-02-15', 'listed', 'full', 'allow')"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query")

        assert len(result["chats"]) == 1
        assert len(result["files"]) == 1
        assert "chat" in result["summary"].lower()
        assert "file" in result["summary"].lower()

    def test_combined_fallback_notes_merged(self, mcp_db):
        """When both collections fall back to FTS5, notes are merged."""
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.side_effect = Exception("ChromaDB not available")

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query")

        assert "error_code" not in result
        assert "keyword-based" in result.get("note", "")

    # --- Permission enforcement (3) ---

    def test_file_fts5_permission_denied_excluded(self, mcp_db):
        """FTS5 fallback excludes visible+denied files entirely (D2)."""
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.side_effect = Exception("ChromaDB not available")

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, content_preview, visibility, access) "
            "VALUES (1, 'local', 'secret.txt', '/Users/test/Work/secret.txt', "
            "'text', 512, '2026-02-15', 'listed', 'Confidential data here', 'full', 'deny')"
        )
        cursor.execute(
            "INSERT INTO files_fts (rowid, name, content_preview) "
            "VALUES (1, 'secret.txt', 'Confidential data here')"
        )
        mcp_db.commit()

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("secret", source="files")

        assert len(result["files"]) == 0

    def test_chat_permission_null_excluded(self, mcp_db):
        """Chat with access=NULL is excluded from semantic results (fail-closed, D2)."""
        mock_results = [
            {
                "chat_id": 1,
                "chat_title": "Unresolved Chat",
                "snippet": "content here",
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "2026-01-01",
            },
        ]
        mock_store = MagicMock()
        mock_store.search_chats.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, visibility, access) "
            "VALUES (1, 'conv-1', 'claude', 'Unresolved Chat', 'full', NULL)"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="chats")

        assert len(result["chats"]) == 0

    def test_file_permission_null_excluded(self, mcp_db):
        """File with access=NULL is excluded from semantic results (fail-closed, D2)."""
        mock_results = [
            {
                "file_id": 1,
                "file_path": "/Users/test/Work/unresolved.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "unresolved content",
                "distance": 0.3,
            },
        ]
        mock_store = MagicMock()
        mock_store.search_files.return_value = mock_results

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, "
            "modified_at, status, visibility, access) "
            "VALUES (1, 'local', 'unresolved.txt', '/Users/test/Work/unresolved.txt', "
            "'text', 512, '2026-02-15', 'listed', 'full', NULL)"
        )
        mcp_db.commit()

        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.semantic import footprinter_semantic

            result = footprinter_semantic("test query", source="files")

        assert len(result["files"]) == 0


# ---------------------------------------------------------------------------
# TestContexterRead
# ---------------------------------------------------------------------------
class TestContexterRead:
    """Tests for footprinter_read (footprinter.mcp.tools.read)."""

    def test_read_nonexistent_not_found(self, mcp_db):
        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 999)

        assert result["error_code"] == "NOT_FOUND"

    def test_read_hidden_not_found(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, body_preview, visibility) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Secret',"
            " 'a@b.com', '2024-01-01', 'secret content', 'hidden')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        # Hidden items indistinguishable from nonexistent
        assert result["error_code"] == "NOT_FOUND"

    def test_read_removed_returns_not_found(self, mcp_db):
        """File with status='removed' must return NOT_FOUND for VIEWER."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status, content_type,"
            " size_bytes, visibility, access) "
            "VALUES (1, 'gone.md', '/Users/u/gone.md', 'local', 'removed',"
            " 'markdown', 100, 'full', 'allow')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("file", 1)

        assert result["error_code"] == "NOT_FOUND"

    def test_read_unlisted_returns_not_found(self, mcp_db):
        """File with status='unlisted' must return NOT_FOUND for VIEWER."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status, content_type,"
            " size_bytes, visibility, access) "
            "VALUES (1, 'shh.md', '/Users/u/shh.md', 'local', 'unlisted',"
            " 'markdown', 100, 'full', 'allow')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("file", 1)

        assert result["error_code"] == "NOT_FOUND"

    def test_unresolved_visibility_defaults_opaque(self, mcp_db):
        """Email without visibility (defaults to inherit) should be opaque."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, received_at, body_preview) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Unresolved', 'a@b.com', '2024-01-01', 'body')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert result["error_code"] == "VISIBILITY_RESTRICTED"
        assert "metadata" in result
        assert "subject" not in result["metadata"]

    def test_read_opaque_visibility_restricted(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, body_preview, visibility) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Opaque Subject',"
            " 'x@y.com', '2024-01-01', 'opaque body', 'opaque')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert result["error_code"] == "VISIBILITY_RESTRICTED"
        assert "metadata" in result
        assert "id" in result["metadata"]
        assert "account" in result["metadata"]
        # Opaque should NOT include subject
        assert "subject" not in result["metadata"]

    def test_read_permission_denied(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject,"
            " from_address, received_at, body_preview, visibility, access) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Denied',"
            " 'a@b.com', '2024-01-01', 'denied body', 'full', 'deny')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert result["error_code"] == "PERMISSION_DENIED"
        assert "metadata" in result
        assert result["metadata"].get("id") == 1
        assert result["metadata"].get("account") == "personal"
        assert "subject" not in result["metadata"]
        assert "from_address" not in result["metadata"]

    def test_read_email_success(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, from_name, "
            "to_addresses, received_at, body_preview, visibility) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Hello World', 'sender@test.com', 'Sender Name', "
            "'recipient@test.com', '2024-01-15', 'This is the email body.', 'full')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:emails', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert "error" not in result
        assert result["content"] == "This is the email body."
        assert result["metadata"]["subject"] == "Hello World"
        assert result["metadata"]["account"] == "personal"
        # body_preview should NOT be in metadata (it's the content)
        assert "body_preview" not in result["metadata"]

    def test_read_email_includes_project_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, from_name, "
            "to_addresses, received_at, body_preview, client_id, project_id, visibility) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Acme Update', 'bob@acme.com', 'Bob', "
            "'alice@test.com', '2024-01-15', 'Project update body.', 1, 1, 'full')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:emails', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert "error" not in result
        assert result["metadata"]["project_name"] == "AcmeWeb"
        assert result["metadata"]["client_name"] == "Acme Corp"

    def test_read_email_null_project_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, from_name, "
            "to_addresses, received_at, body_preview, visibility) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal', 'Personal email', 'sender@test.com', 'Sender', "
            "'me@test.com', '2024-01-15', 'Body text.', 'full')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:emails', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert "error" not in result
        assert result["metadata"]["project_name"] is None
        assert result["metadata"]["client_name"] is None

    def test_read_chat_includes_project_client(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO chats "
            "(id, external_id, account, title, created_at, message_count, client_id, project_id, visibility) "
            "VALUES (1, 'conv-uuid-1', 'claude', 'Acme Chat', '2024-01-15', 1, 1, 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (1, 'user', 'Hello!', '2024-01-15 10:00:00')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:chats', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert "error" not in result
        assert result["metadata"]["project_name"] == "AcmeWeb"
        assert result["metadata"]["client_name"] == "Acme Corp"

    def test_read_email_denied_metadata_excludes_sensitive_fields(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account,"
            " subject, from_address, received_at, body_preview,"
            " client_id, project_id, visibility, access) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal',"
            " 'Denied Email', 'a@b.com', '2024-01-15', 'body',"
            " 1, 1, 'full', 'deny')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert result["error_code"] == "PERMISSION_DENIED"
        assert result["metadata"].get("id") == 1
        assert result["metadata"].get("account") == "personal"
        assert "subject" not in result["metadata"]
        assert "from_address" not in result["metadata"]

    def test_read_chat_success(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats "
            "(id, external_id, account, title, created_at, message_count, visibility) "
            "VALUES (1, 'conv-uuid-1', 'claude', 'Test Chat', '2024-01-15', 2, 'full')"
        )
        cursor.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (1, 'user', 'Hello!', '2024-01-15 10:00:00')"
        )
        cursor.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (1, 'assistant', 'Hi there!', '2024-01-15 10:01:00')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:chats', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert "error" not in result
        assert "Hello!" in result["content"]
        assert "Hi there!" in result["content"]
        assert result["metadata"]["title"] == "Test Chat"

    def test_permission_denied_returns_opaque_metadata(self, mcp_db):
        """PERMISSION_DENIED should return only opaque metadata, not sensitive fields."""
        cursor = mcp_db.cursor()

        # File with sensitive fields
        cursor.execute(
            "INSERT INTO files (id, name, path, content_type, source,"
            " size_bytes, modified_at, visibility, access) "
            "VALUES (1, 'secret.pdf', '/home/user/secret.pdf',"
            " 'application/pdf', 'local', 1024, '2024-01-15',"
            " 'full', 'deny')"
        )
        # Email with sensitive fields
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Acme Corp', 'acme', 'external')")
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account,"
            " subject, from_address, received_at, body_preview,"
            " client_id, project_id, visibility, access) "
            "VALUES (1, 'msg-1', 'thread-1', 'personal',"
            " 'Confidential', 'secret@corp.com', '2024-01-15',"
            " 'body', 1, 1, 'full', 'deny')"
        )
        # Chat with sensitive fields
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title,"
            " created_at, message_count, client_id, project_id,"
            " visibility, access) "
            "VALUES (1, 'conv-uuid-1', 'claude', 'Secret Chat',"
            " '2024-01-15', 5, 1, 1,"
            " 'full', 'deny')"
        )
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            # --- File: opaque fields are id, content_type, source ---
            result = footprinter_read("file", 1)
            assert result["error_code"] == "PERMISSION_DENIED"
            meta = result["metadata"]
            assert meta["id"] == 1
            assert meta["content_type"] == "application/pdf"
            assert meta["source"] == "local"
            # Sensitive fields must be absent
            for field in ("name", "path", "size_bytes", "modified_at"):
                assert field not in meta, f"file: sensitive field '{field}' leaked"
            # Internal fields must be absent
            for field in ("visibility", "access"):
                assert field not in meta, f"file: internal field '{field}' leaked"

            # --- Email: opaque fields are id, account ---
            result = footprinter_read("email", 1)
            assert result["error_code"] == "PERMISSION_DENIED"
            meta = result["metadata"]
            assert meta["id"] == 1
            assert meta["account"] == "personal"
            for field in ("subject", "from_address"):
                assert field not in meta, f"email: sensitive field '{field}' leaked"
            for field in ("client_id", "project_id"):
                assert field in meta, f"email: opaque FK field '{field}' should be present"
            for field in ("visibility", "access"):
                assert field not in meta, f"email: internal field '{field}' leaked"

            # --- Chat: opaque fields are id, account ---
            result = footprinter_read("chat", 1)
            assert result["error_code"] == "PERMISSION_DENIED"
            meta = result["metadata"]
            assert meta["id"] == 1
            assert meta["account"] == "claude"
            for field in ("title", "external_id"):
                assert field not in meta, f"chat: sensitive field '{field}' leaked"
            for field in ("client_id", "project_id"):
                assert field in meta, f"chat: opaque FK field '{field}' should be present"
            for field in ("visibility", "access"):
                assert field not in meta, f"chat: internal field '{field}' leaked"

    def test_read_file_hoists_identity_fields(self, mcp_db):
        """File read should hoist name/path/source/created_at/modified_at/project_name before content."""
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status, content_type,"
            " size_bytes, created_at, modified_at, visibility, access, project_id) "
            "VALUES (1, 'readme.md', '/Users/u/readme.md', 'local', 'listed',"
            " 'markdown', 100, '2024-01-15', '2024-01-16', 'full', 'allow', 1)"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:local', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:local', 'allow')")
        mcp_db.commit()

        with (
            patch("footprinter.mcp.tools.read.get_db") as mock_get_db,
            patch("footprinter.mcp.tools.read.content_service.read_file") as mock_read,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            mock_read.return_value = {
                "status": "ok",
                "content": "# Hello",
                "metadata": {
                    "id": 1,
                    "name": "readme.md",
                    "path": "/Users/u/readme.md",
                    "source": "local",
                    "created_at": "2024-01-15",
                    "modified_at": "2024-01-16",
                    "project_name": "AcmeWeb",
                    "content_type": "markdown",
                },
            }

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("file", 1)

        assert "error" not in result
        # Verify content_service.read_file was called with gate_access metadata
        mock_read.assert_called_once()
        gate_meta = mock_read.call_args[0][1]
        assert gate_meta["name"] == "readme.md"
        assert gate_meta["project_name"] == "AcmeWeb"
        keys = list(result.keys())
        expected_leading = ["name", "path", "source", "created_at", "modified_at", "project_name"]
        assert keys[: len(expected_leading)] == expected_leading
        assert keys[len(expected_leading)] == "content"
        # Non-breaking: fields still in metadata
        for field in expected_leading:
            assert field in result["metadata"]

    def test_read_email_hoists_identity_fields(self, mcp_db):
        """Email read should hoist subject/from_address/from_name/account/received_at/project_name."""
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, from_address, from_name, "
            "to_addresses, received_at, body_preview, project_id, visibility, access) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Weekly Update', 'bob@acme.com', 'Bob', "
            "'alice@test.com', '2024-01-15', 'Email body here.', 1, 'full', 'allow')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:emails', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("email", 1)

        assert "error" not in result
        keys = list(result.keys())
        expected_leading = ["subject", "from_address", "from_name", "account", "received_at", "project_name"]
        assert keys[: len(expected_leading)] == expected_leading
        assert keys[len(expected_leading)] == "content"
        for field in expected_leading:
            assert field in result["metadata"]

    def test_read_chat_hoists_identity_fields(self, mcp_db):
        """Chat read should hoist title/account/created_at/project_name."""
        cursor = mcp_db.cursor()
        cursor.execute("INSERT INTO projects (id, name) VALUES (1, 'AcmeWeb')")
        cursor.execute(
            "INSERT INTO chats "
            "(id, external_id, account, title, created_at, message_count, project_id, visibility) "
            "VALUES (1, 'conv-uuid-1', 'claude', 'Design Chat', '2024-01-15', 1, 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (1, 'user', 'Hello!', '2024-01-15 10:00:00')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:chats', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert "error" not in result
        keys = list(result.keys())
        expected_leading = ["title", "account", "created_at", "project_name"]
        assert keys[: len(expected_leading)] == expected_leading
        assert keys[len(expected_leading)] == "content"
        for field in expected_leading:
            assert field in result["metadata"]

    def test_read_hoisted_fields_none_when_missing(self, mcp_db):
        """Hoisted project_name should be None (not absent) when no project is set."""
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO chats "
            "(id, external_id, account, title, created_at, message_count, visibility) "
            "VALUES (1, 'conv-uuid-1', 'claude', 'Orphan Chat', '2024-01-15', 1, 'full')"
        )
        cursor.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (1, 'user', 'Hi', '2024-01-15 10:00:00')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:chats', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        mcp_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert "error" not in result
        assert "project_name" in result
        assert result["project_name"] is None


# ---------------------------------------------------------------------------
# TestPathContainment
# ---------------------------------------------------------------------------
class TestPathContainment:
    """Tests for path containment validation in read helpers."""

    # --- _validate_local_path unit tests (5) ---

    def test_validate_path_within_home_succeeds(self, tmp_path):
        """Path under home directory returns resolved Path."""
        from pathlib import Path
        from unittest.mock import patch

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _validate_local_path

            result = _validate_local_path(str(test_file))

        assert result == test_file.resolve()

    def test_validate_path_outside_home_raises(self, tmp_path):
        """Path outside home raises PermissionError."""
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _validate_local_path

            with pytest.raises(PermissionError):
                _validate_local_path("/etc/passwd")

    def test_validate_path_traversal_raises(self, tmp_path):
        """Path with traversal that escapes home raises PermissionError."""
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _validate_local_path

            with pytest.raises(PermissionError):
                _validate_local_path(str(tmp_path / ".." / ".." / "etc" / "passwd"))

    def test_validate_path_root_raises(self, tmp_path):
        """Root path raises PermissionError."""
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _validate_local_path

            with pytest.raises(PermissionError):
                _validate_local_path("/")

    def test_validate_path_home_prefix_trick_raises(self, tmp_path):
        """Path that shares home prefix but is a sibling dir raises PermissionError."""
        from pathlib import Path
        from unittest.mock import patch

        evil_path = str(tmp_path) + "-evil/secret.txt"
        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _validate_local_path

            with pytest.raises(PermissionError):
                _validate_local_path(evil_path)

    # --- _read_local_file_bytes integration tests (2) ---

    def test_read_bytes_outside_home_returns_none(self, tmp_path, caplog):
        """Reading bytes from path outside home returns None and logs ERROR."""
        import logging
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _read_local_file_bytes

            with caplog.at_level(logging.ERROR):
                result = _read_local_file_bytes("/etc/passwd")

        assert result is None
        assert any("containment" in r.message.lower() for r in caplog.records)

    def test_read_bytes_inside_home_succeeds(self, tmp_path):
        """Reading bytes from path inside home works correctly."""
        from pathlib import Path
        from unittest.mock import patch

        test_file = tmp_path / "data.bin"
        test_file.write_bytes(b"binary content")

        with patch.object(Path, "home", return_value=tmp_path):
            from footprinter.services.content_service import _read_local_file_bytes

            result = _read_local_file_bytes(str(test_file))

        assert result == b"binary content"

    # --- Full-stack integration (1) ---

    def test_artifact_outside_home_returns_read_failed(self, mcp_db, tmp_path):
        """Artifact with path outside home returns READ_FAILED error."""
        from pathlib import Path
        from unittest.mock import patch

        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes, modified_at, visibility) "
            "VALUES (1, 'local', 'passwd', '/etc/passwd', 'config', 100, '2024-01-15', 'full')"
        )
        cursor.execute("INSERT OR IGNORE INTO sources (name, source_type) VALUES ('local', 'local')")
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'full')")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        mcp_db.commit()

        with (
            patch("footprinter.mcp.tools.read.get_db") as mock_get_db,
            patch.object(Path, "home", return_value=tmp_path),
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("file", 1)

        assert result["error_code"] == "READ_FAILED"


# ---------------------------------------------------------------------------
# TestIncludeFlagsForwarding
# ---------------------------------------------------------------------------
class TestIncludeFlagsForwarding:
    """The three discovery tools accept include_unlisted/include_removed and
    forward them verbatim to the service layer.

    MCP entry points always run as VIEWER, so the flags pass through but the
    service layer ignores them. These tests assert the wiring, not VIEWER
    semantics (which are covered at the service layer).
    """

    def test_search_forwards_include_flags(self, mcp_db):
        with (
            patch("footprinter.mcp.tools.search.get_db") as mock_get_db,
            patch("footprinter.mcp.tools.search.search_service.search") as mock_search,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            mock_search.return_value = {"files": []}

            from footprinter.mcp.tools.search import footprinter_search

            footprinter_search("query", include_unlisted=True, include_removed=True)

        kwargs = mock_search.call_args.kwargs
        assert kwargs["include_unlisted"] is True
        assert kwargs["include_removed"] is True

    def test_search_default_flags_false(self, mcp_db):
        with (
            patch("footprinter.mcp.tools.search.get_db") as mock_get_db,
            patch("footprinter.mcp.tools.search.search_service.search") as mock_search,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            mock_search.return_value = {"files": []}

            from footprinter.mcp.tools.search import footprinter_search

            footprinter_search("query")

        kwargs = mock_search.call_args.kwargs
        assert kwargs["include_unlisted"] is False
        assert kwargs["include_removed"] is False

    def test_folder_forwards_include_flags(self, mcp_db):
        cursor = mcp_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, visibility) "
            "VALUES (1, 'p', '/test/p', 'test/p', 'local', 'full')"
        )
        mcp_db.commit()

        with (
            patch("footprinter.mcp.tools.navigation.get_db") as mock_get_db,
            patch("footprinter.mcp.tools.navigation.folder_service.get_by_path") as mock_get,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            mock_get.return_value = {"path": "/test/p"}

            from footprinter.mcp.tools.navigation import footprinter_folder

            footprinter_folder("/test/p", include_unlisted=True)

        kwargs = mock_get.call_args.kwargs
        assert kwargs["include_unlisted"] is True
        assert kwargs.get("include_removed", False) is False

    def test_semantic_forwards_include_flags(self, mcp_db):
        with (
            patch("footprinter.mcp.tools.semantic.get_db") as mock_get_db,
            patch(
                "footprinter.mcp.tools.semantic.semantic_service.semantic_search"
            ) as mock_sem,
        ):
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            mock_sem.return_value = {"chats": [], "files": [], "summary": ""}

            from footprinter.mcp.tools.semantic import footprinter_semantic

            footprinter_semantic("hello world", include_removed=True)

        kwargs = mock_sem.call_args.kwargs
        assert kwargs["include_removed"] is True
        assert kwargs.get("include_unlisted", False) is False


# ---------------------------------------------------------------------------
# TestSearchSummary — _build_search_summary truncation formatting
# ---------------------------------------------------------------------------


class TestSearchSummary:
    """Unit tests for _build_search_summary truncation-aware formatting."""

    @staticmethod
    def _summary():
        from footprinter.mcp.tools.search import _build_search_summary

        return _build_search_summary

    def test_summary_shows_found_when_not_truncated(self):
        results = {"files": [{"id": i} for i in range(3)]}
        counts = {"files": {"returned": 3, "has_more": False}}
        summary = self._summary()(results, "test", ["files"], counts=counts)
        assert "Found 3 files" in summary

    def test_summary_shows_showing_when_truncated(self):
        results = {"files": [{"id": i} for i in range(10)]}
        counts = {"files": {"returned": 10, "has_more": True}}
        summary = self._summary()(results, "test", ["files"], counts=counts)
        assert "10 of 10+" in summary

    def test_summary_mixed_truncation_query_applies_to_all(self):
        results = {
            "files": [{"id": i} for i in range(10)],
            "emails": [{"id": i} for i in range(2)],
        }
        counts = {
            "files": {"returned": 10, "has_more": True},
            "emails": {"returned": 2, "has_more": False},
        }
        summary = self._summary()(
            results, "report", ["files", "emails"], counts=counts
        )
        assert "10 of 10+" in summary
        assert "2 emails" in summary
        assert "2 of 2+" not in summary
        assert summary.count("for 'report'") == 1
        assert summary.endswith("for 'report'.")

    def test_summary_no_counts_backward_compat(self):
        results = {"files": [{"id": i} for i in range(5)]}
        summary = self._summary()(results, "test", ["files"])
        assert "Found 5 files" in summary

    def test_was_capped_suppressed_when_not_truncated(self):
        results = {"files": [{"id": i} for i in range(5)]}
        counts = {"files": {"returned": 5, "has_more": False}}
        summary = self._summary()(
            results, "test", ["files"], was_capped=True, counts=counts
        )
        assert "capped" not in summary.lower()

    def test_was_capped_shown_when_truncated(self):
        results = {"files": [{"id": i} for i in range(10)]}
        counts = {"files": {"returned": 10, "has_more": True}}
        summary = self._summary()(
            results, "test", ["files"], was_capped=True, counts=counts
        )
        assert "capped" in summary.lower()


# ---------------------------------------------------------------------------
# TestSchemaConsistency — verify fixture matches production schema
# ---------------------------------------------------------------------------
