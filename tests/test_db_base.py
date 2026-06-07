"""Tests for footprinter.db_base — shared connection setup for MCP and API."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def empty_db(tmp_path):
    """Create an empty SQLite file with no tables."""
    db_path = tmp_path / "empty.db"
    sqlite3.connect(str(db_path)).close()
    return db_path


@pytest.fixture
def initialized_db(tmp_path):
    """Create a SQLite file with a files table (minimal schema)."""
    db_path = tmp_path / "init.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return db_path


class TestGetConnection:
    def test_sets_pragmas(self, tmp_path):
        """Connection has busy_timeout, foreign_keys, row_factory, and no query_only."""
        from footprinter.db_base import get_connection

        db_path = tmp_path / "test.db"
        conn = get_connection(str(db_path))
        try:
            assert conn.row_factory is sqlite3.Row
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 0
        finally:
            conn.close()

    def test_read_only_sets_query_only(self, tmp_path):
        """read_only=True enables PRAGMA query_only."""
        from footprinter.db_base import get_connection

        db_path = tmp_path / "test.db"
        conn = get_connection(str(db_path), read_only=True)
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            conn.close()

    def test_accepts_path_object(self, tmp_path):
        """Accepts pathlib.Path as well as str."""
        from footprinter.db_base import get_connection

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()


class TestOpenCheckedConnection:
    def test_sets_pragmas(self, initialized_db):
        """Connection has busy_timeout, foreign_keys, and row_factory set."""
        from footprinter.db_base import open_checked_connection

        with (
            patch("footprinter.db_base.get_db_path", return_value=initialized_db),
            patch("footprinter.db_base.load_globals"),
        ):
            with open_checked_connection() as conn:
                assert conn.row_factory is sqlite3.Row
                bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                assert bt == 5000
                fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                assert fk == 1

    def test_read_only_sets_query_only(self, initialized_db):
        """read_only=True enables PRAGMA query_only."""
        from footprinter.db_base import open_checked_connection

        with (
            patch("footprinter.db_base.get_db_path", return_value=initialized_db),
            patch("footprinter.db_base.load_globals"),
        ):
            with open_checked_connection(read_only=True) as conn:
                qo = conn.execute("PRAGMA query_only").fetchone()[0]
                assert qo == 1

    def test_read_write_no_query_only(self, initialized_db):
        """Default (read_only=False) does not set query_only."""
        from footprinter.db_base import open_checked_connection

        with (
            patch("footprinter.db_base.get_db_path", return_value=initialized_db),
            patch("footprinter.db_base.load_globals"),
        ):
            with open_checked_connection() as conn:
                qo = conn.execute("PRAGMA query_only").fetchone()[0]
                assert qo == 0

    def test_raises_on_uninit_db(self, empty_db):
        """Empty DB (no files table) raises DatabaseNotInitializedError."""
        from footprinter.db_base import open_checked_connection
        from footprinter.utils.exceptions import DatabaseNotInitializedError

        with (
            patch("footprinter.db_base.get_db_path", return_value=empty_db),
            patch("footprinter.db_base.load_globals"),
        ):
            with pytest.raises(DatabaseNotInitializedError):
                with open_checked_connection() as _conn:
                    pass

    def test_closes_connection_on_error(self, empty_db):
        """Connection is closed even when initialization check fails."""
        from footprinter.db_base import open_checked_connection

        with (
            patch("footprinter.db_base.get_db_path", return_value=empty_db),
            patch("footprinter.db_base.load_globals"),
        ):
            try:
                with open_checked_connection() as _conn:
                    pass
            except Exception:
                pass
        # Verify the DB file isn't locked (connection was closed)
        conn = sqlite3.connect(str(empty_db))
        conn.close()
