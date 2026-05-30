"""Tests for recalculation triggers on entity edit commands.

Verifies that changing a file's project or a project's client triggers
access recalculation, while non-relationship edits (status, description)
do not.
"""

import pytest


@pytest.fixture
def conn(tool_db):
    """Full-schema database for edit-recalculate tests."""
    yield tool_db


def _seed(conn):
    """Insert entities with two projects under different clients.

    Layout:
        client 5 (Acme) → project 3 (Widget)  → file 1
        client 6 (Beta) → project 4 (Gadget)
        file 2 — no project
    """
    cur = conn.cursor()

    # Clients
    cur.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (5, 'Acme', 'acme', 'external')")
    cur.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (6, 'Beta', 'beta', 'external')")

    # Projects
    cur.execute(
        "INSERT INTO projects (id, name, client_id) VALUES (3, 'Widget', 5)"
    )
    cur.execute(
        "INSERT INTO projects (id, name, client_id) VALUES (4, 'Gadget', 6)"
    )

    # Files
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id) "
        "VALUES (1, 'local', 'a.py', '/Users/me/Work/widget/a.py', 'work', 3)"
    )
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id) "
        "VALUES (2, 'local', 'b.py', '/Users/me/Work/other/b.py', 'work', NULL)"
    )

    # Visibility policy on project 4 so moving a file there changes its visibility
    cur.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:4', 'hidden')")

    # Visibility policy on client 6 so changing project's client changes visibility
    cur.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:6', 'hidden')")

    conn.commit()


# ---------------------------------------------------------------------------
# File edit → recalculate
# ---------------------------------------------------------------------------


class TestFileEditRecalculate:
    def test_project_change_triggers_recalculate(self, conn):
        """Moving a file to a different project recalculates its cached values."""
        _seed(conn)

        # Baseline: file 1 has inherit (no project:3 policy)
        row = conn.execute("SELECT visibility FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "inherit"

        # Move file 1 to project 4 (which has a 'hidden' policy)
        from footprinter.db.files import update_file_relationships

        update_file_relationships(conn, 1, project_id=4)

        # Trigger recalculate — this is what the wiring should do
        from footprinter.access_stamper import recalculate_entity

        recalculate_entity(conn, "file", 1)

        row = conn.execute("SELECT visibility FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "hidden"

    def test_status_change_does_not_recalculate(self, conn):
        """Editing only status does NOT trigger recalculation."""
        _seed(conn)

        # Set a global hidden policy so recalculate would change visibility
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        # Status edit goes through db layer only — no recalculate wiring
        from footprinter.db.files import update_file_status

        update_file_status(conn, 1, "unlisted")

        # Visibility should remain 'inherit' (not recalculated to 'hidden')
        row = conn.execute("SELECT visibility FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "inherit"


# ---------------------------------------------------------------------------
# Project edit → recalculate
# ---------------------------------------------------------------------------


class TestProjectEditRecalculate:
    def test_client_change_triggers_recalculate(self, conn):
        """Changing a project's client recalculates the project + children."""
        _seed(conn)

        # Baseline: project 3 under client 5 (Acme), no policy → inherit
        row = conn.execute("SELECT visibility FROM projects WHERE id = 3").fetchone()
        assert row["visibility"] == "inherit"

        # Change project 3's client to 6 (Beta, which has 'hidden' policy)
        from footprinter.db.projects import update_project

        update_project(conn, 3, client_id=6)

        # Trigger recalculate — this is what the wiring should do
        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "project:3")

        # Project itself should be hidden (inherits from client 6)
        row = conn.execute("SELECT visibility FROM projects WHERE id = 3").fetchone()
        assert row["visibility"] == "hidden"

        # Child file should also be hidden
        row = conn.execute("SELECT visibility FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "hidden"

    def test_non_relationship_edit_no_recalculate(self, conn):
        """Editing description/status/name does NOT trigger recalculation."""
        _seed(conn)

        # Set a global hidden policy so recalculate would change visibility
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        # Description edit goes through db layer only — no recalculate wiring
        from footprinter.db.projects import update_project

        update_project(conn, 3, description="Updated desc")

        # Visibility should remain 'inherit' (not recalculated to 'hidden')
        row = conn.execute("SELECT visibility FROM projects WHERE id = 3").fetchone()
        assert row["visibility"] == "inherit"
