"""Tests for fp data — entity CSV import.

Validates:
  1. fp data --help exits 0 and lists import subcommand
  2. Bare fp data shows help
  3. fp data import <noun> <file> executes directly
  4. writable_columns exclude mcp_view/mcp_read (policy-only columns)

Export and template functionality moved to fp view format flags (FPR-1863).
"""

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

from conftest import run_fp
from footprinter.cli.data import DATA_SOURCE_SPECS


def _seeded_inmemory_db():
    """Build an in-memory SQLite connection with the tool-scope schema seeded."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn
    assert conn is not None  # Database() always opens a connection
    conn.execute(
        "INSERT INTO clients (name, slug, client_type, status) VALUES (?, ?, ?, ?)",
        ("Acme Corp", "acme", "external", "listed"),
    )
    conn.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES (?, ?, 'local', 'listed', 'text', 100)",
        ("readme.md", "/tmp/readme.md"),
    )
    conn.commit()
    return conn


@contextmanager
def _open_db_stub(conn):
    """Mimic open_db()'s context-manager contract over a pre-built connection."""
    try:
        yield conn
    finally:
        pass


# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestDataHelp:
    """fp data --help exits 0 and lists subcommands."""

    def test_help_exits_zero(self):
        _, _, code = run_fp("data", "--help")
        assert code == 0

    def test_help_lists_import(self):
        stdout, stderr, _ = run_fp("data", "--help")
        output = stdout + stderr
        assert "import" in output, "'import' not in fp data --help"


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


class TestDataBare:
    """fp data with no action shows help and exits 0."""

    def test_bare_data_exits_zero(self):
        _, _, code = run_fp("data")
        assert code == 0


# ---------------------------------------------------------------------------
# 3. Import
# ---------------------------------------------------------------------------


class TestDataImport:
    """fp data import validates inputs and executes directly."""

    def test_import_missing_file_arg_exits_nonzero(self):
        _, _, code = run_fp("data", "import", "files")
        assert code != 0

    def test_import_nonexistent_file_exits_one(self):
        _, _, code = run_fp("data", "import", "files", "/nonexistent/path.csv")
        assert code == 1

    def test_import_executes_with_valid_csv(self, tmp_path):
        csv_path = tmp_path / "files.csv"
        csv_path.write_text("id,status\n1,unlisted\n")

        conn = _seeded_inmemory_db()

        with patch("footprinter.cli.data.open_db", return_value=_open_db_stub(conn)):
            stdout, stderr, code = run_fp("data", "import", "files", str(csv_path))

        assert code == 0
        row = conn.execute("SELECT status FROM files WHERE id = 1").fetchone()
        assert row["status"] == "unlisted"


# ---------------------------------------------------------------------------
# 6. Writable columns exclude policy-only MCP fields
# ---------------------------------------------------------------------------


class TestWritableColumnsExcludeMcp:
    """mcp_view and mcp_read are policy-system output — not writable via import."""

    def test_writable_columns_exclude_mcp_fields(self):
        for noun, spec in DATA_SOURCE_SPECS.items():
            for col in ("mcp_view", "mcp_read"):
                assert col not in spec.writable_columns, (
                    f"{noun}.writable_columns still contains {col!r}"
                )

    def test_messages_zero_writable_columns(self):
        assert DATA_SOURCE_SPECS["messages"].writable_columns == []

    def test_import_ignores_mcp_columns_in_csv(self, tmp_path):
        csv_path = tmp_path / "files.csv"
        csv_path.write_text("id,status,mcp_view,mcp_read\n1,unlisted,visible,allow\n")

        conn = _seeded_inmemory_db()

        with patch("footprinter.cli.data.open_db", return_value=_open_db_stub(conn)):
            stdout, stderr, code = run_fp("data", "import", "files", str(csv_path))

        assert code == 0
        row = conn.execute("SELECT status, mcp_view FROM files WHERE id = 1").fetchone()
        assert row["status"] == "unlisted"
        assert row["mcp_view"] != "visible", "mcp_view should be ignored from CSV"
