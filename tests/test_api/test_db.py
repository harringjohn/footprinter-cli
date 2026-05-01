"""Tests for footprinter.api.db — database connection dependency."""

import sqlite3
from unittest.mock import patch

import pytest


def _db_path(tool_db) -> str:
    """Extract the file path from a live sqlite3.Connection."""
    return str(tool_db.execute("PRAGMA database_list").fetchone()[2])


class TestGetDb:
    """Test the get_db() context manager."""

    def test_get_conn_yields_connection(self, tool_db):
        """get_conn yields a sqlite3.Connection with row_factory set."""
        from footprinter.api.db import get_db

        with (
            patch("footprinter.api.db.get_db_path", return_value=_db_path(tool_db)),
            patch("footprinter.api.db.load_globals"),
        ):
            with get_db() as conn:
                assert isinstance(conn, sqlite3.Connection)
                assert conn.row_factory == sqlite3.Row

    def test_get_conn_calls_load_globals(self, tool_db):
        """get_db() calls load_globals(conn) for access policy cache."""
        from footprinter.api.db import get_db

        with (
            patch("footprinter.api.db.get_db_path", return_value=_db_path(tool_db)),
            patch("footprinter.api.db.load_globals") as mock_load,
        ):
            with get_db() as conn:
                mock_load.assert_called_once_with(conn)

    def test_get_conn_no_query_only(self, tool_db):
        """Unlike MCP, API db has no PRAGMA query_only (ADMIN may write)."""
        from footprinter.api.db import get_db

        with (
            patch("footprinter.api.db.get_db_path", return_value=_db_path(tool_db)),
            patch("footprinter.api.db.load_globals"),
        ):
            with get_db() as conn:
                row = conn.execute("PRAGMA query_only").fetchone()
                assert row[0] == 0, "API db should NOT be query_only"


class TestDatabaseNotInitialized:
    """Test DatabaseNotInitializedError handling."""

    def test_raises_when_no_tables(self, tmp_path):
        """get_db raises DatabaseNotInitializedError on empty database."""
        from footprinter.api.db import DatabaseNotInitializedError, get_db

        empty_db = tmp_path / "empty.db"
        sqlite3.connect(str(empty_db)).close()

        with patch("footprinter.api.db.get_db_path", return_value=str(empty_db)):
            with pytest.raises(DatabaseNotInitializedError):
                with get_db():
                    pass
