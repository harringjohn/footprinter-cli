"""Integration tests: MCP tools survive transient schema migration errors.

Simulates concurrent migration by patching open_checked_connection to raise
transient OperationalError on the first call, then succeed on retries.
"""

import sqlite3
from unittest.mock import patch

import pytest

from footprinter.mcp.tools.search import footprinter_search
from footprinter.mcp.tools.status import footprinter_status


class TestSchemaMigrationResilience:

    def _patch_transient_then_succeed(self, fail_count=1):
        """Return a side_effect that raises transient errors then delegates to the real impl."""
        from footprinter.db_base import open_checked_connection as real_open

        call_count = 0

        def side_effect(*, read_only=False):
            nonlocal call_count
            call_count += 1
            if call_count <= fail_count:
                raise sqlite3.OperationalError("database schema has changed")
            return real_open(read_only=read_only)

        return side_effect

    def test_search_survives_transient_schema_error(self, tool_db, tmp_path):
        db_path = tmp_path / "test.db"
        se = self._patch_transient_then_succeed(fail_count=1)
        with patch("footprinter.db_base.get_db_path", return_value=db_path), \
             patch("footprinter.mcp.db.open_checked_connection", side_effect=se):
            result = footprinter_search("test")
        assert "error_code" not in result or result.get("error_code") != "DATABASE_ERROR"

    def test_status_survives_transient_schema_error(self, tool_db, tmp_path):
        db_path = tmp_path / "test.db"
        se = self._patch_transient_then_succeed(fail_count=1)
        with patch("footprinter.db_base.get_db_path", return_value=db_path), \
             patch("footprinter.mcp.db.open_checked_connection", side_effect=se):
            result = footprinter_status()
        assert "error_code" not in result or result.get("error_code") != "DATABASE_ERROR"

    def test_all_retries_exhausted_returns_database_error(self):
        def always_fail(*, read_only=False):
            raise sqlite3.OperationalError("database schema has changed")

        with patch("footprinter.mcp.db.open_checked_connection", side_effect=always_fail):
            result = footprinter_search("test")
        assert result["error_code"] == "DATABASE_ERROR"

    def test_error_response_hides_sql_details(self):
        def always_fail(*, read_only=False):
            raise sqlite3.OperationalError("no such column: visibility")

        with patch("footprinter.mcp.db.open_checked_connection", side_effect=always_fail):
            result = footprinter_search("test")
        assert result["error_code"] == "DATABASE_ERROR"
        assert result["error"] == "Unreachable"
        assert "hint" in result
        assert "visibility" not in result["error"]
