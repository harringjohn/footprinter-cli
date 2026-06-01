"""Tests for single-entity panel view access fields (FPR-1846).

Validates:
  1. Panel shows resolved visibility, access, and source
  2. Panel access display is consistent with --json output
  3. mcp_* keys remain hidden in panel view
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

from conftest import run_fp


def _seeded_panel_db():
    """Build an in-memory DB with a single file with explicit access fields."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn
    assert conn is not None

    conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, "
        "visibility, access) "
        "VALUES (1, 'readme.md', '/tmp/test/readme.md', 'local', 'listed', 'text', 100, "
        "'full', 'allow')"
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


class TestSingleEntityPanelAccess:
    """fp view file <id> panel shows resolved access fields in a dedicated section."""

    def test_panel_has_access_section(self):
        conn = _seeded_panel_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "file", "1")

        assert code == 0
        assert "Access" in stdout

    def test_panel_shows_enriched_source(self):
        conn = _seeded_panel_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "file", "1")

        assert code == 0
        assert "cached" in stdout

    def test_panel_hides_raw_access_fields(self):
        conn = _seeded_panel_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "file", "1")

        assert code == 0
        assert "visibility_raw" not in stdout
        assert "access_raw" not in stdout

    def test_panel_hides_mcp_keys(self):
        conn = _seeded_panel_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "file", "1")

        assert code == 0
        assert "mcp_" not in stdout.lower()

    def test_panel_access_consistent_with_json(self):
        conn = _seeded_panel_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            json_stdout, _, json_code = run_fp("view", "file", "1", "--json")

        assert json_code == 0
        data = json.loads(json_stdout)

        conn2 = _seeded_panel_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn2)):
            panel_stdout, _, panel_code = run_fp("view", "file", "1")

        assert panel_code == 0
        for key in ("visibility", "access", "access_source"):
            val = str(data[key])
            assert val in panel_stdout, f"JSON {key}={val!r} not found in panel output"
