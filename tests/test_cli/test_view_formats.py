"""Tests for fp view --csv, --json, and --template format flags.

Validates:
  1. --csv outputs full export columns (not just display columns)
  2. --template outputs header + example rows without DB
  3. --template prints valid-values notes to stderr
  4. Format flags are mutually exclusive
  5. --csv composes with filter flags and --all
"""

import csv
import io
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from conftest import run_fp


def _seeded_format_db():
    """Build an in-memory DB with clients, projects, and files for format tests."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn
    assert conn is not None

    conn.execute(
        "INSERT INTO clients (id, name, slug, client_type, status) "
        "VALUES (1, 'Acme Corp', 'acme', 'external', 'listed')"
    )
    conn.execute(
        "INSERT INTO projects (id, name, description, status, client_id) "
        "VALUES (1, 'Alpha Project', 'A test project', 'listed', 1)"
    )
    conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, "
        "size_bytes, project_id, client_id) "
        "VALUES (1, 'readme.md', '/tmp/readme.md', 'local', 'listed', "
        "'text', 100, 1, 1)"
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


def _parse_csv_header(stdout: str) -> list[str]:
    """Extract the header row from CSV output."""
    reader = csv.reader(io.StringIO(stdout))
    return next(reader)


# ---------------------------------------------------------------------------
# 1. CSV export columns
# ---------------------------------------------------------------------------


class TestCsvExportColumns:
    """fp view <noun> --csv outputs full export column sets."""

    def test_csv_files_has_export_columns(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--csv", "--all")

        assert code == 0
        header = _parse_csv_header(stdout)
        expected = [
            "id", "name", "path", "source", "status", "content_type",
            "size_bytes", "modified_at", "project_id", "client_id",
            "visibility", "access",
        ]
        assert header == expected

    def test_csv_clients_has_export_columns(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "clients", "--csv")

        assert code == 0
        header = _parse_csv_header(stdout)
        expected = ["name", "client_type", "slug", "status"]
        assert header == expected

    def test_csv_projects_has_export_columns(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "projects", "--csv")

        assert code == 0
        header = _parse_csv_header(stdout)
        expected = ["name", "client", "description", "status"]
        assert header == expected


# ---------------------------------------------------------------------------
# 2. CSV data rows
# ---------------------------------------------------------------------------


class TestCsvDataRows:
    """CSV output includes actual data from the DB."""

    def test_csv_clients_outputs_data_rows(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "clients", "--csv")

        assert code == 0
        assert "Acme Corp" in stdout

    def test_csv_files_outputs_data_rows(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--csv", "--all")

        assert code == 0
        assert "readme.md" in stdout
        assert "/tmp/readme.md" in stdout

    def test_csv_projects_outputs_data_rows(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "projects", "--csv")

        assert code == 0
        assert "Alpha Project" in stdout


# ---------------------------------------------------------------------------
# 3. Template output
# ---------------------------------------------------------------------------


class TestTemplateOutput:
    """fp view <noun> --template outputs headers + example rows."""

    def test_template_files_has_header_and_example(self):
        stdout, _, code = run_fp("view", "files", "--template")
        assert code == 0
        header = _parse_csv_header(stdout)
        assert "id" in header
        assert "path" in header
        assert "readme.md" in stdout

    def test_template_files_prints_valid_values_to_stderr(self):
        _, stderr, code = run_fp("view", "files", "--template")
        assert code == 0
        assert "Valid values" in stderr
        assert "status" in stderr

    def test_template_clients_has_header(self):
        stdout, _, code = run_fp("view", "clients", "--template")
        assert code == 0
        assert "name" in stdout
        assert "client_type" in stdout

    def test_template_files_excludes_non_writable_columns(self):
        stdout, _, code = run_fp("view", "files", "--template")
        assert code == 0
        header = _parse_csv_header(stdout)
        assert "visibility" not in header
        assert "access" not in header

    @pytest.mark.parametrize("noun", ["files", "folders", "emails", "chats", "visits"])
    def test_template_excludes_non_writable_for_all_entity_types(self, noun):
        stdout, _, code = run_fp("view", noun, "--template")
        assert code == 0
        header = _parse_csv_header(stdout)
        assert "visibility" not in header, f"{noun} template should not include 'visibility'"
        assert "access" not in header, f"{noun} template should not include 'access'"

    def test_csv_files_still_includes_access_columns(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--csv", "--all")
        assert code == 0
        header = _parse_csv_header(stdout)
        assert "visibility" in header
        assert "access" in header


# ---------------------------------------------------------------------------
# 4. Mutual exclusion
# ---------------------------------------------------------------------------


class TestFormatMutualExclusion:
    """Format flags --csv, --json, --template are mutually exclusive."""

    def test_csv_template_mutually_exclusive(self):
        _, _, code = run_fp("view", "files", "--csv", "--template")
        assert code != 0

    def test_csv_json_mutually_exclusive(self):
        _, _, code = run_fp("view", "files", "--csv", "--json")
        assert code != 0


# ---------------------------------------------------------------------------
# 5. Composition with filters
# ---------------------------------------------------------------------------


class TestCsvComposition:
    """--csv composes with filter flags and --all."""

    def test_csv_composes_with_project_filter(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--project", "1", "--csv")

        assert code == 0
        assert "readme.md" in stdout

    def test_csv_composes_with_all_flag(self):
        conn = _seeded_format_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--all", "--csv")

        assert code == 0
        assert "readme.md" in stdout
