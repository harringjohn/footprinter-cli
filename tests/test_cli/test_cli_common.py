"""Tests for footprinter.cli._common shared utilities."""

import sqlite3
from io import StringIO

import pytest
from rich.console import Console

from footprinter.cli._common import format_size, open_database, open_db
from footprinter.ingest.database import Database


class TestFormatSize:
    """Tests for the shared format_size() helper."""

    def test_zero_bytes(self):
        assert format_size(0) == "0 B"

    def test_bytes_below_kb(self):
        assert format_size(512) == "512 B"

    def test_one_byte(self):
        assert format_size(1) == "1 B"

    def test_exactly_1kb(self):
        assert format_size(1024) == "1.0 KB"

    def test_kilobytes(self):
        assert format_size(1536) == "1.5 KB"

    def test_exactly_1mb(self):
        assert format_size(1024 * 1024) == "1.0 MB"

    def test_megabytes(self):
        assert format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_exactly_1gb(self):
        assert format_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_gigabytes(self):
        assert format_size(2 * 1024 * 1024 * 1024) == "2.0 GB"

    def test_boundary_just_under_kb(self):
        assert format_size(1023) == "1023 B"

    def test_boundary_just_under_mb(self):
        result = format_size(1024 * 1024 - 1)
        assert "KB" in result

    def test_boundary_just_under_gb(self):
        result = format_size(1024 * 1024 * 1024 - 1)
        assert "MB" in result


class TestColorConstants:
    """Tests for shared color vocabulary constants."""

    def test_color_constants_importable(self):
        from footprinter.cli._common import C_DIM, C_ERROR, C_INFO, C_SUCCESS, C_WARNING

        for name, val in [
            ("C_SUCCESS", C_SUCCESS),
            ("C_WARNING", C_WARNING),
            ("C_ERROR", C_ERROR),
            ("C_INFO", C_INFO),
            ("C_DIM", C_DIM),
        ]:
            assert isinstance(val, str), f"{name} should be a string"
            assert len(val) > 0, f"{name} should be non-empty"

    def test_color_constants_are_distinct(self):
        from footprinter.cli._common import C_ERROR, C_INFO, C_SUCCESS, C_WARNING

        values = {C_SUCCESS, C_WARNING, C_ERROR, C_INFO}
        assert len(values) == 4, "Primary colors should be distinct"


class TestOpenDb:
    """Tests for the open_db() context manager."""

    def test_missing_db_exits(self, tmp_path):
        """Nonexistent db_path triggers sys.exit(1)."""
        with pytest.raises(SystemExit) as exc_info:
            with open_db(db_path=tmp_path / "nonexistent.db"):
                pass
        assert exc_info.value.code == 1

    def test_missing_db_message_contains_guidance(self, tmp_path, monkeypatch):
        """Error message includes guidance to run fp setup and fp ingest."""
        import footprinter.cli._common as mod

        buf = StringIO()
        monkeypatch.setattr(mod, "console", Console(file=buf, force_terminal=False))
        with pytest.raises(SystemExit):
            with open_db(db_path=tmp_path / "nonexistent.db"):
                pass
        output = buf.getvalue()
        assert "fp setup" in output
        assert "fp ingest" in output


class TestOpenDatabase:
    """Tests for the open_database() context manager."""

    def test_missing_db_exits(self, tmp_path):
        """Nonexistent db_path triggers sys.exit(1)."""
        with pytest.raises(SystemExit) as exc_info:
            with open_database(db_path=tmp_path / "nonexistent.db"):
                pass
        assert exc_info.value.code == 1

    def test_yields_database_instance(self, tmp_path):
        """Context manager yields a Database with an active connection."""
        db_path = tmp_path / "test.db"
        Database(str(db_path)).close()

        with open_database(db_path=db_path) as db:
            assert isinstance(db, Database)
            assert db.conn is not None

    def test_closes_on_normal_exit(self, tmp_path):
        """Connection is closed after the with block exits normally."""
        db_path = tmp_path / "test.db"
        Database(str(db_path)).close()

        with open_database(db_path=db_path) as db:
            conn = db.conn

        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_closes_on_exception(self, tmp_path):
        """Connection is closed even when an exception occurs inside the block."""
        db_path = tmp_path / "test.db"
        Database(str(db_path)).close()

        with pytest.raises(RuntimeError):
            with open_database(db_path=db_path) as db:
                conn = db.conn
                raise RuntimeError("boom")

        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_missing_db_message_contains_guidance(self, tmp_path, monkeypatch):
        """Error message includes guidance to run fp setup and fp ingest."""
        import footprinter.cli._common as mod

        buf = StringIO()
        monkeypatch.setattr(mod, "console", Console(file=buf, force_terminal=False))
        with pytest.raises(SystemExit):
            with open_database(db_path=tmp_path / "nonexistent.db"):
                pass
        output = buf.getvalue()
        assert "fp setup" in output
        assert "fp ingest" in output
