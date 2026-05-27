"""Tests for MCP error helper and information oracle prevention.

Tests that:
1. mcp_error() produces consistent response structure
2. Internal details are logged but not exposed
3. Error messages don't leak sensitive information (oracle tests)
"""

import logging
import sqlite3
from unittest.mock import patch

import pytest

from footprinter.mcp.errors import ERROR_MESSAGES, mcp_error


class TestMcpErrorHelper:
    """Unit tests for the mcp_error() function."""

    def test_basic_error_structure(self):
        """Error response has required keys."""
        result = mcp_error("NOT_FOUND")
        assert "error" in result
        assert "error_code" in result
        assert result["error_code"] == "NOT_FOUND"

    def test_uses_error_messages_dict(self):
        """Error message comes from ERROR_MESSAGES."""
        result = mcp_error("NOT_FOUND")
        assert result["error"] == ERROR_MESSAGES["NOT_FOUND"]
        assert result["error"] == "Nothing here"

    def test_all_error_codes_have_messages(self):
        """Every error code in ERROR_MESSAGES works."""
        for code in ERROR_MESSAGES:
            result = mcp_error(code)
            assert result["error_code"] == code
            assert result["error"] == ERROR_MESSAGES[code]

    def test_detail_override(self):
        """detail parameter overrides default message."""
        result = mcp_error("NOT_FOUND", detail="Custom message")
        assert result["error"] == "Custom message"
        assert result["error_code"] == "NOT_FOUND"

    def test_metadata_included(self):
        """metadata parameter is included in response."""
        meta = {"id": 123, "name": "test"}
        result = mcp_error("NOT_FOUND", metadata=meta)
        assert result["metadata"] == meta

    def test_no_metadata_key_when_none(self):
        """metadata key is absent when not provided."""
        result = mcp_error("NOT_FOUND")
        assert "metadata" not in result

    def test_internal_message_not_exposed(self):
        """internal_message is logged but not in response."""
        result = mcp_error("NOT_FOUND", internal_message="secret info")
        assert "secret info" not in str(result)
        assert "internal_message" not in result

    def test_internal_message_logged(self, caplog):
        """internal_message is written to logs."""
        with caplog.at_level(logging.WARNING):
            mcp_error("NOT_FOUND", internal_message="file:123 missing")
        assert "file:123 missing" in caplog.text
        assert "[NOT_FOUND]" in caplog.text

    def test_log_level_parameter(self, caplog):
        """level parameter controls log level."""
        with caplog.at_level(logging.DEBUG):
            mcp_error("NOT_FOUND", internal_message="debug msg", level="debug")
        assert "debug msg" in caplog.text

    def test_unknown_code_fallback(self):
        """Unknown error code returns 'Error' as message."""
        result = mcp_error("UNKNOWN_CODE")
        assert result["error"] == "Error"
        assert result["error_code"] == "UNKNOWN_CODE"

    def test_hint_included_in_response(self):
        """Known error codes include a hint field."""
        result = mcp_error("NOT_FOUND")
        assert "hint" in result
        assert isinstance(result["hint"], str)
        assert len(result["hint"]) > 0

    def test_all_error_codes_have_hints(self):
        """Every error code in ERROR_MESSAGES gets a hint."""
        for code in ERROR_MESSAGES:
            result = mcp_error(code)
            assert "hint" in result, f"{code} missing hint"
            assert isinstance(result["hint"], str)
            assert len(result["hint"]) > 0, f"{code} has empty hint"

    def test_hint_override(self):
        """hint parameter overrides the default hint."""
        result = mcp_error("NOT_FOUND", hint="Try a different ID")
        assert result["hint"] == "Try a different ID"

    def test_unknown_code_no_hint(self):
        """Unknown error codes don't get a hint."""
        result = mcp_error("UNKNOWN_CODE")
        assert "hint" not in result


class TestInformationOracles:
    """Tests that error messages don't leak internal information."""

    @pytest.fixture
    def test_db(self):
        """Create an in-memory test database."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Create minimal tables
        cursor.execute("""
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                source TEXT,
                external_id TEXT,
                account TEXT,
                name TEXT,
                path TEXT,
                content_type TEXT,
                mime_type TEXT,
                size_bytes INTEGER,
                created_at TEXT,
                modified_at DATETIME,
                indexed_at TEXT,
                project_id INTEGER,
                md5_hash TEXT,
                status TEXT DEFAULT 'listed',
                status_reason TEXT,
                mcp_view TEXT DEFAULT 'inherit',
                mcp_read TEXT DEFAULT 'inherit',
                mcp_view_source TEXT,
                mcp_read_source TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE emails (
                id INTEGER PRIMARY KEY,
                message_id TEXT,
                account TEXT,
                subject TEXT,
                from_address TEXT,
                from_name TEXT,
                to_addresses TEXT,
                received_at DATETIME,
                body_preview TEXT,
                mcp_view TEXT DEFAULT 'inherit',
                mcp_read TEXT DEFAULT 'inherit',
                mcp_view_source TEXT,
                mcp_read_source TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE chats (
                id INTEGER PRIMARY KEY,
                external_id TEXT,
                account TEXT,
                title TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                message_count INTEGER,
                mcp_view TEXT DEFAULT 'inherit',
                mcp_read TEXT DEFAULT 'inherit',
                mcp_view_source TEXT,
                mcp_read_source TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                role TEXT,
                content TEXT,
                created_at DATETIME
            )
        """)
        cursor.execute("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                project_name TEXT,
                project_type TEXT,
                root_path TEXT,
                markers TEXT,
                status TEXT,
                detected_at DATETIME,
                client TEXT,
                description TEXT,
                github_url TEXT,
                mcp_view TEXT DEFAULT 'inherit',
                mcp_read TEXT DEFAULT 'inherit',
                mcp_view_source TEXT,
                mcp_read_source TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY,
                name TEXT,
                slug TEXT,
                client_type TEXT,
                path_pattern TEXT,
                status TEXT,
                created_at DATETIME,
                mcp_view TEXT DEFAULT 'inherit',
                mcp_read TEXT DEFAULT 'inherit',
                mcp_view_source TEXT,
                mcp_read_source TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE folders (
                id INTEGER PRIMARY KEY,
                path TEXT,
                relative_path TEXT,
                name TEXT,
                source TEXT,
                direct_file_count INTEGER,
                total_size_bytes INTEGER,
                scanned_at DATETIME,
                project_id INTEGER,
                external_id TEXT,
                account TEXT,
                mcp_view TEXT DEFAULT 'inherit',
                mcp_read TEXT,
                mcp_view_source TEXT,
                mcp_read_source TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE visibility_policies (
                scope TEXT PRIMARY KEY,
                setting TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE permission_policies (
                scope TEXT PRIMARY KEY,
                setting TEXT
            )
        """)

        conn.commit()
        yield conn
        conn.close()

    def test_read_entity_type_not_leaked(self, test_db):
        """footprinter_read with invalid type doesn't leak the type value."""
        # Insert a valid file so _get_metadata returns something
        test_db.execute("""
            INSERT INTO files (id, source, name, path)
            VALUES (1, 'local', 'test.txt', '/tmp/test.txt')
        """)
        # Make it visible by default
        test_db.execute("""
            INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')
        """)
        test_db.execute("""
            INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')
        """)
        test_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_db:
            mock_db.return_value.__enter__ = lambda s: test_db
            mock_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            # Use a valid-looking type that will pass metadata check but fail on type switch
            result = footprinter_read("invalid_type", 1)

        # Valid types are documented in the tool's docstring, so INVALID_TYPE
        # is the correct response — it tells the caller they're using the API
        # wrong without leaking anything secret.
        assert result["error_code"] == "INVALID_TYPE"
        assert "invalid_type" not in result["error"]
        assert result["error"] == "Unknown kind"

    def test_read_nonexistent_entity_no_id_leak(self, test_db):
        """footprinter_read with nonexistent ID doesn't leak the ID."""
        with patch("footprinter.mcp.tools.read.get_db") as mock_db:
            mock_db.return_value.__enter__ = lambda s: test_db
            mock_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("file", 999999)

        assert result["error_code"] == "NOT_FOUND"
        assert "999999" not in result["error"]
        assert result["error"] == "Nothing here"

    def test_project_name_not_leaked(self, test_db):
        """footprinter_project with unknown name doesn't leak the search term."""
        with patch("footprinter.mcp.tools.navigation.get_db") as mock_db:
            mock_db.return_value.__enter__ = lambda s: test_db
            mock_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_project

            result = footprinter_project("secret-project-name")

        assert result["error_code"] == "NOT_FOUND"
        assert "secret-project-name" not in result["error"]
        assert result["error"] == "Nothing here"

    def test_client_name_not_leaked(self, test_db):
        """footprinter_client with unknown name doesn't leak the search term."""
        with patch("footprinter.mcp.tools.navigation.get_db") as mock_db:
            mock_db.return_value.__enter__ = lambda s: test_db
            mock_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_client

            result = footprinter_client("secret-client")

        assert result["error_code"] == "NOT_FOUND"
        assert "secret-client" not in result["error"]
        assert result["error"] == "Nothing here"

    def test_folder_path_not_leaked(self, test_db):
        """footprinter_folder with unknown path doesn't leak the path."""
        with patch("footprinter.mcp.tools.navigation.get_db") as mock_db:
            mock_db.return_value.__enter__ = lambda s: test_db
            mock_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.navigation import footprinter_folder

            result = footprinter_folder("/secret/path/to/folder")

        assert result["error_code"] == "NOT_FOUND"
        assert "/secret/path" not in result["error"]
        assert result["error"] == "Nothing here"

    def test_hint_does_not_leak_internals(self):
        """Hints must not contain sensitive internal terms."""
        import re

        from footprinter.mcp.errors import ERROR_HINTS

        for code, hint_text in ERROR_HINTS.items():
            forbidden = ["path", "database", "sql", "file", "folder"]
            words = re.findall(r"\b\w+\b", hint_text.lower())
            for term in forbidden:
                assert term not in words, f"Hint for {code} contains '{term}': {hint_text}"

    def test_semantic_search_error_format(self):
        """SEARCH_FAILED error doesn't leak exception details."""
        # Test the error format directly - the actual semantic search
        # integration is tested elsewhere
        result = mcp_error(
            "SEARCH_FAILED",
            internal_message="ChromaDB connection failed: secret error",
        )

        assert result["error_code"] == "SEARCH_FAILED"
        assert "ChromaDB" not in result["error"]
        assert "secret error" not in result["error"]
        assert result["error"] == "Fruitless"


class TestErrorMessageConsistency:
    """Tests that all MCP tools use consistent error patterns."""

    def test_error_messages_are_vague(self):
        """Error messages should not reveal system internals."""
        import re

        for code, message in ERROR_MESSAGES.items():
            # Messages should be short and generic
            assert len(message) < 30, f"{code} message too long: {message}"
            # Messages should not contain technical terms as standalone words
            # Using word boundaries to avoid false positives like "Forbidden" containing "id"
            forbidden = ["path", "database", "sql", "file", "folder"]
            words = re.findall(r"\b\w+\b", message.lower())
            for term in forbidden:
                assert term not in words, f"{code} contains '{term}': {message}"
