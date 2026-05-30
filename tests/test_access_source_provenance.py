"""Tests for access_source provenance tracking."""

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
        "INSERT INTO projects (id, name, client_id) VALUES (3, 'Widget', 5)"
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
            assert "visibility_source" in cols, f"{table} missing visibility_source"
            assert "access_source" in cols, f"{table} missing access_source"

    def test_source_columns_default_null(self, conn):
        conn.execute(
            "INSERT INTO files (id, source, name) VALUES (999, 'local', 'test.txt')"
        )
        row = conn.execute(
            "SELECT visibility_source, access_source FROM files WHERE id = 999"
        ).fetchone()
        assert row["visibility_source"] is None
        assert row["access_source"] is None


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

        row = conn.execute("SELECT visibility, visibility_source FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "hidden"
        assert row["visibility_source"] == "source:files"

    def test_stamp_inherit_source_writes_null_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "global")

        row = conn.execute("SELECT visibility, visibility_source FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "inherit"
        assert row["visibility_source"] is None

    def test_stamp_specific_policy_writes_permission_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'deny')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "source:files")

        row = conn.execute("SELECT access, access_source FROM files WHERE id = 1").fetchone()
        assert row["access"] == "deny"
        assert row["access_source"] == "source:files"

    def test_stamp_permission_inherit_writes_null_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'deny')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "global")

        row = conn.execute("SELECT access, access_source FROM files WHERE id = 1").fetchone()
        assert row["access"] == "inherit"
        assert row["access_source"] is None

    def test_stamp_project_scope_writes_project_source(self, conn):
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:3', 'hidden')")
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "project:3")

        row1 = conn.execute("SELECT visibility, visibility_source FROM files WHERE id = 1").fetchone()
        assert row1["visibility"] == "hidden"
        assert "project:3" in row1["visibility_source"]

        row2 = conn.execute("SELECT visibility_source FROM files WHERE id = 2").fetchone()
        assert row2["visibility_source"] is None

    def test_stamp_folder_scope_writes_folder_source(self, conn):
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO visibility_policies (scope, setting) VALUES ('folder:/Users/me/Work/', 'hidden')"
        )
        conn.commit()

        from footprinter.access_stamper import recalculate_access

        recalculate_access(conn, "folder:/Users/me/Work/")

        row = conn.execute("SELECT visibility, visibility_source FROM files WHERE id = 1").fetchone()
        assert row["visibility"] == "hidden"
        assert row["visibility_source"] is not None
        assert "folder:" in row["visibility_source"]


# ---------------------------------------------------------------------------
# RED 3 — Display: enrich uses stored source
# ---------------------------------------------------------------------------


class TestEnrichSourceDisplay:
    def test_enrich_uses_stored_source(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"access": "allow", "access_source": "project:3", "visibility": "full"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] == "project:3"

    def test_enrich_falls_back_to_cached_when_source_absent(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"access": "allow", "visibility": "full"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] == "cached"

    def test_enrich_falls_back_to_cached_when_source_null(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"access": "allow", "access_source": None, "visibility": "full"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] == "cached"

    def test_enrich_inherit_unchanged(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"access": "inherit", "visibility": "inherit"}
        enrich_verbose_access([row], "file")
        assert row["access_source"] in ("global", "baseline")

    def test_enrich_drops_internal_source_fields(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {
            "access": "allow",
            "access_source": "source:files",
            "visibility": "hidden",
            "visibility_source": "folder:~/Work/",
        }
        enrich_verbose_access([row], "file")
        assert "visibility_raw" in row
        assert "access_raw" in row
        assert "visibility_source" in row
        assert row["visibility_source"] == "folder:~/Work/"

    def test_enrich_reorders_access_fields(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {
            "id": 1,
            "name": "a.py",
            "path": "/tmp/a.py",
            "visibility": "full",
            "access": "allow",
            "visibility_source": "source:files",
            "access_source": "project:3",
        }
        enrich_verbose_access([row], "file")
        keys = list(row.keys())
        expected = ["visibility_raw", "access_raw", "visibility", "access", "access_source", "visibility_source"]
        assert keys[-6:] == expected

    def test_enrich_folder_without_access(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"id": 30, "name": "widget", "visibility": "full"}
        enrich_verbose_access([row], "folder")
        keys = list(row.keys())
        expected = ["visibility_raw", "access_raw", "visibility", "access", "access_source", "visibility_source"]
        assert keys[-6:] == expected
        assert row["access"] == "—"
        assert row["access_source"] == "—"

    def test_enrich_visibility_source_from_stored_value(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {
            "visibility": "opaque",
            "visibility_source": "folder:~/Work",
            "access": "allow",
            "access_source": "project:3",
        }
        enrich_verbose_access([row], "file")
        assert row["visibility_source"] == "folder:~/Work"
        assert row["access_source"] == "project:3"

    def test_enrich_visibility_source_inherits_to_baseline(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"visibility": "inherit", "access": "inherit"}
        enrich_verbose_access([row], "file")
        assert row["visibility_source"] in ("global", "baseline")

    def test_enrich_folder_visibility_source_cached(self):
        from footprinter.cli._common import enrich_verbose_access

        row = {"id": 30, "name": "widget", "visibility": "full", "visibility_source": "folder:~/Work"}
        enrich_verbose_access([row], "folder")
        assert row["visibility_source"] == "folder:~/Work"
