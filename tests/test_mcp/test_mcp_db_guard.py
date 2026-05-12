"""Tests for MCP database guard behavior.

Tests that:
1. get_db() raises DatabaseNotInitializedError on empty/uninitialized databases
2. handle_db_errors decorator converts DatabaseNotInitializedError to structured MCP errors
3. All MCP tools return structured errors (not raw exceptions) on empty DB
4. DB_NOT_INITIALIZED error code is properly registered
"""

import re
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from footprinter.mcp.db import (
    DatabaseNotInitializedError,
    get_db,
    handle_db_errors,
)
from footprinter.mcp.errors import ERROR_HINTS, ERROR_MESSAGES
from footprinter.mcp.tools.navigation import (
    footprinter_client,
    footprinter_folder,
    footprinter_project,
)
from footprinter.mcp.tools.read import footprinter_read
from footprinter.mcp.tools.search import footprinter_search
from footprinter.mcp.tools.semantic import footprinter_semantic
from footprinter.mcp.tools.status import footprinter_status


@pytest.fixture
def empty_db(tmp_path):
    """Create an empty SQLite file with no tables."""
    db_path = tmp_path / "empty.db"
    sqlite3.connect(str(db_path)).close()
    return db_path


class TestGetDbGuard:
    """Tests for the database initialization check in get_db()."""

    def test_get_db_raises_on_empty_db(self, empty_db):
        """Empty database (no tables) raises DatabaseNotInitializedError."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            with pytest.raises(DatabaseNotInitializedError):
                with get_db() as _conn:
                    pass

    def test_get_db_closes_connection_on_init_failure(self):
        """Connection is closed even when _check_db_initialized raises."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        with (
            patch("footprinter.mcp.db.sqlite3.connect", return_value=mock_conn),
            patch("footprinter.mcp.db.get_db_path", return_value="/fake/path.db"),
            patch("footprinter.mcp.db._check_db_initialized", side_effect=DatabaseNotInitializedError),
        ):
            with pytest.raises(DatabaseNotInitializedError):
                with get_db() as _conn:
                    pass
        mock_conn.close.assert_called_once()

    def test_get_db_succeeds_with_initialized_db(self, tool_db, tmp_path):
        """Initialized database (has files table) does not raise."""
        db_path = tmp_path / "test.db"
        with patch("footprinter.mcp.db.get_db_path", return_value=db_path):
            with get_db() as conn:
                assert conn is not None


class TestHandleDbErrorsDecorator:
    """Tests for the handle_db_errors decorator."""

    def _make_failing_fn(self):
        @handle_db_errors
        def dummy():
            raise DatabaseNotInitializedError()

        return dummy

    def test_handle_db_errors_returns_structured_error(self):
        """Decorator catches DatabaseNotInitializedError and returns structured MCP error."""
        result = self._make_failing_fn()()
        assert result["error_code"] == "DB_NOT_INITIALIZED"
        assert result["error"] == "Unpopulated"
        assert "hint" in result

    def test_handle_db_errors_no_sandbox_metadata(self):
        """Decorator returns no metadata key on the error response."""
        result = self._make_failing_fn()()
        assert "metadata" not in result


class TestToolsErrorOnEmptyDb:
    """Tests that all MCP tools return structured errors on uninitialized DB."""

    def test_status_tool_error_on_empty_db(self, empty_db):
        """footprinter_status returns structured error, not raw exception."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            result = footprinter_status()
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_navigation_tools_error_on_empty_db(self, empty_db):
        """Navigation tools return structured error on empty DB."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            assert footprinter_project("test")["error_code"] == "DB_NOT_INITIALIZED"
            assert footprinter_client("test")["error_code"] == "DB_NOT_INITIALIZED"
            assert footprinter_folder("~/test")["error_code"] == "DB_NOT_INITIALIZED"

    def test_search_tool_error_on_empty_db(self, empty_db):
        """footprinter_search returns structured error on empty DB."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            result = footprinter_search("test")
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_read_tool_error_on_empty_db(self, empty_db):
        """footprinter_read returns structured error on empty DB."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            result = footprinter_read("file", 1)
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_semantic_files_error_on_empty_db(self, empty_db):
        """footprinter_semantic(source='files') returns DB_NOT_INITIALIZED on empty DB."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            result = footprinter_semantic("test query", source="files")
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_semantic_all_error_on_empty_db(self, empty_db):
        """footprinter_semantic(source='all') returns DB_NOT_INITIALIZED on empty DB."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            result = footprinter_semantic("test query", source="all")
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_semantic_chats_error_on_empty_db(self, empty_db):
        """footprinter_semantic(source='chats') returns DB_NOT_INITIALIZED on empty DB."""
        with patch("footprinter.mcp.db.get_db_path", return_value=empty_db):
            result = footprinter_semantic("test query", source="chats")
        assert result["error_code"] == "DB_NOT_INITIALIZED"


class TestErrorCodeRegistration:
    """Tests for DB_NOT_INITIALIZED error code in error maps."""

    def test_error_hint_no_forbidden_words(self):
        """Default error hint avoids implementation-detail words."""
        forbidden = ["path", "database", "sql", "file", "folder"]

        hint = ERROR_HINTS["DB_NOT_INITIALIZED"]
        words = re.findall(r"\b\w+\b", hint.lower())
        for term in forbidden:
            assert term not in words, f"Default hint contains '{term}': {hint}"

    def test_new_error_code_registered(self):
        """DB_NOT_INITIALIZED is in both ERROR_MESSAGES and ERROR_HINTS."""
        assert "DB_NOT_INITIALIZED" in ERROR_MESSAGES
        assert "DB_NOT_INITIALIZED" in ERROR_HINTS
