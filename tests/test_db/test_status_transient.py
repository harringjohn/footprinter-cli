"""Tests that status helpers re-raise schema-busy errors instead of swallowing them."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from footprinter.db.status import _safe_count, _safe_fetchall, _safe_query, get_mcp_status


class TestSafeCountTransient:

    def test_reraises_schema_busy(self):
        cursor = MagicMock()
        cursor.execute.side_effect = sqlite3.OperationalError("database schema has changed")
        with pytest.raises(sqlite3.OperationalError, match="database schema has changed"):
            _safe_count(cursor, "SELECT COUNT(*) FROM files")

    def test_swallows_missing_table(self):
        cursor = MagicMock()
        cursor.execute.side_effect = sqlite3.OperationalError("no such table: files")
        assert _safe_count(cursor, "SELECT COUNT(*) FROM files") == 0


class TestSafeQueryTransient:

    def test_reraises_schema_busy(self):
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            _safe_query(conn, "SELECT MAX(indexed_at) FROM files")

    def test_swallows_missing_table(self):
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("no such table: files")
        assert _safe_query(conn, "SELECT MAX(indexed_at) FROM files") is None


class TestSafeFetchallTransient:

    def test_reraises_schema_busy(self):
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("database schema has changed")
        with pytest.raises(sqlite3.OperationalError, match="database schema has changed"):
            _safe_fetchall(conn, "SELECT source, COUNT(*) FROM files GROUP BY source")

    def test_swallows_missing_table(self):
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("no such table: files")
        assert _safe_fetchall(conn, "SELECT source, COUNT(*) FROM files GROUP BY source") == []


class TestMcpStatusDoubleCheck:

    def test_reraises_schema_busy_in_double_check(self):
        """When the double-check conn.execute hits a schema-busy error, it propagates.

        Patches _safe_query to return 0/None (triggering the double-check branch)
        while conn.execute raises the schema-busy error on the verification query.
        """
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("database schema has changed")
        with patch("footprinter.db.status._safe_query", side_effect=[0, None]), \
             pytest.raises(sqlite3.OperationalError, match="database schema has changed"):
            get_mcp_status(conn)

    def test_schema_busy_propagates_through_safe_query(self):
        """Schema-busy errors in _safe_query itself also propagate."""
        conn = MagicMock()
        conn.execute.side_effect = sqlite3.OperationalError("database schema has changed")
        with pytest.raises(sqlite3.OperationalError, match="database schema has changed"):
            get_mcp_status(conn)
