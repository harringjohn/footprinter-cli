"""Tests for fp view --project and --client filter flags (FPR-1853).

Validates:
  1. --project filters files, folders, and emails by project_id
  2. --client filters emails by client_id
  3. Filters compose with --verbose and --json
  4. Nonexistent project/client IDs produce clear errors
  5. Unsupported flag + noun combos are rejected by argparse
"""

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

from conftest import run_fp


def _seeded_view_db():
    """Build an in-memory DB with projects, clients, files, folders, and emails."""
    from footprinter.ingest.database import Database

    db = Database(":memory:")
    conn = db.conn
    assert conn is not None

    conn.execute(
        "INSERT INTO clients (id, name, slug, client_type, status) "
        "VALUES (1, 'Acme Corp', 'acme', 'external', 'listed')"
    )
    conn.execute(
        "INSERT INTO clients (id, name, slug, client_type, status) "
        "VALUES (2, 'Beta Inc', 'beta', 'external', 'listed')"
    )

    conn.execute(
        "INSERT INTO projects (id, name, status, client_id) "
        "VALUES (1, 'Alpha Project', 'listed', 1)"
    )
    conn.execute(
        "INSERT INTO projects (id, name, status, client_id) "
        "VALUES (2, 'Beta Project', 'listed', 2)"
    )

    # Files: 2 in project 1, 1 in project 2
    conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, project_id) "
        "VALUES (1, 'alpha-readme.md', '/tmp/alpha/readme.md', 'local', 'listed', 'text', 100, 1)"
    )
    conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, project_id) "
        "VALUES (2, 'alpha-notes.md', '/tmp/alpha/notes.md', 'local', 'listed', 'text', 200, 1)"
    )
    conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, project_id) "
        "VALUES (3, 'beta-spec.md', '/tmp/beta/spec.md', 'local', 'listed', 'text', 300, 2)"
    )

    # Folders: 1 in project 1, 1 in project 2
    conn.execute(
        "INSERT INTO folders (id, path, relative_path, name, source, project_id) "
        "VALUES (1, '/tmp/alpha', 'alpha', 'alpha', 'local', 1)"
    )
    conn.execute(
        "INSERT INTO folders (id, path, relative_path, name, source, project_id) "
        "VALUES (2, '/tmp/beta', 'beta', 'beta', 'local', 2)"
    )

    # Emails: mixed project and client assignments
    conn.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, from_address, subject, "
        "received_at, project_id, client_id) "
        "VALUES (1, 'msg-1', 'thr-1', 'work', 'alice@acme.com', 'Acme Q1 Report', "
        "'2026-01-01', 1, 1)"
    )
    conn.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, from_address, subject, "
        "received_at, project_id, client_id) "
        "VALUES (2, 'msg-2', 'thr-2', 'work', 'bob@beta.com', 'Beta Kickoff', "
        "'2026-01-02', 2, 2)"
    )
    conn.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, from_address, subject, "
        "received_at, project_id, client_id) "
        "VALUES (3, 'msg-3', 'thr-3', 'work', 'carol@acme.com', 'Acme Follow-up', "
        "'2026-01-03', 2, 1)"
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
# 1. Project filtering
# ---------------------------------------------------------------------------


class TestProjectFilterFiles:
    """fp view files --project <id> filters by project_id."""

    def test_filters_by_project(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--project", "1")

        assert code == 0
        assert "alpha-readme" in stdout
        assert "alpha-notes" in stdout
        assert "beta-spec" not in stdout

    def test_nonexistent_project_errors(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, stderr, code = run_fp("view", "files", "--project", "999")

        assert code == 1
        output = stdout + stderr
        assert "999" in output
        assert "not found" in output.lower()


class TestProjectFilterFolders:
    """fp view folders --project <id> filters by project_id."""

    def test_filters_by_project(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "folders", "--project", "1")

        assert code == 0
        assert "alpha" in stdout
        assert "beta" not in stdout


class TestProjectFilterEmails:
    """fp view emails --project <id> filters by project_id."""

    def test_filters_by_project(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "emails", "--project", "1")

        assert code == 0
        assert "Acme Q1 Report" in stdout
        assert "Beta Kickoff" not in stdout


# ---------------------------------------------------------------------------
# 2. Client filtering
# ---------------------------------------------------------------------------


class TestClientFilterEmails:
    """fp view emails --client <id> filters by client_id."""

    def test_filters_by_client(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "emails", "--client", "1")

        assert code == 0
        assert "Acme Q1 Report" in stdout
        assert "Acme Follow-up" in stdout
        assert "Beta Kickoff" not in stdout

    def test_nonexistent_client_errors(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, stderr, code = run_fp("view", "emails", "--client", "999")

        assert code == 1
        output = stdout + stderr
        assert "999" in output
        assert "not found" in output.lower()


# ---------------------------------------------------------------------------
# 3. Composition with other flags
# ---------------------------------------------------------------------------


class TestFilterComposition:
    """Filters compose with --verbose and --json."""

    def test_project_with_verbose(self):
        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--project", "1", "--verbose")

        assert code == 0
        assert "2 total" in stdout
        assert "beta" not in stdout.lower()

    def test_project_with_json(self):
        import json

        conn = _seeded_view_db()
        with patch("footprinter.cli.view.open_db", return_value=_open_db_stub(conn)):
            stdout, _, code = run_fp("view", "files", "--project", "1", "--json")

        assert code == 0
        data = json.loads(stdout)
        names = [f["name"] for f in data["files"]]
        assert "alpha-readme.md" in names
        assert "beta-spec.md" not in names


# ---------------------------------------------------------------------------
# 4. Unsupported flag + noun rejection
# ---------------------------------------------------------------------------


class TestUnsupportedFlagRejection:
    """Flags not registered on a noun are rejected by argparse."""

    def test_chats_rejects_project_flag(self):
        _, _, code = run_fp("view", "chats", "--project", "1")
        assert code == 2

    def test_visits_rejects_project_flag(self):
        _, _, code = run_fp("view", "visits", "--project", "1")
        assert code == 2

    def test_files_rejects_client_flag(self):
        _, _, code = run_fp("view", "files", "--client", "1")
        assert code == 2
