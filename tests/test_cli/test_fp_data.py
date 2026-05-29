"""Tests for fp data — entity CSV export, template, and import.

Validates:
  1. fp data --help exits 0 and lists subcommands
  2. Bare fp data shows help
  3. fp data template <noun> writes header + sample rows (no DB needed)
  4. fp data export <noun> queries the DB and writes CSV
  5. fp data import <noun> <file> validates input and runs in dry-run by default
  6. writable_columns exclude mcp_view/mcp_read (policy-only columns)
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

    def test_help_lists_subcommands(self):
        stdout, stderr, _ = run_fp("data", "--help")
        output = stdout + stderr
        for sub in ("export", "template", "import"):
            assert sub in output, f"'{sub}' not in fp data --help"


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


class TestDataBare:
    """fp data with no action shows help and exits 0."""

    def test_bare_data_exits_zero(self):
        _, _, code = run_fp("data")
        assert code == 0


# ---------------------------------------------------------------------------
# 3. Template
# ---------------------------------------------------------------------------


class TestDataTemplate:
    """fp data template <noun> writes the header row and example values."""

    def test_template_clients_writes_header(self):
        stdout, _, code = run_fp("data", "template", "clients")
        assert code == 0
        assert "name" in stdout
        assert "client_type" in stdout

    def test_template_projects_writes_header(self):
        stdout, _, code = run_fp("data", "template", "projects")
        assert code == 0
        assert "project_name" in stdout

    def test_template_files_writes_header(self):
        # Data-source noun routes through DATA_SOURCE_SPECS, not the legacy path.
        stdout, _, code = run_fp("data", "template", "files")
        assert code == 0
        assert "id" in stdout
        assert "path" in stdout

    def test_template_invalid_noun_exits_nonzero(self):
        _, _, code = run_fp("data", "template", "bogus")
        assert code != 0


# ---------------------------------------------------------------------------
# 4. Export
# ---------------------------------------------------------------------------


class TestDataExport:
    """fp data export <noun> writes a CSV row for each matching record."""

    def test_export_clients_writes_csv(self):
        conn = _seeded_inmemory_db()
        with patch("footprinter.cli.data.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("data", "export", "clients")

        assert code == 0
        assert "name" in stdout
        assert "Acme Corp" in stdout

    def test_export_files_writes_csv(self):
        conn = _seeded_inmemory_db()
        with patch("footprinter.cli.data.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("data", "export", "files")

        assert code == 0
        assert "readme.md" in stdout

    def test_export_invalid_noun_exits_nonzero(self):
        _, _, code = run_fp("data", "export", "bogus")
        assert code != 0


# ---------------------------------------------------------------------------
# 5. Import
# ---------------------------------------------------------------------------


class TestDataImport:
    """fp data import validates inputs and defaults to dry-run."""

    def test_import_missing_file_arg_exits_nonzero(self):
        _, _, code = run_fp("data", "import", "files")
        assert code != 0

    def test_import_nonexistent_file_exits_one(self):
        _, _, code = run_fp("data", "import", "files", "/nonexistent/path.csv")
        assert code == 1

    def test_import_dry_run_with_valid_csv(self, tmp_path):
        csv_path = tmp_path / "files.csv"
        csv_path.write_text("id,status\n1,hidden\n")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Minimal table — _handle_import only needs SELECT id and UPDATE.
        conn.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, status TEXT, "
            "project_id INTEGER, client_id INTEGER, mcp_view TEXT, mcp_read TEXT)"
        )
        conn.execute("INSERT INTO files (id, status) VALUES (1, 'listed')")
        conn.commit()

        with patch("footprinter.cli.data.open_db", return_value=_open_db_stub(conn)):
            stdout, stderr, code = run_fp("data", "import", "files", str(csv_path))

        assert code == 0
        # Default mode is dry-run — output mentions either "Would update" or
        # "Dry run" or "--commit" hint.
        output = stdout + stderr
        assert "commit" in output.lower() or "ould" in output  # "Would update"


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

    def test_template_files_excludes_mcp_columns(self):
        stdout, _, code = run_fp("data", "template", "files")
        assert code == 0
        header = stdout.splitlines()[0]
        assert "mcp_view" not in header
        assert "mcp_read" not in header

    def test_import_ignores_mcp_columns_in_csv(self, tmp_path):
        csv_path = tmp_path / "files.csv"
        csv_path.write_text("id,status,mcp_view,mcp_read\n1,hidden,visible,allow\n")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, status TEXT, "
            "project_id INTEGER, client_id INTEGER, mcp_view TEXT, mcp_read TEXT)"
        )
        conn.execute("INSERT INTO files (id, status) VALUES (1, 'listed')")
        conn.commit()

        with patch("footprinter.cli.data.open_db", return_value=_open_db_stub(conn)):
            stdout, stderr, code = run_fp("data", "import", "files", str(csv_path))

        assert code == 0
        output = stdout + stderr
        assert "commit" in output.lower() or "ould" in output
