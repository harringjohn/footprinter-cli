"""Tests for MCP database guard behavior.

Tests that:
1. get_db() raises DatabaseNotInitializedError on empty/uninitialized databases
2. handle_db_errors decorator converts DatabaseNotInitializedError to structured MCP errors
3. All MCP tools return structured errors (not raw exceptions) on empty DB
4. DB_NOT_INITIALIZED error code is properly registered
5. Transient schema error classifiers correctly categorize OperationalError messages
6. handle_db_errors retries transient schema errors on fresh connections
"""

import re
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from footprinter.mcp.db import get_db, handle_db_errors
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
from footprinter.utils.exceptions import DatabaseNotInitializedError
from footprinter.utils.sqlite_errors import is_schema_busy_error, is_transient_schema_error


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
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            with pytest.raises(DatabaseNotInitializedError):
                with get_db() as _conn:
                    pass

    def test_get_db_closes_connection_on_init_failure(self):
        """Connection is closed even when _check_db_initialized raises."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        with (
            patch("footprinter.db_base.sqlite3.connect", return_value=mock_conn),
            patch("footprinter.db_base.get_db_path", return_value="/fake/path.db"),
            patch("footprinter.db_base._check_db_initialized", side_effect=DatabaseNotInitializedError),
        ):
            with pytest.raises(DatabaseNotInitializedError):
                with get_db() as _conn:
                    pass
        mock_conn.close.assert_called_once()

    def test_get_db_succeeds_with_initialized_db(self, tool_db, tmp_path):
        """Initialized database (has files table) does not raise."""
        db_path = tmp_path / "test.db"
        with patch("footprinter.db_base.get_db_path", return_value=db_path):
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
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            result = footprinter_status()
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_navigation_tools_error_on_empty_db(self, empty_db):
        """Navigation tools return structured error on empty DB."""
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            assert footprinter_project("test")["error_code"] == "DB_NOT_INITIALIZED"
            assert footprinter_client("test")["error_code"] == "DB_NOT_INITIALIZED"
            assert footprinter_folder("~/test")["error_code"] == "DB_NOT_INITIALIZED"

    def test_search_tool_error_on_empty_db(self, empty_db):
        """footprinter_search returns structured error on empty DB."""
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            result = footprinter_search("test")
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_read_tool_error_on_empty_db(self, empty_db):
        """footprinter_read returns structured error on empty DB."""
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            result = footprinter_read("file", 1)
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_semantic_files_error_on_empty_db(self, empty_db):
        """footprinter_semantic(source='files') returns DB_NOT_INITIALIZED on empty DB."""
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            result = footprinter_semantic("test query", source="files")
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_semantic_all_error_on_empty_db(self, empty_db):
        """footprinter_semantic(source='all') returns DB_NOT_INITIALIZED on empty DB."""
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            result = footprinter_semantic("test query", source="all")
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_semantic_chats_error_on_empty_db(self, empty_db):
        """footprinter_semantic(source='chats') returns DB_NOT_INITIALIZED on empty DB."""
        with patch("footprinter.db_base.get_db_path", return_value=empty_db):
            result = footprinter_semantic("test query", source="chats")
        assert result["error_code"] == "DB_NOT_INITIALIZED"


class TestSharedExceptionImport:
    """Tests that DatabaseNotInitializedError lives in the shared module."""

    def test_exception_importable_from_utils(self):
        """DatabaseNotInitializedError can be imported from utils.exceptions."""
        from footprinter.utils.exceptions import DatabaseNotInitializedError as SharedError

        assert SharedError is DatabaseNotInitializedError


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


class TestTransientErrorClassifier:
    """Tests for is_transient_schema_error — broad classifier used in MCP decorator."""

    def test_schema_changed_is_transient(self):
        exc = sqlite3.OperationalError("database schema has changed")
        assert is_transient_schema_error(exc) is True

    def test_locked_is_transient(self):
        exc = sqlite3.OperationalError("database is locked")
        assert is_transient_schema_error(exc) is True

    def test_no_such_column_is_transient(self):
        exc = sqlite3.OperationalError("no such column: visibility")
        assert is_transient_schema_error(exc) is True

    def test_no_such_table_is_transient(self):
        exc = sqlite3.OperationalError("no such table: files")
        assert is_transient_schema_error(exc) is True

    def test_syntax_error_is_not_transient(self):
        exc = sqlite3.OperationalError('near "SELECT": syntax error')
        assert is_transient_schema_error(exc) is False

    def test_migration_window_vtable_is_transient(self):
        """Vtable constructor failure without a missing-module cause is transient.

        This is the migration-window case: FTS5 is present but the vtable's
        constructor transiently fails between the migration commit and FTS-trigger
        re-creation. It recovers on retry with a fresh connection.
        """
        exc = sqlite3.OperationalError("vtable constructor failed: files_fts")
        assert is_transient_schema_error(exc) is True

    def test_permanent_vtable_missing_module_is_not_transient(self):
        """Vtable constructor failure caused by a missing FTS5 module is permanent.

        SQLite emits 'no such module: fts5' as the cause when the extension is
        unavailable; that case must surface rather than be retried and swallowed.
        """
        exc = sqlite3.OperationalError(
            "vtable constructor failed: files_fts: no such module: fts5"
        )
        assert is_transient_schema_error(exc) is False

    def test_no_such_module_alone_is_not_transient(self):
        """A bare missing-module error (no vtable phrase) is never transient."""
        exc = sqlite3.OperationalError("no such module: fts5")
        assert is_transient_schema_error(exc) is False


class TestSchemaBusyClassifier:
    """Tests for is_schema_busy_error — narrow classifier used in status helpers."""

    def test_schema_changed_is_busy(self):
        exc = sqlite3.OperationalError("database schema has changed")
        assert is_schema_busy_error(exc) is True

    def test_locked_is_busy(self):
        exc = sqlite3.OperationalError("database is locked")
        assert is_schema_busy_error(exc) is True

    def test_no_such_column_is_not_busy(self):
        exc = sqlite3.OperationalError("no such column: visibility")
        assert is_schema_busy_error(exc) is False

    def test_no_such_table_is_not_busy(self):
        exc = sqlite3.OperationalError("no such table: files")
        assert is_schema_busy_error(exc) is False


class TestHandleDbErrorsRetry:
    """Tests that handle_db_errors retries transient schema errors."""

    def test_transient_error_retries_and_succeeds(self):
        """Transient OperationalError on first call, success on second."""
        call_count = 0

        @handle_db_errors
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise sqlite3.OperationalError("database schema has changed")
            return {"results": []}

        result = flaky()
        assert "error_code" not in result
        assert result == {"results": []}
        assert call_count == 2

    def test_transient_error_exhausts_retries(self):
        """All retries fail → DATABASE_ERROR response."""

        @handle_db_errors
        def always_fails():
            raise sqlite3.OperationalError("database schema has changed")

        result = always_fails()
        assert result["error_code"] == "DATABASE_ERROR"

    def test_non_transient_error_no_retry(self):
        """Non-transient OperationalError returns DATABASE_ERROR immediately."""
        call_count = 0

        @handle_db_errors
        def syntax_error():
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("near \"SELECT\": syntax error")

        result = syntax_error()
        assert result["error_code"] == "DATABASE_ERROR"
        assert call_count == 1

    def test_database_not_initialized_still_caught(self):
        """Existing behavior: DatabaseNotInitializedError → DB_NOT_INITIALIZED."""

        @handle_db_errors
        def uninit():
            raise DatabaseNotInitializedError()

        result = uninit()
        assert result["error_code"] == "DB_NOT_INITIALIZED"

    def test_retry_count_matches_constant(self):
        """Function called exactly _MAX_RETRIES + 1 times on persistent transient error."""
        from footprinter.mcp.db import _MAX_RETRIES

        call_count = 0

        @handle_db_errors
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("database is locked")

        always_fails()
        assert call_count == _MAX_RETRIES + 1

    def test_database_error_response_structure(self):
        """Exhausted retry response has correct structure and hides raw SQL."""

        @handle_db_errors
        def always_fails():
            raise sqlite3.OperationalError("no such column: visibility")

        result = always_fails()
        assert result["error_code"] == "DATABASE_ERROR"
        assert result["error"] == "Unreachable"
        assert "hint" in result
        assert "visibility" not in result["error"]
