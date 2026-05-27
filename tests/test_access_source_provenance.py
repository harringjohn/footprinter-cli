"""Tests for FPR-1789 — access_source provenance tracking."""

import sqlite3

import pytest

from footprinter.ingest.db.schema import ACCESS_CONTROL_TABLES


@pytest.fixture
def conn(tool_db):
    yield tool_db


def _seed_entities(conn):
    """Insert minimal rows across all entity tables for testing."""
    cur = conn.cursor()
    cur.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (5, 'Acme', 'acme', 'external')")
    cur.execute(
        "INSERT INTO projects (id, project_name, root_path, client_id) VALUES (3, 'Widget', '/Users/me/Work/widget', 5)"
    )
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id) "
        "VALUES (1, 'local', 'a.py', '/Users/me/Work/widget/a.py', 'work', 3)"
    )
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id) "
        "VALUES (2, 'local', 'b.py', '/Users/me/Personal/b.py', 'personal', NULL)"
    )
    cur.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id, client_id) "
        "VALUES (10, 'msg1', 't1', 'personal', 'Hello', '2024-01-01', 3, 5)"
    )
    cur.execute(
        "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
        "VALUES (20, 'chat1', 'claude', 'Debug session', 3, 5)"
    )
    cur.execute(
        "INSERT INTO folders (id, path, relative_path, name, project_id) "
        "VALUES (30, '/Users/me/Work/widget', 'Work/widget', 'widget', 3)"
    )
    cur.execute(
        "INSERT INTO visits (id, url, title, visit_time, browser) "
        "VALUES (40, 'https://example.com', 'Example', '2024-01-15T10:00:00', 'chrome')"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# RED 1 — Schema: source columns exist
# ---------------------------------------------------------------------------


class TestSchemaSourceColumns:
    def test_source_columns_exist_in_fresh_db(self, conn):
        for table in ACCESS_CONTROL_TABLES:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            assert "mcp_view_source" in cols, f"{table} missing mcp_view_source"
            assert "mcp_read_source" in cols, f"{table} missing mcp_read_source"

    def test_source_columns_default_null(self, conn):
        conn.execute(
            "INSERT INTO files (id, source, name) VALUES (999, 'local', 'test.txt')"
        )
        row = conn.execute(
            "SELECT mcp_view_source, mcp_read_source FROM files WHERE id = 999"
        ).fetchone()
        assert row["mcp_view_source"] is None
        assert row["mcp_read_source"] is None


# ---------------------------------------------------------------------------
# RED 2 — Stamper: source is persisted
# ---------------------------------------------------------------------------


class TestStamperSourcePersistence:
    def test_stamp_specific_policy_writes_visibility_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'hidden')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "source:files")

        row = conn.execute("SELECT mcp_view, mcp_view_source FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"
        assert row["mcp_view_source"] == "source:files"

    def test_stamp_inherit_source_writes_null_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "global")

        row = conn.execute("SELECT mcp_view, mcp_view_source FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "inherit"
        assert row["mcp_view_source"] is None

    def test_stamp_specific_policy_writes_permission_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'deny')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "source:files")

        row = conn.execute("SELECT mcp_read, mcp_read_source FROM files WHERE id = 1").fetchone()
        assert row["mcp_read"] == "deny"
        assert row["mcp_read_source"] == "source:files"

    def test_stamp_permission_inherit_writes_null_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'deny')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "global")

        row = conn.execute("SELECT mcp_read, mcp_read_source FROM files WHERE id = 1").fetchone()
        assert row["mcp_read"] == "inherit"
        assert row["mcp_read_source"] is None

    def test_stamp_project_scope_writes_project_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:3', 'hidden')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "project:3")

        row1 = conn.execute("SELECT mcp_view, mcp_view_source FROM files WHERE id = 1").fetchone()
        assert row1["mcp_view"] == "hidden"
        assert "project:3" in row1["mcp_view_source"]

        row2 = conn.execute("SELECT mcp_view_source FROM files WHERE id = 2").fetchone()
        assert row2["mcp_view_source"] is None

    def test_stamp_folder_scope_writes_folder_source(self, conn):
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO visibility_policies (scope, setting) VALUES ('folder:/Users/me/Work/', 'hidden')"
        )
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "folder:/Users/me/Work/")

        row = conn.execute("SELECT mcp_view, mcp_view_source FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"
        assert row["mcp_view_source"] is not None
        assert "folder:" in row["mcp_view_source"]


# ---------------------------------------------------------------------------
# RED 3 — Display: enrich uses stored source
# ---------------------------------------------------------------------------


class TestEnrichSourceDisplay:
    def test_enrich_uses_stored_source(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"mcp_read": "allow", "mcp_read_source": "project:3", "mcp_view": "visible"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] == "project:3"

    def test_enrich_falls_back_to_cached_when_source_absent(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"mcp_read": "allow", "mcp_view": "visible"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] == "cached"

    def test_enrich_falls_back_to_cached_when_source_null(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"mcp_read": "allow", "mcp_read_source": None, "mcp_view": "visible"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] == "cached"

    def test_enrich_inherit_unchanged(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"mcp_read": "inherit", "mcp_view": "inherit"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] in ("global", "baseline")

    def test_enrich_visibility_source_populated(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {
            "mcp_read": "allow",
            "mcp_read_source": "source:files",
            "mcp_view": "hidden",
            "mcp_view_source": "folder:~/Work/",
        }
        enrich_verbose_access([row], "file")
        assert row["visibility_source"] == "folder:~/Work/"
