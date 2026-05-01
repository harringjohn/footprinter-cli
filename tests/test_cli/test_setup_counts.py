"""Tests for setup.py _get_indexing_counts exception handling."""

import sqlite3
from unittest.mock import MagicMock, patch

from footprinter.cli.setup import _get_indexing_counts


class TestGetIndexingCounts:
    def test_returns_zero_for_missing_table(self):
        """In-memory DB with no tables returns 0 for all counts."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        with patch("footprinter.cli.setup._get_db_connection", return_value=conn):
            result = _get_indexing_counts()

        assert isinstance(result, dict)
        assert all(v == 0 for v in result.values())

    def test_outer_catches_non_sqlite_error(self):
        """Non-sqlite error in cursor.execute should be caught (wizard must not crash)."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = RuntimeError("unexpected")
        mock_cursor.fetchone.return_value = (0,)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("footprinter.cli.setup._get_db_connection", return_value=mock_conn):
            result = _get_indexing_counts()

        assert result == {}

    def test_outer_returns_empty_on_db_error(self):
        """sqlite3.DatabaseError during cursor ops should return {}."""
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = sqlite3.DatabaseError("corrupt db")

        with patch("footprinter.cli.setup._get_db_connection", return_value=mock_conn):
            result = _get_indexing_counts()

        assert result == {}
