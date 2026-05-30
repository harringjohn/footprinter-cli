"""
Schema drift detection and database operation tests.

Asserts **exact** column sets (not subsets), verifies all tables exist,
checks foreign keys, and validates basic CRUD operations and index
enforcement.  Any column added to schema.py or queried in blueprints but
missing from init_db() will cause a failure here.
"""

import json
import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

# ========================================
# Expected column sets — single source of truth.
# Both TestCompleteColumnSets (exact match) and
# TestFullOldSchemaConvergence (subset check) reference these.
# ========================================
EXPECTED_COLUMNS = {
    "files": {
        "id",
        "source",
        "external_id",
        "account",
        "name",
        "path",
        "content_type",
        "mime_type",
        "size_bytes",
        "created_at",
        "modified_at",
        "accessed_at",
        "indexed_at",
        "updated_at",
        "content_preview",
        "sha256_hash",
        "vectorized_at",
        "vectorized_chunks",
        "project_id",
        "client_id",
        "metadata",
        "folder_id",
        "md5_hash",
        "status",
        "status_reason",
        "status_changed_at",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "display_name",
        "vectorize",
    },
    "projects": {
        "id",
        "name",
        "description",
        "status",
        "status_reason",
        "created_at",
        "updated_at",
        "slug",
        "status_changed_at",
        "client_id",
        "client",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "display_name",
    },
    "clients": {
        "id",
        "name",
        "slug",
        "client_type",
        "status",
        "status_reason",
        "created_at",
        "updated_at",
        "status_changed_at",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "display_name",
    },
    "folders": {
        "id",
        "path",
        "relative_path",
        "name",
        "parent_path",
        "file_count",
        "scanned_at",
        "created_at",
        "project_id",
        "source",
        "external_id",
        "account",
        "parent_folder_id",
        "direct_file_count",
        "total_file_count",
        "total_size_bytes",
        "visibility",
        "access",
        "visibility_source",
        "access_source",
        "status",
        "status_reason",
        "status_changed_at",
        "client_id",
        "indexed_at",
        "updated_at",
        "display_name",
    },
    "chats": {
        "id",
        "external_id",
        "account",
        "title",
        "created_at",
        "modified_at",
        "message_count",
        "indexed_at",
        "updated_at",
        "metadata",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "client_id",
        "project_id",
        "metadata_vectorized_at",
        "status",
        "merged_into_id",
        "display_name",
        "vectorize",
    },
    "messages": {
        "id",
        "chat_id",
        "message_id",
        "role",
        "content",
        "created_at",
        "metadata",
        "vectorized_at",
        "vectorized_chunks",
        "indexed_at",
        "updated_at",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "status",
        "display_name",
        "vectorize",
    },
    "emails": {
        "id",
        "message_id",
        "thread_id",
        "account",
        "from_address",
        "from_name",
        "to_addresses",
        "cc_addresses",
        "subject",
        "body_preview",
        "received_at",
        "labels",
        "has_attachments",
        "is_read",
        "indexed_at",
        "updated_at",
        "metadata",
        "status",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "client_id",
        "project_id",
        "created_at",
        "display_name",
    },
    "visits": {
        "id",
        "url",
        "title",
        "visit_time",
        "browser",
        "visit_count",
        "indexed_at",
        "updated_at",
        "status",
        "access",
        "visibility",
        "access_source",
        "visibility_source",
        "client_id",
        "project_id",
        "created_at",
        "display_name",
    },
    "sources": {
        "name",
        "source_type",
        "adapter",
        "account",
        "label",
        "icon",
        "enabled",
        "config",
        "created_at",
        "updated_at",
    },
    "ingests": {
        "id",
        "pipe",
        "started_at",
        "completed_at",
        "status",
        "mode",
        "trigger",
        "items_processed",
        "items_new",
        "items_updated",
        "items_skipped",
        "errors",
        "elapsed_seconds",
        "metadata",
    },
}


class TestSchemaIdempotency:
    """Verify init_db() can be called repeatedly without error."""

    def test_init_db_idempotent(self, temp_db):
        """Create Database twice on same file — no error, sources preserved."""
        from footprinter.ingest.database import Database

        db1 = Database(temp_db)
        cursor = db1.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sources")
        count1 = cursor.fetchone()[0]
        db1.close()

        db2 = Database(temp_db)
        cursor = db2.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sources")
        count2 = cursor.fetchone()[0]
        db2.close()

        assert count2 == count1

    def test_init_db_idempotent_with_data(self, temp_db):
        """Init DB, insert a row, init again — row survives."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("INSERT INTO files (source, name, path) VALUES ('local', 'test.txt', '/tmp/test.txt')")
        db.conn.commit()
        db.close()

        # Re-init on same file
        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files WHERE path = '/tmp/test.txt'")
        assert cursor.fetchone()[0] == 1
        db.close()


class TestVectorizeColumnMigration:
    """Verify _ensure_vectorize_column() backfills from JSON metadata."""

    def test_backfill_copies_json_flag_to_column(self, temp_db):
        """Simulate old DB without vectorize column, then let init_db() migrate."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute("ALTER TABLE files DROP COLUMN vectorize")
        db.conn.execute("ALTER TABLE chats DROP COLUMN vectorize")
        db.conn.execute("ALTER TABLE messages DROP COLUMN vectorize")
        db.conn.execute(
            "INSERT INTO files (source, name, metadata) "
            "VALUES ('local', 'a.txt', '{\"vectorize\": 0}')"
        )
        db.conn.execute(
            "INSERT INTO files (source, name, metadata) "
            "VALUES ('local', 'b.txt', '{\"vectorize\": 1}')"
        )
        db.conn.execute(
            "INSERT INTO files (source, name) VALUES ('local', 'c.txt')"
        )
        db.conn.execute(
            "INSERT INTO chats (external_id, account, metadata) "
            "VALUES ('ch1', 'test', '{\"vectorize\": 0}')"
        )
        db.conn.execute(
            "INSERT INTO messages (chat_id, role, metadata) "
            "VALUES (1, 'user', '{\"vectorize\": 0}')"
        )
        db.conn.commit()
        db.close()

        db2 = Database(temp_db)
        row_a = db2.conn.execute(
            "SELECT vectorize FROM files WHERE name = 'a.txt'"
        ).fetchone()
        row_b = db2.conn.execute(
            "SELECT vectorize FROM files WHERE name = 'b.txt'"
        ).fetchone()
        row_c = db2.conn.execute(
            "SELECT vectorize FROM files WHERE name = 'c.txt'"
        ).fetchone()
        chat_row = db2.conn.execute(
            "SELECT vectorize FROM chats WHERE external_id = 'ch1'"
        ).fetchone()
        msg_row = db2.conn.execute(
            "SELECT vectorize FROM messages WHERE role = 'user'"
        ).fetchone()
        db2.close()

        assert row_a[0] == 0, "vectorize=0 should be backfilled from JSON"
        assert row_b[0] == 1, "vectorize=1 should remain default"
        assert row_c[0] == 1, "no metadata → default 1"
        assert chat_row[0] == 0, "chat vectorize=0 backfilled"
        assert msg_row[0] == 0, "message vectorize=0 backfilled"


class TestAccessColumnMigration:
    """Verify _migrate_access_columns() renames mcp_view/mcp_read → visibility/access."""

    def test_migration_renames_columns_and_values(self, temp_db):
        """Simulate old DB with mcp_view/mcp_read, then let init_db() migrate."""
        from footprinter.ingest.database import Database
        from footprinter.ingest.db.schema import ACCESS_CONTROL_TABLES

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO files (source, name, visibility, access) "
            "VALUES ('local', 'full.txt', 'full', 'allow')"
        )
        db.conn.execute(
            "INSERT INTO files (source, name, visibility, access) "
            "VALUES ('local', 'hidden.txt', 'hidden', 'deny')"
        )
        db.conn.execute(
            "INSERT INTO files (source, name, visibility, access) "
            "VALUES ('local', 'inherit.txt', 'inherit', 'inherit')"
        )
        db.conn.commit()

        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(files)")
        col_names = {row[1] for row in cursor.fetchall()}
        assert "visibility" in col_names, "Fresh DB should have 'visibility' column"
        assert "access" in col_names, "Fresh DB should have 'access' column"
        assert "mcp_view" not in col_names, "Fresh DB should NOT have 'mcp_view'"
        assert "mcp_read" not in col_names, "Fresh DB should NOT have 'mcp_read'"

        for table in ACCESS_CONTROL_TABLES:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in cursor.fetchall()}
            assert "visibility" in cols, f"{table} missing 'visibility'"
            assert "access" in cols, f"{table} missing 'access'"
            assert "visibility_source" in cols, f"{table} missing 'visibility_source'"
            assert "access_source" in cols, f"{table} missing 'access_source'"

        row = db.conn.execute(
            "SELECT visibility, access FROM files WHERE name = 'full.txt'"
        ).fetchone()
        assert row[0] == "full", f"Expected 'full', got {row[0]!r}"
        assert row[1] == "allow"
        db.close()

    def test_fresh_db_uses_full_not_visible(self, temp_db):
        """A fresh database should use 'full' as the visibility value, not 'visible'."""
        from footprinter.db.policies import VISIBILITY_SETTINGS, seed_visibility_defaults
        from footprinter.ingest.database import Database
        from footprinter.visibility import is_readable

        assert "full" in VISIBILITY_SETTINGS
        assert "visible" not in VISIBILITY_SETTINGS

        db = Database(temp_db)
        seed_visibility_defaults(db.conn)
        row = db.conn.execute(
            "SELECT setting FROM visibility_policies WHERE scope = 'global'"
        ).fetchone()
        assert row[0] == "full", f"Expected 'full', got {row[0]!r}"

        assert is_readable("full") is True
        assert is_readable("opaque") is False
        db.close()

    def test_old_schema_migration(self, temp_db):
        """Create DB with old column names, reopen → migration renames them."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database
        from footprinter.ingest.db.schema import ACCESS_CONTROL_TABLES

        db = Database(temp_db)
        db.close()

        conn2 = _sqlite3.connect(temp_db)
        conn2.row_factory = _sqlite3.Row

        for table in ACCESS_CONTROL_TABLES:
            conn2.execute(f"ALTER TABLE {table} RENAME COLUMN visibility TO mcp_view")
            conn2.execute(f"ALTER TABLE {table} RENAME COLUMN access TO mcp_read")
            conn2.execute(f"ALTER TABLE {table} RENAME COLUMN visibility_source TO mcp_view_source")
            conn2.execute(f"ALTER TABLE {table} RENAME COLUMN access_source TO mcp_read_source")

        conn2.execute("PRAGMA ignore_check_constraints = ON")
        conn2.execute(
            "INSERT INTO files (source, name, mcp_view, mcp_read) "
            "VALUES ('local', 'oldfile.txt', 'full', 'allow')"
        )
        conn2.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) "
            "VALUES ('global', 'full')"
        )
        conn2.execute("PRAGMA ignore_check_constraints = OFF")
        conn2.commit()
        conn2.close()

        db2 = Database(temp_db)
        cursor = db2.conn.cursor()
        cursor.execute("PRAGMA table_info(files)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "visibility" in cols, "Migration should rename mcp_view → visibility"
        assert "access" in cols, "Migration should rename mcp_read → access"
        assert "mcp_view" not in cols
        assert "mcp_read" not in cols

        row = db2.conn.execute(
            "SELECT visibility FROM files WHERE name = 'oldfile.txt'"
        ).fetchone()
        assert row[0] == "full", f"Migration should convert 'full' → 'full', got {row[0]!r}"

        pol = db2.conn.execute(
            "SELECT setting FROM visibility_policies WHERE scope = 'global'"
        ).fetchone()
        assert pol[0] == "full", f"Policy migration should convert 'full' → 'full', got {pol[0]!r}"
        db2.close()


class TestCompleteColumnSets:
    """Assert exact column sets — the core schema drift detector.

    Each test uses PRAGMA table_info() and asserts an exact match against
    the full column set from schema.py. Adding a column to queries without
    adding it to init_db() will fail here.
    """

    def _get_columns(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def test_files_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "files")
        expected = EXPECTED_COLUMNS["files"]
        assert columns == expected, (
            f"files column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_projects_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "projects")
        expected = EXPECTED_COLUMNS["projects"]
        assert columns == expected, (
            f"projects column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_projects_status_default_listed(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute(
            "INSERT INTO projects (name) VALUES (?)",
            ("test-project",),
        )
        db.conn.commit()

        cursor.execute(
            "SELECT status FROM projects WHERE name = ?",
            ("test-project",),
        )
        row = cursor.fetchone()
        assert row[0] == "listed", f"Expected status 'listed', got {row[0]!r}"

        cursor.execute("PRAGMA table_info(projects)")
        columns = {r[1]: r[4] for r in cursor.fetchall()}
        assert columns["status"] == "'listed'", f"Expected dflt_value \"'listed'\", got {columns['status']!r}"
        db.close()

    def test_projects_status_reason_default_null(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO projects (name) VALUES (?)",
            ("test-project-sr",),
        )
        db.conn.commit()
        cursor.execute(
            "SELECT status_reason FROM projects WHERE name = ?",
            ("test-project-sr",),
        )
        row = cursor.fetchone()
        assert row[0] is None, f"Expected status_reason NULL, got {row[0]!r}"
        db.close()

    def test_clients_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "clients")
        expected = EXPECTED_COLUMNS["clients"]
        assert columns == expected, (
            f"clients column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_clients_status_reason_default_null(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO clients (name, slug, client_type) VALUES (?, ?, ?)",
            ("test-client-sr", "test-client-sr", "external"),
        )
        db.conn.commit()
        cursor.execute(
            "SELECT status_reason FROM clients WHERE name = ?",
            ("test-client-sr",),
        )
        row = cursor.fetchone()
        assert row[0] is None, f"Expected status_reason NULL, got {row[0]!r}"
        db.close()

    def test_folders_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "folders")
        expected = EXPECTED_COLUMNS["folders"]
        assert columns == expected, (
            f"folders column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_chats_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "chats")
        expected = EXPECTED_COLUMNS["chats"]
        assert columns == expected, (
            f"chats column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_messages_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "messages")
        expected = EXPECTED_COLUMNS["messages"]
        assert columns == expected, (
            f"messages column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_emails_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "emails")
        expected = EXPECTED_COLUMNS["emails"]
        assert columns == expected, (
            f"emails column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_visits_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "visits")
        expected = EXPECTED_COLUMNS["visits"]
        assert columns == expected, (
            f"visits column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_sources_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "sources")
        expected = EXPECTED_COLUMNS["sources"]
        assert columns == expected, (
            f"sources column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()

    def test_ingests_complete_columns(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "ingests")
        expected = EXPECTED_COLUMNS["ingests"]
        assert columns == expected, (
            f"ingests column mismatch.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )
        db.close()


class TestIngestsDefaults:
    """Verify ingests table default values."""

    def test_ingests_status_default_running(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO ingests (pipe, started_at) VALUES (?, ?)",
            ("browser", "2026-04-04T12:00:00"),
        )
        db.conn.commit()

        cursor.execute("SELECT status FROM ingests WHERE pipe = 'browser'")
        row = cursor.fetchone()
        assert row[0] == "running", f"Expected status 'running', got {row[0]!r}"
        db.close()

    def test_ingests_items_default_zero(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO ingests (pipe, started_at) VALUES (?, ?)",
            ("local_files", "2026-04-04T12:00:00"),
        )
        db.conn.commit()

        cursor.execute(
            "SELECT items_processed, items_new, items_updated, "
            "items_skipped, errors FROM ingests WHERE pipe = 'local_files'"
        )
        row = cursor.fetchone()
        assert tuple(row) == (0, 0, 0, 0, 0), f"Expected all zeros, got {tuple(row)}"
        db.close()


class TestIngestsIndexes:
    """Verify ingests table indexes."""

    def test_ingests_pipe_status_index(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA index_list(ingests)")
        indexes = {row[1] for row in cursor.fetchall()}
        assert "idx_ingests_pipe_status" in indexes, f"Missing idx_ingests_pipe_status. Found: {indexes}"

        # Verify the index covers (pipe, status)
        cursor.execute("PRAGMA index_info(idx_ingests_pipe_status)")
        cols = [row[2] for row in cursor.fetchall()]
        assert cols == ["pipe", "status"], f"Expected [pipe, status], got {cols}"
        db.close()


class TestMCPColumnNames:
    """Verify access control columns use mcp_ prefix, not claude_ prefix."""

    _ACCESS_CONTROL_TABLES = (
        "files",
        "folders",
        "visits",
        "projects",
        "chats",
        "messages",
        "emails",
        "clients",
    )

    def _get_columns(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def test_all_tables_have_mcp_columns(self, temp_db):
        """All 8 entity tables must have access and visibility columns."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        for table in self._ACCESS_CONTROL_TABLES:
            columns = self._get_columns(db, table)
            assert "access" in columns, f"{table} missing access column"
            assert "visibility" in columns, f"{table} missing visibility column"
        db.close()

    def test_no_old_claude_columns(self, temp_db):
        """No entity table should have claude_can_read or claude_visibility."""
        from footprinter.ingest.database import Database

        # Build old names via concat so bulk renames don't rewrite them
        old_read = "claude_" + "can_read"
        old_view = "claude_" + "visibility"

        db = Database(temp_db)
        for table in self._ACCESS_CONTROL_TABLES:
            columns = self._get_columns(db, table)
            assert old_read not in columns, f"{table} still has {old_read}"
            assert old_view not in columns, f"{table} still has {old_view}"
        db.close()

    def test_no_old_references_in_source(self):
        """Zero references to old column names in footprinter/ source."""
        import pathlib

        root = pathlib.Path(__file__).parent.parent.parent / "footprinter"
        # Build old names via concat so bulk renames don't rewrite them
        old_names = ("claude_" + "can_read", "claude_" + "visibility")
        violations = []
        for py_file in root.rglob("*.py"):
            text = py_file.read_text()
            for name in old_names:
                if name in text:
                    violations.append(f"{py_file.relative_to(root.parent)}: {name}")
        assert not violations, "Old column names found in source:\n" + "\n".join(violations)


class TestAllTablesCreated:
    """Verify all tables created by init_db() — catches missing CREATE TABLE statements."""

    def test_all_tables_created(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}

        required_tables = {
            "files",
            "folders",
            "visits",
            "projects",
            "chats",
            "messages",
            "chats_fts",
            "files_fts",
            "emails_fts",
            "emails",
            "clients",
            "sources",
            "uploads",
            "permission_policies",
            "visibility_policies",
            "ingests",
        }
        missing = required_tables - tables
        assert not missing, f"Missing tables: {missing}"
        db.close()


class TestForeignKeys:
    """Verify REFERENCES clauses exist in the schema."""

    def _get_ddl(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
        row = cursor.fetchone()
        return row[0] if row else ""

    def test_fk_projects_client_id(self, temp_db):
        """projects.client_id → clients(id)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        # ALTER TABLE FKs don't appear in sqlite_master DDL or PRAGMA.
        # Verify the column exists and the referenced table exists.
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(projects)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "client_id" in columns

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clients'")
        assert cursor.fetchone() is not None
        db.close()

    def test_fk_files_project_id(self, temp_db):
        """files.project_id → projects(id) — defined in CREATE TABLE."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        ddl = self._get_ddl(db, "files")
        assert "project_id" in ddl
        assert "REFERENCES projects(id)" in ddl
        db.close()

    def test_fk_chats_project_id(self, temp_db):
        """chats.project_id → projects(id)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(chats)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "project_id" in columns

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
        assert cursor.fetchone() is not None
        db.close()

    def test_fk_emails_project_id(self, temp_db):
        """emails.project_id → projects(id)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(emails)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "project_id" in columns

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
        assert cursor.fetchone() is not None
        db.close()

    def test_fk_messages_chat_id(self, temp_db):
        """messages.chat_id → chats(id) — in CREATE TABLE."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        ddl = self._get_ddl(db, "messages")
        assert "chat_id" in ddl
        assert "REFERENCES chats(id)" in ddl
        db.close()


class TestForeignKeyEnforcement:
    """Verify PRAGMA foreign_keys = ON is set and actually enforced."""

    def test_insert_chat_upsert_preserves_id(self, temp_db):
        """Re-inserting a chat by external_id preserves its id and child messages."""
        from footprinter.db import chats as chats_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        # Insert a chat
        chat_id = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "test-uuid-001",
                "account": "claude",
                "title": "Original Title",
            },
        )
        assert chat_id > 0

        # Add a message referencing this chat
        msg_id = chats_db.insert_message(
            db.conn,
            {
                "chat_id": chat_id,
                "role": "user",
                "content": "Hello world",
            },
        )
        assert msg_id > 0

        # Re-insert same chat with updated title
        chat_id_2 = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "test-uuid-001",
                "account": "claude",
                "title": "Updated Title",
            },
        )

        # The id must be preserved (same row, not delete+insert)
        assert chat_id_2 == chat_id

        # Title should be updated
        cursor = db.conn.cursor()
        cursor.execute("SELECT title FROM chats WHERE id = ?", (chat_id,))
        assert cursor.fetchone()[0] == "Updated Title"

        # Message must still exist
        cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,))
        assert cursor.fetchone()[0] == 1

        db.close()

    def test_database_connection_enforces_fks(self, temp_db):
        """Database connections must have PRAGMA foreign_keys = ON."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        result = cursor.fetchone()[0]
        assert result == 1, f"foreign_keys pragma is {result}, expected 1"
        db.close()

    def test_fk_rejects_invalid_project_id(self, temp_db):
        """Inserting a file with a nonexistent project_id raises IntegrityError."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute("INSERT INTO files (name, source, project_id) VALUES ('test.txt', 'local', 99999)")
        db.close()

    def test_fk_rejects_invalid_chat_id(self, temp_db):
        """Inserting a message with a nonexistent chat_id raises IntegrityError."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute("INSERT INTO messages (chat_id, role, content) VALUES (99999, 'user', 'orphaned message')")
        db.close()

    def test_fk_allows_null_optional_fk(self, temp_db):
        """NULL foreign keys are allowed (project_id is optional on files)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("INSERT INTO files (name, source, project_id) VALUES ('no-project.txt', 'local', NULL)")
        assert cursor.lastrowid > 0
        db.close()


class TestProjectIdIndexes:
    """Verify project_id indexes exist on chats and emails."""

    def test_idx_chats_project(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_chats_project",),
        )
        assert cursor.fetchone() is not None, "idx_chats_project index not found"
        db.close()

    def test_idx_emails_project(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_emails_project",),
        )
        assert cursor.fetchone() is not None, "idx_emails_project index not found"
        db.close()


class TestFTS5Tables:
    """Verify FTS5 virtual tables, triggers, and content-sync behavior."""

    def test_files_fts_exists(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'")
        assert cursor.fetchone() is not None, "files_fts table not found"
        db.close()

    def test_emails_fts_exists(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='emails_fts'")
        assert cursor.fetchone() is not None, "emails_fts table not found"
        db.close()

    def test_chats_fts_exists(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chats_fts'")
        assert cursor.fetchone() is not None, "chats_fts table not found"
        db.close()

    def test_chats_fts_backfill(self, temp_db):
        """Backfill SQL for chats_fts correctly populates the FTS index."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Drop chats_fts triggers so the INSERT below won't auto-populate FTS
        for suffix in ("ai", "ad", "au"):
            cursor.execute(f"DROP TRIGGER IF EXISTS chats_fts_{suffix}")

        # Insert a chat row directly (bypassing triggers)
        cursor.execute(
            "INSERT INTO chats (external_id, account, title) "
            "VALUES ('test-ext-1', 'personal', 'Test Chat')"
        )
        db.conn.commit()

        # Confirm FTS index is empty (MATCH queries the actual index)
        cursor.execute("SELECT COUNT(*) FROM chats_fts WHERE chats_fts MATCH 'Test'")
        assert cursor.fetchone()[0] == 0, "FTS index should be empty before backfill"

        # Execute backfill SQL directly (same SQL the init_db backfill loop runs)
        cursor.execute(db._fts_backfill_sql("chats_fts"))
        db.conn.commit()

        # Verify FTS index now has the row
        cursor.execute("SELECT COUNT(*) FROM chats_fts WHERE chats_fts MATCH 'Test'")
        count = cursor.fetchone()[0]
        assert count == 1, f"chats_fts backfill expected 1 match, got {count}"
        db.close()

    def test_files_fts_triggers_exist(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        triggers = {row[0] for row in cursor.fetchall()}
        for name in ["files_fts_ai", "files_fts_ad", "files_fts_au"]:
            assert name in triggers, f"Trigger {name} not found"
        db.close()

    def test_emails_fts_triggers_exist(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        triggers = {row[0] for row in cursor.fetchall()}
        for name in ["emails_fts_ai", "emails_fts_ad", "emails_fts_au"]:
            assert name in triggers, f"Trigger {name} not found"
        db.close()

    def test_chats_fts_triggers_exist(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        triggers = {row[0] for row in cursor.fetchall()}
        for name in ["chats_fts_ai", "chats_fts_ad", "chats_fts_au"]:
            assert name in triggers, f"Trigger {name} not found"
        db.close()

    def test_old_chats_triggers_absent(self, temp_db):
        """Old chats_ai/ad/au trigger names must not exist after migration."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
        triggers = {row[0] for row in cursor.fetchall()}
        for name in ["chats_ai", "chats_ad", "chats_au"]:
            assert name not in triggers, f"Old trigger {name} still exists"
        db.close()

    def test_files_fts_content_sync_insert(self, temp_db):
        """INSERT into files → row appears in files_fts."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview) "
            "VALUES (1, 'local', 'report.pdf', 'quarterly revenue summary')"
        )
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"revenue\"*'").fetchone()
        assert row is not None, "FTS5 insert trigger did not fire"
        db.close()

    def test_files_fts_content_sync_delete(self, temp_db):
        """DELETE from files → row removed from files_fts."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute("INSERT INTO files (id, source, name) VALUES (1, 'local', 'delete_me.txt')")
        db.conn.commit()
        db.conn.execute("DELETE FROM files WHERE id = 1")
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"delete_me\"*'").fetchone()
        assert row is None, "FTS5 delete trigger did not fire"
        db.close()

    def test_files_fts_content_sync_update(self, temp_db):
        """UPDATE files → FTS5 reflects new values."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute("INSERT INTO files (id, source, name) VALUES (1, 'local', 'old_name.txt')")
        db.conn.commit()
        db.conn.execute("UPDATE files SET name = 'new_name.txt' WHERE id = 1")
        db.conn.commit()
        old = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"old_name\"*'").fetchone()
        new = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"new_name\"*'").fetchone()
        assert old is None, "Old value still in FTS5 after update"
        assert new is not None, "New value not in FTS5 after update"
        db.close()

    def test_emails_fts_content_sync_insert(self, temp_db):
        """INSERT into emails → row appears in emails_fts."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_name, from_address, body_preview, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Invoice attached', "
            "'Alice', 'alice@example.com', 'Please review the attached invoice', '2024-01-01')"
        )
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM emails_fts WHERE emails_fts MATCH '\"invoice\"*'").fetchone()
        assert row is not None, "emails FTS5 insert trigger did not fire"
        db.close()

    def test_emails_fts_content_sync_delete(self, temp_db):
        """DELETE from emails → row removed from emails_fts."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_address, received_at) "
            "VALUES (1, 'msg-1', 'thread-1', 'work', 'Delete me', 'a@b.com', '2024-01-01')"
        )
        db.conn.commit()
        db.conn.execute("DELETE FROM emails WHERE id = 1")
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM emails_fts WHERE emails_fts MATCH '\"Delete\"*'").fetchone()
        assert row is None, "emails FTS5 delete trigger did not fire"
        db.close()

    def test_drop_fts_triggers_removes_all_triggers(self, temp_db):
        """drop_fts_triggers() removes all 9 FTS-related triggers."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Verify triggers exist before drop
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        triggers_before = {row[0] for row in cursor.fetchall()}
        fts_triggers = {
            "files_fts_ai",
            "files_fts_ad",
            "files_fts_au",
            "emails_fts_ai",
            "emails_fts_ad",
            "emails_fts_au",
            "chats_fts_ai",
            "chats_fts_ad",
            "chats_fts_au",
        }
        assert fts_triggers.issubset(triggers_before), (
            f"Expected FTS triggers missing before drop: {fts_triggers - triggers_before}"
        )

        db.drop_fts_triggers()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        triggers_after = {row[0] for row in cursor.fetchall()}
        remaining = fts_triggers & triggers_after
        assert not remaining, f"FTS triggers still present after drop: {remaining}"

        # Base tables still writable
        cursor.execute("INSERT INTO files (source, name, path) VALUES ('local', 'test.txt', '/tmp/test.txt')")
        db.conn.commit()
        db.close()

    def test_create_fts_triggers_restores_all_triggers(self, temp_db):
        """create_fts_triggers() restores triggers after drop."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.drop_fts_triggers()
        db.create_fts_triggers()

        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        triggers = {row[0] for row in cursor.fetchall()}
        for name in [
            "files_fts_ai",
            "files_fts_ad",
            "files_fts_au",
            "emails_fts_ai",
            "emails_fts_ad",
            "emails_fts_au",
            "chats_fts_ai",
            "chats_fts_ad",
            "chats_fts_au",
        ]:
            assert name in triggers, f"Trigger {name} not restored"

        # Verify trigger fires: insert into files, check files_fts
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview) "
            "VALUES (1, 'local', 'trigger_test.txt', 'trigger content')"
        )
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"trigger\"*'").fetchone()
        assert row is not None, "Restored trigger did not fire on insert"
        db.close()

    def test_rebuild_fts_indexes_repopulates_from_base_tables(self, temp_db):
        """rebuild_fts_indexes() repopulates FTS from base table data."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)

        # Insert with triggers active
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview) "
            "VALUES (1, 'local', 'existing.txt', 'existing preview')"
        )
        db.conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_name, from_address, body_preview, received_at) "
            "VALUES (1, 'msg-1', 'thr-1', 'work', 'Old email', "
            "'Bob', 'bob@test.com', 'old body', '2024-01-01')"
        )
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title) "
            "VALUES (1, 'chat-1', 'personal', 'Old chat')"
        )
        db.conn.commit()

        # Drop triggers, insert more rows (won't appear in FTS)
        db.drop_fts_triggers()
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview) "
            "VALUES (2, 'local', 'new_file.txt', 'new preview')"
        )
        db.conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_name, from_address, body_preview, received_at) "
            "VALUES (2, 'msg-2', 'thr-2', 'work', 'New email', "
            "'Alice', 'alice@test.com', 'new body', '2024-02-01')"
        )
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title) "
            "VALUES (2, 'chat-2', 'personal', 'New chat')"
        )
        db.conn.commit()

        # Rebuild — should contain ALL rows
        db.rebuild_fts_indexes()

        # files_fts: both old and new
        row = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"existing\"*'").fetchone()
        assert row is not None, "Existing file not in FTS after rebuild"
        row = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"new_file\"*'").fetchone()
        assert row is not None, "New file not in FTS after rebuild"

        # emails_fts: both old and new
        row = db.conn.execute("SELECT * FROM emails_fts WHERE emails_fts MATCH '\"Old email\"'").fetchone()
        assert row is not None, "Old email not in FTS after rebuild"
        row = db.conn.execute("SELECT * FROM emails_fts WHERE emails_fts MATCH '\"New email\"'").fetchone()
        assert row is not None, "New email not in FTS after rebuild"

        # chats_fts: both old and new
        row = db.conn.execute("SELECT * FROM chats_fts WHERE chats_fts MATCH '\"Old chat\"'").fetchone()
        assert row is not None, "Old chat not in FTS after rebuild"
        row = db.conn.execute("SELECT * FROM chats_fts WHERE chats_fts MATCH '\"New chat\"'").fetchone()
        assert row is not None, "New chat not in FTS after rebuild"

        db.close()

    def test_rebuild_fts_indexes_recreates_triggers(self, temp_db):
        """rebuild_fts_indexes() restores triggers so subsequent inserts sync."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.drop_fts_triggers()
        db.rebuild_fts_indexes()

        # Insert after rebuild — trigger should fire
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview) "
            "VALUES (1, 'local', 'post_rebuild.txt', 'post rebuild content')"
        )
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM files_fts WHERE files_fts MATCH '\"post_rebuild\"*'").fetchone()
        assert row is not None, "Trigger not restored after rebuild_fts_indexes()"
        db.close()


class TestFTSTriggerManagementNoFTS5:
    """FTS trigger management degrades gracefully without FTS5."""

    def test_fts_trigger_management_noop_without_fts5(self, temp_db):
        """drop/rebuild must not raise when FTS5 is unavailable."""
        from footprinter.ingest.database import Database

        with patch("sqlite3.connect", side_effect=_fts5_blocking_connect):
            db = Database(temp_db)

        # These must not raise
        db.drop_fts_triggers()
        db.rebuild_fts_indexes()
        db.close()


class TestPipelineFTSManagement:
    """Verify PipeRunner manages FTS triggers around bulk ingest."""

    def _make_runner(self, get_db, full_mode=False):
        """Create a minimal PipeRunner with a noop adapter."""
        from unittest.mock import MagicMock

        from footprinter.ingest.pipe_runner import PipeRunner

        processing = MagicMock()
        processing.is_processing_pipe.return_value = False

        # Noop adapter that returns a result
        class NoopAdapter:
            def run(self, db, config):
                result = MagicMock()
                result.elapsed_seconds = 0.1
                result.to_dict.return_value = {"stage": "test_stage", "status": "completed"}
                return result

        runner = PipeRunner(
            processing=processing,
            get_db=get_db,
            config={},
            config_path="/dev/null",
            adapter_registry={"test_stage": NoopAdapter},
            pipelines={"test": ["test_stage"]},
            all_pipes=["test_stage"],
        )
        runner.full_mode = full_mode
        return runner

    def test_pipeline_drops_triggers_in_full_mode(self, temp_db):
        """In full mode, triggers are dropped before stages and rebuilt after."""
        from footprinter.ingest.database import Database
        from footprinter.services.ingest_service import IngestService

        db = Database(temp_db)
        drop_calls = []
        rebuild_calls = []
        original_drop = db.drop_fts_triggers
        original_rebuild = db.rebuild_fts_indexes

        def mock_drop():
            drop_calls.append("drop")
            return original_drop()

        def mock_rebuild():
            rebuild_calls.append("rebuild")
            return original_rebuild()

        db.drop_fts_triggers = mock_drop
        db.rebuild_fts_indexes = mock_rebuild

        runner = self._make_runner(get_db=lambda: db, full_mode=True)
        svc = IngestService(db.conn, get_db=lambda: db)
        svc.run_pipes(["test_stage"], runner=runner, full_mode=True)

        assert len(drop_calls) == 1, "drop_fts_triggers not called in full mode"
        assert len(rebuild_calls) == 1, "rebuild_fts_indexes not called in full mode"
        db.close()

    def test_pipeline_skips_trigger_management_incremental(self, temp_db):
        """In incremental mode, FTS triggers are left alone."""
        from footprinter.ingest.database import Database
        from footprinter.services.ingest_service import IngestService

        db = Database(temp_db)
        drop_calls = []
        rebuild_calls = []

        db.drop_fts_triggers = lambda: drop_calls.append("drop")
        db.rebuild_fts_indexes = lambda: rebuild_calls.append("rebuild")

        runner = self._make_runner(get_db=lambda: db, full_mode=False)
        svc = IngestService(db.conn, get_db=lambda: db)
        svc.run_pipes(["test_stage"], runner=runner, full_mode=False)

        assert len(drop_calls) == 0, "drop_fts_triggers called in incremental mode"
        assert len(rebuild_calls) == 0, "rebuild_fts_indexes called in incremental mode"
        db.close()

    def test_pipeline_rebuilds_fts_on_stage_failure(self, temp_db):
        """FTS is rebuilt even when run_pipe raises an uncaught exception."""
        from footprinter.ingest.database import Database
        from footprinter.services.ingest_service import IngestService

        db = Database(temp_db)
        rebuild_calls = []
        original_rebuild = db.rebuild_fts_indexes

        def mock_rebuild():
            rebuild_calls.append("rebuild")
            return original_rebuild()

        db.rebuild_fts_indexes = mock_rebuild

        runner = self._make_runner(get_db=lambda: db, full_mode=True)

        # Patch run_pipe to raise — bypasses the internal exception handling
        # so the exception actually escapes into run_pipes's try/finally.
        def exploding_run_stage(stage, **kwargs):
            raise RuntimeError("stage exploded")

        runner.run_pipe = exploding_run_stage

        svc = IngestService(db.conn, get_db=lambda: db)
        with pytest.raises(RuntimeError, match="stage exploded"):
            svc.run_pipes(["test_stage"], runner=runner, full_mode=True)

        assert len(rebuild_calls) == 1, "rebuild_fts_indexes not called after exception"
        db.close()


class TestAppSchemaExcluded:
    """Verify app-scope tables (folder_mappings) are NOT created by init_db()."""

    def test_folder_mappings_absent(self, temp_db):
        """folder_mappings must not exist after init_db() — it's app-scope."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        assert "folder_mappings" not in tables, "folder_mappings should not be created by init_db()"
        db.close()


class TestRetentionSchemaExcluded:
    """Verify retention tables and columns are NOT created by default init_db()."""

    def test_retention_tables_absent(self, temp_db):
        """classifications and classification_audit_log must not exist after init_db()."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        assert "classifications" not in tables, "classifications should not be created by init_db()"
        assert "classification_audit_log" not in tables, "classification_audit_log should not be created by init_db()"
        db.close()

    def test_projects_no_retention_columns(self, temp_db):
        """closed_at, retention_review_at, retention_policy, purged_at must not exist on projects."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(projects)")
        columns = {row[1] for row in cursor.fetchall()}
        retention_cols = {"closed_at", "retention_review_at", "retention_policy", "purged_at"}
        present = retention_cols & columns
        assert not present, f"Retention columns should not exist on projects: {present}"
        db.close()

    def test_folders_no_classification_after_init_db(self, temp_db):
        """classification must not exist on folders after init_db() only."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(folders)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "classification" not in columns, "classification should not be in folders after init_db()"
        db.close()


class TestAppSchemaExtraction:
    """Verify app schema extraction to standalone module."""

    def test_schemamixin_no_longer_has_init_app_schema(self):
        """SchemaMixin should not have init_app_schema after extraction."""
        from footprinter.ingest.db.schema import SchemaMixin

        assert not hasattr(SchemaMixin, "init_app_schema"), "init_app_schema should be removed from SchemaMixin"

    def test_database_no_longer_has_init_retention_schema(self):
        """Database should not have init_retention_schema after extraction."""
        from footprinter.ingest.database import Database

        assert not hasattr(Database, "init_retention_schema"), "init_retention_schema should be removed from Database"


class _NoFTS5Cursor:
    """Cursor proxy that rejects CREATE VIRTUAL TABLE ... USING fts5."""

    def __init__(self, real_cursor):
        object.__setattr__(self, "_real", real_cursor)

    def execute(self, sql, *args):
        if isinstance(sql, str) and "using fts5" in sql.lower():
            raise sqlite3.OperationalError("no such module: fts5")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _NoFTS5Connection:
    """Connection proxy whose cursors block FTS5 DDL."""

    def __init__(self, real_conn):
        object.__setattr__(self, "_real", real_conn)

    def cursor(self):
        return _NoFTS5Cursor(self._real.cursor())

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)


def _fts5_blocking_connect(*args, **kwargs):
    """sqlite3.connect replacement that blocks FTS5."""
    real_conn = _real_connect(*args, **kwargs)
    return _NoFTS5Connection(real_conn)


_real_connect = sqlite3.connect


class TestFTS5Unavailable:
    """Verify init_db() degrades gracefully when FTS5 is not compiled in."""

    def test_init_db_completes_without_fts5(self, temp_db):
        """init_db() must not crash when FTS5 is unavailable."""
        from footprinter.ingest.database import Database

        with patch("sqlite3.connect", side_effect=_fts5_blocking_connect):
            db = Database(temp_db)

        # Core tables must exist (query through the real connection)
        real_conn = sqlite3.connect(temp_db)
        cursor = real_conn.cursor()
        for table in ("files", "emails", "chats"):
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            assert cursor.fetchone() is not None, f"{table} table not created"

        # FTS5 virtual tables must NOT exist
        for fts_table in ("files_fts", "emails_fts", "chats_fts"):
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (fts_table,),
            )
            assert cursor.fetchone() is None, f"{fts_table} should not exist without FTS5"

        real_conn.close()
        db.close()

    def test_fts5_backfill_skipped_without_tables(self, temp_db):
        """Backfill queries must not crash when FTS tables are absent."""
        from footprinter.ingest.database import Database

        with patch("sqlite3.connect", side_effect=_fts5_blocking_connect):
            # Should complete without raising — backfill is guarded
            db = Database(temp_db)

        # Verify we can still insert into core tables (no leftover errors)
        real_conn = sqlite3.connect(temp_db)
        real_conn.execute("INSERT INTO files (source, name, path) VALUES ('local', 'test.txt', '/tmp/test.txt')")
        real_conn.commit()
        cursor = real_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files")
        assert cursor.fetchone()[0] == 1
        real_conn.close()
        db.close()


class TestColumnRenames:
    """Verify column renames: old names gone, new names present."""

    def _get_columns(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def _get_tables(self, db):
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name")
        return {row[0] for row in cursor.fetchall()}

    def test_messages_chat_id_column(self, temp_db):
        """messages table has chat_id, not old column name."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "messages")
        assert "chat_id" in columns, "chat_id missing from messages"
        assert "conversation_id" not in columns, "conversation_id still in messages"
        db.close()

    def test_chats_external_id_column(self, temp_db):
        """chats table has external_id, not old column name."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "chats")
        assert "external_id" in columns, "external_id missing from chats"
        assert "conversation_id" not in columns, "conversation_id still in chats"
        db.close()

    def test_projects_name_column(self, temp_db):
        """projects uses `name` (standardized), not the old `project_name`."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "projects")
        assert "name" in columns, "name missing from projects"
        assert "project_name" not in columns, "project_name still in projects"
        db.close()

    def test_projects_has_slug_and_status_changed_at(self, temp_db):
        """projects gains slug + status_changed_at for super-entity parity."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "projects")
        for col in ("slug", "status_changed_at"):
            assert col in columns, f"{col} missing from projects"
        db.close()

    def test_projects_no_code_columns(self, temp_db):
        """Code-project fields are stripped from the package schema (moved to app-scope)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "projects")
        code_cols = {"root_path", "project_type", "github_url", "root_folder_id"}
        present = code_cols & columns
        assert not present, f"Code-project columns should not exist on projects: {present}"
        db.close()

    def test_projects_metadata_absent(self, temp_db):
        """metadata is dropped from projects (never populated, speculative)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "projects")
        assert "metadata" not in columns, "metadata should not exist on projects"
        db.close()

    def test_clients_pathpattern_metadata_absent(self, temp_db):
        """clients drops path_pattern + metadata and gains updated_at + status_changed_at."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "clients")
        for col in ("path_pattern", "metadata"):
            assert col not in columns, f"{col} should not exist on clients"
        for col in ("updated_at", "status_changed_at"):
            assert col in columns, f"{col} missing from clients"
        db.close()


class TestFTSDefinitions:
    """Verify FTS definitions are a single source of truth for all FTS tables."""

    def test_fts_definitions_single_source_of_truth(self):
        """_FTS_DEFINITIONS contains all 3 FTS tables with expected structure and columns."""
        from footprinter.ingest.db.schema import _FTS_DEFINITIONS

        assert set(_FTS_DEFINITIONS.keys()) == {"files_fts", "emails_fts", "chats_fts"}

        for fts_table, defn in _FTS_DEFINITIONS.items():
            assert "base_table" in defn, f"{fts_table} missing 'base_table'"
            assert "columns" in defn, f"{fts_table} missing 'columns'"
            assert isinstance(defn["columns"], list), f"{fts_table} columns not a list"
            assert len(defn["columns"]) > 0, f"{fts_table} has empty columns"

        # Verify exact column lists
        assert _FTS_DEFINITIONS["files_fts"]["columns"] == ["name", "content_preview"]
        assert _FTS_DEFINITIONS["files_fts"]["base_table"] == "files"
        assert _FTS_DEFINITIONS["emails_fts"]["columns"] == ["subject", "from_name", "from_address", "body_preview"]
        assert _FTS_DEFINITIONS["emails_fts"]["base_table"] == "emails"
        assert _FTS_DEFINITIONS["chats_fts"]["columns"] == ["title"]
        assert _FTS_DEFINITIONS["chats_fts"]["base_table"] == "chats"

    def test_fts_trigger_names_derived_from_definitions(self):
        """_FTS_TRIGGER_NAMES has exactly 3 entries per FTS table (ai, ad, au)."""
        from footprinter.ingest.db.schema import _FTS_DEFINITIONS, SchemaMixin

        trigger_names = SchemaMixin._FTS_TRIGGER_NAMES
        assert len(trigger_names) == len(_FTS_DEFINITIONS) * 3

        for fts_table in _FTS_DEFINITIONS:
            for suffix in ("ai", "ad", "au"):
                expected = f"{fts_table}_{suffix}"
                assert expected in trigger_names, f"Missing trigger name: {expected}"

    def test_fts_table_map_derived_from_definitions(self):
        """_FTS_TABLE_MAP keys and values match _FTS_DEFINITIONS."""
        from footprinter.ingest.db.schema import _FTS_DEFINITIONS, SchemaMixin

        table_map = SchemaMixin._FTS_TABLE_MAP
        assert set(table_map.keys()) == set(_FTS_DEFINITIONS.keys())

        for fts_table, base_table in table_map.items():
            assert base_table == _FTS_DEFINITIONS[fts_table]["base_table"], (
                f"{fts_table}: map says {base_table}, definitions say {_FTS_DEFINITIONS[fts_table]['base_table']}"
            )


class TestNoMigrationScaffolding:
    """Governance: init_db() and init_app_schema() must contain no migration scaffolding.

    All columns belong in their parent CREATE TABLE. ALTER TABLE ADD COLUMN,
    DROP INDEX IF EXISTS, and RENAME statements are migration artifacts that
    should not exist in clean DDL.
    """

    def _get_source(self, method_name: str) -> str:
        """Return the source code of a SchemaMixin method."""
        import inspect

        from footprinter.ingest.db.schema import SchemaMixin

        return inspect.getsource(getattr(SchemaMixin, method_name))

    def test_no_alter_table_in_init_db(self):
        """init_db() must not contain ALTER TABLE (app-scope uses ALTER TABLE correctly)."""
        source = self._get_source("init_db")
        assert "ALTER TABLE" not in source, (
            "init_db() still contains ALTER TABLE — fold columns into CREATE TABLE instead"
        )

    def test_no_drop_index_in_init_db(self):
        """init_db() must not contain DROP INDEX IF EXISTS."""
        source = self._get_source("init_db")
        assert "DROP INDEX IF EXISTS" not in source, (
            "init_db() still contains DROP INDEX IF EXISTS — remove migration scaffolding"
        )


class TestIngestsDDLConstant:
    """Verify ingests DDL is defined once via a module-level constant."""

    def test_ingests_ddl_constant_exists(self):
        """_INGESTS_DDL should be a non-empty string containing CREATE TABLE."""
        from footprinter.ingest.db.schema import _INGESTS_DDL

        assert isinstance(_INGESTS_DDL, str)
        assert len(_INGESTS_DDL) > 0
        assert "CREATE TABLE" in _INGESTS_DDL
        assert "ingests" in _INGESTS_DDL

    def test_ingests_ddl_not_duplicated_in_source(self):
        """The ingests CREATE TABLE SQL should appear only once in schema.py source."""
        import inspect
        import re

        import footprinter.ingest.db.schema as schema_module

        source = inspect.getsource(schema_module)
        # Count occurrences of inline CREATE TABLE ... ingests DDL.
        # The constant definition is one occurrence; there should be no others.
        pattern = r"CREATE TABLE IF NOT EXISTS ingests\s*\("
        matches = re.findall(pattern, source)
        assert len(matches) == 1, f"Expected exactly 1 CREATE TABLE definition for ingests, found {len(matches)}"


# ========================================
# CHECK constraint tests
# ========================================

# Minimal INSERT SQL for each entity table, satisfying NOT NULL constraints.
# The status/access/visibility values are placeholders replaced by the tests.
_ENTITY_INSERTS = {
    "files": (
        "INSERT INTO files (source, name, status, access, visibility)"
        " VALUES ('local', 'x', {status}, {access}, {visibility})"
    ),
    "folders": (
        "INSERT INTO folders (path, relative_path, name, status, access, visibility)"
        " VALUES ('/x', 'x', 'x', {status}, {access}, {visibility})"
    ),
    "visits": (
        "INSERT INTO visits (url, visit_time, browser, status, access, visibility)"
        " VALUES ('http://x', '2025-01-01', 'chrome', {status}, {access}, {visibility})"
    ),
    "projects": (
        "INSERT INTO projects (name, status, access, visibility)"
        " VALUES ('x', {status}, {access}, {visibility})"
    ),
    "chats": (
        "INSERT INTO chats (external_id, account, status, access, visibility)"
        " VALUES ('x', 'a', {status}, {access}, {visibility})"
    ),
    "messages": (
        "INSERT INTO messages (chat_id, role, status, access, visibility)"
        " VALUES (0, 'user', {status}, {access}, {visibility})"
    ),
    "emails": (
        "INSERT INTO emails (message_id, thread_id, account, received_at,"
        " status, access, visibility)"
        " VALUES ('x', 't', 'a', '2025-01-01',"
        " {status}, {access}, {visibility})"
    ),
    "clients": (
        "INSERT INTO clients (name, slug, client_type, status, access, visibility)"
        " VALUES ('x', 'x', 'org', {status}, {access}, {visibility})"
    ),
}

_ENTITY_TABLES = list(_ENTITY_INSERTS.keys())


class TestCheckConstraints:
    """Verify CHECK constraints on status, access, visibility columns."""

    def _get_fresh_db(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        # Disable FK checks so messages INSERT doesn't need a real chat_id
        db.conn.execute("PRAGMA foreign_keys=OFF")
        return db

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    def test_invalid_status_rejected(self, temp_db, table):
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status="'invalid'",
            access="'inherit'",
            visibility="'inherit'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    def test_invalid_access_rejected(self, temp_db, table):
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status="'listed'",
            access="'bogus'",
            visibility="'inherit'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    def test_invalid_visibility_rejected(self, temp_db, table):
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status="'listed'",
            access="'inherit'",
            visibility="'bogus'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    @pytest.mark.parametrize("status", ["'listed'", "'unlisted'", "'removed'"])
    def test_trichotomy_status_accepted(self, temp_db, table, status):
        """Trichotomy values must be accepted on every entity table."""
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status=status,
            access="'inherit'",
            visibility="'inherit'",
        )
        db.conn.execute(sql)  # should not raise
        db.close()

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    @pytest.mark.parametrize(
        "legacy_status",
        ["'active'", "'hidden'", "'paused'", "'completed'", "'abandoned'", "'archived'"],
    )
    def test_legacy_status_rejected(self, temp_db, table, legacy_status):
        """Legacy status values must be rejected on every entity table."""
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status=legacy_status,
            access="'inherit'",
            visibility="'inherit'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    def test_null_status_passes_check(self, temp_db):
        """NULL status should pass CHECK (SQLite evaluates NULL IN (...) as NULL → pass)."""
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS["files"].format(
            status="NULL",
            access="'inherit'",
            visibility="'inherit'",
        )
        db.conn.execute(sql)  # should not raise
        db.close()


# ========================================
# display_name trigger tests
# ========================================


class TestDisplayNameTriggers:
    """Verify AFTER INSERT triggers auto-populate display_name from source columns."""

    def _get_fresh_db(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute("PRAGMA foreign_keys=OFF")
        return db

    def test_display_name_trigger_files(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute("INSERT INTO files (source, name, path) VALUES ('local', 'report.pdf', '/tmp/report.pdf')")
        row = db.conn.execute("SELECT display_name FROM files WHERE name = 'report.pdf'").fetchone()
        assert row["display_name"] == "report.pdf"
        db.close()

    def test_display_name_trigger_folders(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute("INSERT INTO folders (path, relative_path, name) VALUES ('/tmp/docs', 'docs', 'docs')")
        row = db.conn.execute("SELECT display_name FROM folders WHERE name = 'docs'").fetchone()
        assert row["display_name"] == "docs"
        db.close()

    def test_display_name_trigger_emails(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute(
            "INSERT INTO emails (message_id, thread_id, account, received_at, subject) "
            "VALUES ('m1', 't1', 'a', '2025-01-01', 'Weekly Report')"
        )
        row = db.conn.execute("SELECT display_name FROM emails WHERE message_id = 'm1'").fetchone()
        assert row["display_name"] == "Weekly Report"
        db.close()

    def test_display_name_trigger_chats(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute("INSERT INTO chats (external_id, account, title) VALUES ('c1', 'a', 'Architecture Discussion')")
        row = db.conn.execute("SELECT display_name FROM chats WHERE external_id = 'c1'").fetchone()
        assert row["display_name"] == "Architecture Discussion"
        db.close()

    def test_display_name_trigger_visits(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute(
            "INSERT INTO visits (url, visit_time, browser, title) "
            "VALUES ('http://x', '2025-01-01', 'chrome', 'GitHub - Home')"
        )
        row = db.conn.execute("SELECT display_name FROM visits WHERE url = 'http://x'").fetchone()
        assert row["display_name"] == "GitHub - Home"
        db.close()

    def test_display_name_trigger_projects(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute("INSERT INTO projects (name) VALUES ('footprinter')")
        row = db.conn.execute("SELECT display_name FROM projects WHERE name = 'footprinter'").fetchone()
        assert row["display_name"] == "footprinter"
        db.close()

    def test_display_name_trigger_clients(self, temp_db):
        db = self._get_fresh_db(temp_db)
        db.conn.execute("INSERT INTO clients (name, slug, client_type) VALUES ('Acme Corp', 'acme', 'org')")
        row = db.conn.execute("SELECT display_name FROM clients WHERE slug = 'acme'").fetchone()
        assert row["display_name"] == "Acme Corp"
        db.close()

    def test_display_name_trigger_messages(self, temp_db):
        db = self._get_fresh_db(temp_db)
        long_content = "A" * 200
        db.conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (0, 'user', ?)",
            (long_content,),
        )
        row = db.conn.execute("SELECT display_name FROM messages ORDER BY id DESC LIMIT 1").fetchone()
        assert row["display_name"] == "A" * 100
        db.close()

    def test_display_name_explicit_not_overwritten(self, temp_db):
        """Explicit display_name should not be overwritten by trigger."""
        db = self._get_fresh_db(temp_db)
        db.conn.execute(
            "INSERT INTO files (source, name, path, display_name) "
            "VALUES ('local', 'foo.txt', '/tmp/foo.txt', 'Custom Name')"
        )
        row = db.conn.execute("SELECT display_name FROM files WHERE name = 'foo.txt'").fetchone()
        assert row["display_name"] == "Custom Name"
        db.close()

    def test_display_name_null_source_stays_null(self, temp_db):
        """If the source column is NULL, display_name stays NULL (trigger sets NULL)."""
        db = self._get_fresh_db(temp_db)
        db.conn.execute("INSERT INTO visits (url, visit_time, browser) VALUES ('http://y', '2025-01-01', 'safari')")
        row = db.conn.execute("SELECT display_name FROM visits WHERE url = 'http://y'").fetchone()
        # title is NULL so display_name should be NULL
        assert row["display_name"] is None
        db.close()


# ========================================
# Schema documentation tests
# ========================================


class TestSchemaDocumentation:
    """Verify that schema.py documents the standard entity column set."""

    def test_schema_header_documents_standard_columns(self):
        """schema.py should contain a comment block listing the standard entity columns."""
        import inspect

        import footprinter.ingest.db.schema as schema_module

        source = inspect.getsource(schema_module)
        assert "Standard Entity Column Set" in source
        for keyword in ("status", "created_at", "display_name", "access", "visibility"):
            assert keyword in source, f"Schema header should document '{keyword}'"


# ========================================
# Migration separation tests
# ========================================


class TestMigrationRemoved:
    """Verify pre-1.0 migration infrastructure has been deleted."""

    def test_migration_module_not_importable(self):
        """migration.py must not exist — importing it should raise ImportError."""
        with pytest.raises(ImportError):
            import footprinter.ingest.db.migration  # noqa: F401

    def test_schema_has_no_migrate_reference(self):
        """schema.py source must not import or call migrate_schema."""
        import inspect

        from footprinter.ingest.db.schema import SchemaMixin

        source = inspect.getsource(SchemaMixin.init_db)
        assert "migrate_schema" not in source
        assert "from footprinter.ingest.db.migration" not in source


# ========================================
# Database operations — merged from test_database.py
# ========================================


class TestSourceSeeding:
    """Verify init_db seeds the sources table from config."""

    def test_sources_seeded_on_init(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sources")
        count = cursor.fetchone()[0]
        assert count >= 4, f"Expected at least 4 seeded sources, got {count}"

        cursor.execute("SELECT name FROM sources ORDER BY name")
        names = {row[0] for row in cursor.fetchall()}
        assert "local" in names
        assert "browser" in names
        assert "email" in names
        assert "chat" in names
        db.close()


class TestDatabaseOperations:
    """Basic CRUD and index enforcement on the database."""

    def test_insert_file(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO files (source, name, path, content_type, size_bytes, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("local", "file.txt", "/test/file.txt", "txt", 100, datetime.now().isoformat()),
        )
        db.conn.commit()

        cursor.execute("SELECT * FROM files WHERE path = ?", ("/test/file.txt",))
        row = cursor.fetchone()
        assert row is not None
        assert row["name"] == "file.txt"
        assert row["size_bytes"] == 100
        assert row["source"] == "local"
        db.close()

    def test_update_file_metadata(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO files (source, name, path, content_type, size_bytes, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("local", "file.txt", "/test/file.txt", "txt", 100, datetime.now().isoformat()),
        )
        db.conn.commit()

        metadata = {"analysis": {"category": "test"}}
        cursor.execute(
            "UPDATE files SET metadata = ? WHERE path = ?",
            (json.dumps(metadata), "/test/file.txt"),
        )
        db.conn.commit()

        cursor.execute("SELECT metadata FROM files WHERE path = ?", ("/test/file.txt",))
        row = cursor.fetchone()
        loaded = json.loads(row["metadata"])
        assert loaded["analysis"]["category"] == "test"
        db.close()

    def test_query_by_content_type(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        files = [
            ("local", "a.txt", "/test/a.txt", "txt", 100),
            ("local", "b.txt", "/test/b.txt", "txt", 200),
            ("local", "c.py", "/test/c.py", "py", 150),
        ]
        for source, name, path, ctype, size in files:
            cursor.execute(
                """
                INSERT INTO files (source, name, path, content_type, size_bytes, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, name, path, ctype, size, datetime.now().isoformat()),
            )
        db.conn.commit()

        cursor.execute("SELECT * FROM files WHERE content_type = ?", ("txt",))
        txt_files = cursor.fetchall()
        assert len(txt_files) == 2
        db.close()

    def test_insert_folder(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO folders (path, relative_path, name, parent_path, scanned_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "/Users/test/Documents",
                "Documents",
                "Documents",
                "/Users/test",
                datetime.now().isoformat(),
            ),
        )
        db.conn.commit()

        cursor.execute("SELECT * FROM folders WHERE path = ?", ("/Users/test/Documents",))
        row = cursor.fetchone()
        assert row is not None
        assert row["name"] == "Documents"
        db.close()

    def test_file_local_unique_index(self, temp_db):
        """Local files enforce path uniqueness."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO files (source, name, path, content_type, size_bytes, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("local", "file.txt", "/test/file.txt", "txt", 100, datetime.now().isoformat()),
        )
        db.conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO files (source, name, path, content_type, size_bytes, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("local", "file.txt", "/test/file.txt", "txt", 200, datetime.now().isoformat()),
            )
        db.close()
