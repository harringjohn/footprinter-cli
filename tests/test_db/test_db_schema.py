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
        "mcp_read",
        "mcp_view",
        "summary",
        "summarized_at",
        "display_name",
    },
    "projects": {
        "id",
        "project_name",
        "description",
        "status",
        "status_reason",
        "created_at",
        "updated_at",
        "metadata",
        "root_path",
        "project_type",
        "client_id",
        "client",
        "github_url",
        "root_folder_id",
        "mcp_read",
        "mcp_view",
        "display_name",
    },
    "clients": {
        "id",
        "name",
        "slug",
        "client_type",
        "path_pattern",
        "status",
        "status_reason",
        "created_at",
        "metadata",
        "mcp_read",
        "mcp_view",
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
        "mcp_view",
        "mcp_read",
        "status",
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
        "summary",
        "created_at",
        "modified_at",
        "message_count",
        "indexed_at",
        "updated_at",
        "metadata",
        "mcp_read",
        "mcp_view",
        "client_id",
        "project_id",
        "metadata_vectorized_at",
        "status",
        "merged_into_id",
        "display_name",
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
        "mcp_read",
        "mcp_view",
        "status",
        "display_name",
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
        "mcp_read",
        "mcp_view",
        "summary",
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
        "mcp_read",
        "mcp_view",
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

    def test_projects_status_default_active(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Insert a project without explicit status
        cursor.execute(
            "INSERT INTO projects (project_name) VALUES (?)",
            ("test-project",),
        )
        db.conn.commit()

        # Verify the row gets status='active' from the DEFAULT
        cursor.execute(
            "SELECT status FROM projects WHERE project_name = ?",
            ("test-project",),
        )
        row = cursor.fetchone()
        assert row[0] == "active", f"Expected status 'active', got {row[0]!r}"

        # Verify schema metadata declares the default
        cursor.execute("PRAGMA table_info(projects)")
        columns = {r[1]: r[4] for r in cursor.fetchall()}  # name -> dflt_value
        assert columns["status"] == "'active'", f"Expected dflt_value \"'active'\", got {columns['status']!r}"
        db.close()

    def test_projects_status_reason_default_null(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO projects (project_name) VALUES (?)",
            ("test-project-sr",),
        )
        db.conn.commit()
        cursor.execute(
            "SELECT status_reason FROM projects WHERE project_name = ?",
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
        """All 8 entity tables must have mcp_read and mcp_view columns."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        for table in self._ACCESS_CONTROL_TABLES:
            columns = self._get_columns(db, table)
            assert "mcp_read" in columns, f"{table} missing mcp_read column"
            assert "mcp_view" in columns, f"{table} missing mcp_view column"
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
            "INSERT INTO chats (external_id, account, title, summary) "
            "VALUES ('test-ext-1', 'personal', 'Test Chat', 'A test summary')"
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

    def test_stale_chat_conversations_triggers_cleaned(self, temp_db):
        """Migration must drop stale chat_conversations triggers and FTS table.

        Simulates a pre-migration DB that has the old chat_conversations_ai/ad/au
        triggers and chat_conversations_fts virtual table left over from the
        chat_conversations → chats rename.  After Database() init, those artefacts
        must be gone and INSERT into chats must succeed.
        """
        import sqlite3 as _sqlite3

        # Build a legacy DB with stale artefacts.
        # The chats table must have all columns that init_db() indexes reference,
        # because CREATE TABLE IF NOT EXISTS is a no-op on existing tables.
        conn = _sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "external_id TEXT UNIQUE NOT NULL, "
            "account TEXT NOT NULL, "
            "title TEXT, summary TEXT, "
            "created_at DATETIME, updated_at DATETIME, "
            "message_count INTEGER DEFAULT 0, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "metadata TEXT, "
            "metadata_vectorized_at DATETIME, "
            "status TEXT DEFAULT 'active', "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "client_id INTEGER, assignment_source TEXT, "
            "project_id INTEGER, "
            "merged_into_id INTEGER)"
        )
        # Create the stale FTS table pointing at a non-existent content table
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chat_conversations_fts "
            "USING fts5(title, summary, "
            "content='chat_conversations', content_rowid='id')"
        )
        # Create stale triggers that fire on INSERT into chats
        conn.execute(
            "CREATE TRIGGER chat_conversations_ai AFTER INSERT ON chats BEGIN "
            "INSERT INTO chat_conversations_fts(rowid, title, summary) "
            "VALUES (new.id, new.title, new.summary); END"
        )
        conn.execute(
            "CREATE TRIGGER chat_conversations_ad AFTER DELETE ON chats BEGIN "
            "INSERT INTO chat_conversations_fts(chat_conversations_fts, rowid, title, summary) "
            "VALUES ('delete', old.id, old.title, old.summary); END"
        )
        conn.execute(
            "CREATE TRIGGER chat_conversations_au AFTER UPDATE ON chats BEGIN "
            "INSERT INTO chat_conversations_fts(chat_conversations_fts, rowid, title, summary) "
            "VALUES ('delete', old.id, old.title, old.summary); "
            "INSERT INTO chat_conversations_fts(rowid, title, summary) "
            "VALUES (new.id, new.title, new.summary); END"
        )
        conn.commit()
        conn.close()

        # Now let Database() run its migrations
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Stale triggers must be gone
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'chat_conversations_%'")
        stale_triggers = [row[0] for row in cursor.fetchall()]
        assert stale_triggers == [], f"Stale triggers still present: {stale_triggers}"

        # Stale FTS table must be gone
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = 'chat_conversations_fts'")
        assert cursor.fetchone() is None, "chat_conversations_fts still exists"

        # Current chats_fts triggers must exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'chats_fts_%'")
        current_triggers = {row[0] for row in cursor.fetchall()}
        for name in ["chats_fts_ai", "chats_fts_ad", "chats_fts_au"]:
            assert name in current_triggers, f"Expected trigger {name} missing"

        # INSERT into chats must succeed (the original bug)
        cursor.execute(
            "INSERT INTO chats (external_id, account, title, summary) "
            "VALUES ('test-uuid', 'chatgpt', 'Test Chat', 'test summary')"
        )
        db.conn.commit()
        db.close()

    def test_files_fts_content_sync_insert(self, temp_db):
        """INSERT into files → row appears in files_fts."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview, summary) "
            "VALUES (1, 'local', 'report.pdf', 'quarterly revenue summary', 'Q3 financials')"
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
            "INSERT INTO files (id, source, name, content_preview, summary) "
            "VALUES (1, 'local', 'existing.txt', 'existing preview', 'existing summary')"
        )
        db.conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_name, from_address, body_preview, received_at) "
            "VALUES (1, 'msg-1', 'thr-1', 'work', 'Old email', "
            "'Bob', 'bob@test.com', 'old body', '2024-01-01')"
        )
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title, summary) "
            "VALUES (1, 'chat-1', 'personal', 'Old chat', 'old chat summary')"
        )
        db.conn.commit()

        # Drop triggers, insert more rows (won't appear in FTS)
        db.drop_fts_triggers()
        db.conn.execute(
            "INSERT INTO files (id, source, name, content_preview, summary) "
            "VALUES (2, 'local', 'new_file.txt', 'new preview', 'new summary')"
        )
        db.conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, "
            "from_name, from_address, body_preview, received_at) "
            "VALUES (2, 'msg-2', 'thr-2', 'work', 'New email', "
            "'Alice', 'alice@test.com', 'new body', '2024-02-01')"
        )
        db.conn.execute(
            "INSERT INTO chats (id, external_id, account, title, summary) "
            "VALUES (2, 'chat-2', 'personal', 'New chat', 'new chat summary')"
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
        assert _FTS_DEFINITIONS["files_fts"]["columns"] == ["name", "content_preview", "summary"]
        assert _FTS_DEFINITIONS["files_fts"]["base_table"] == "files"
        assert _FTS_DEFINITIONS["emails_fts"]["columns"] == ["subject", "from_name", "from_address", "body_preview"]
        assert _FTS_DEFINITIONS["emails_fts"]["base_table"] == "emails"
        assert _FTS_DEFINITIONS["chats_fts"]["columns"] == ["title", "summary"]
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


class TestBrowserVisitsMigration:
    """Verify _migrate_schema() adds missing browser_visits columns to legacy databases."""

    # Original 7-column schema, before status/client/project columns were added.
    _LEGACY_DDL = """
        CREATE TABLE browser_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            visit_time DATETIME NOT NULL,
            browser TEXT NOT NULL,
            visit_count INTEGER DEFAULT 1,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """

    def test_migrate_adds_missing_browser_columns(self, temp_db):
        """Legacy 7-column DB gains all 13 columns after init_db(); existing rows survive.

        After migration, table is renamed to 'visits'.
        """
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        # Seed a legacy table with one row
        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_DDL)
        raw.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser)"
            " VALUES ('https://example.com', 'Example', '2025-01-01T00:00:00', 'safari')"
        )
        raw.commit()
        raw.close()

        # init_db() triggers _migrate_schema() then CREATE TABLE IF NOT EXISTS (no-op)
        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(visits)")
        columns = {row[1] for row in cursor.fetchall()}

        expected = {
            "id",
            "url",
            "title",
            "visit_time",
            "browser",
            "visit_count",
            "indexed_at",
            "updated_at",
            "status",
            "mcp_read",
            "mcp_view",
            "client_id",
            "project_id",
            "created_at",
            "display_name",
        }
        assert columns == expected, (
            f"visits column mismatch after migration.\n  Missing: {expected - columns}\n  Extra:   {columns - expected}"
        )

        # Pre-existing row survives with DEFAULT status
        row = cursor.execute("SELECT status FROM visits WHERE url = 'https://example.com'").fetchone()
        assert row is not None, "pre-existing row was lost during migration"
        assert row[0] == "active", f"expected status='active', got {row[0]!r}"
        db.close()

    def test_migrate_browser_columns_idempotent(self, temp_db):
        """Running init_db() twice on a fresh DB doesn't error or change column count."""
        from footprinter.ingest.database import Database

        db1 = Database(temp_db)
        cursor = db1.conn.cursor()
        cursor.execute("PRAGMA table_info(visits)")
        count1 = len(cursor.fetchall())
        db1.close()

        db2 = Database(temp_db)
        cursor = db2.conn.cursor()
        cursor.execute("PRAGMA table_info(visits)")
        count2 = len(cursor.fetchall())
        db2.close()

        assert count1 == count2 == len(EXPECTED_COLUMNS["visits"])

    def test_migrate_browser_queries_work_on_legacy_db(self, temp_db):
        """list_visits() and get_visit() work after migrating a legacy DB."""
        import sqlite3 as _sqlite3

        from footprinter.db.browser import get_visit, list_visits
        from footprinter.ingest.database import Database

        # Seed legacy table
        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_DDL)
        raw.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser)"
            " VALUES ('https://example.com', 'Example', '2025-01-01T00:00:00', 'safari')"
        )
        raw.commit()
        raw.close()

        db = Database(temp_db)

        result = list_visits(db.conn)
        assert len(result["visits"]) == 1
        assert result["visits"][0]["url"] == "https://example.com"

        entry = get_visit(db.conn, result["visits"][0]["id"])
        assert entry is not None
        assert entry["url"] == "https://example.com"
        assert entry["status"] == "active"
        db.close()

    def test_rename_migration_on_existing_db(self, temp_db):
        """Legacy browser_visits table is renamed to visits after init_db()."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_DDL)
        raw.commit()
        raw.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('visits', 'browser_visits')")
        tables = {row[0] for row in cursor.fetchall()}
        assert "visits" in tables, "visits table should exist after migration"
        assert "browser_visits" not in tables, "browser_visits should not exist after migration"
        db.close()

    def test_rename_migration_preserves_data(self, temp_db):
        """Rows in browser_visits survive the rename to visits."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_DDL)
        raw.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser)"
            " VALUES ('https://example.com', 'Example', '2025-01-01T00:00:00', 'safari')"
        )
        raw.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser)"
            " VALUES ('https://test.com', 'Test', '2025-01-02T00:00:00', 'chrome')"
        )
        raw.commit()
        raw.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM visits")
        assert cursor.fetchone()[0] == 2
        cursor.execute("SELECT url FROM visits ORDER BY visit_time")
        urls = [row[0] for row in cursor.fetchall()]
        assert urls == ["https://example.com", "https://test.com"]
        db.close()

    def test_rename_migration_drops_old_indexes(self, temp_db):
        """Old idx_browser_* indexes are dropped; only idx_visits_* remain."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_DDL)
        raw.execute("CREATE INDEX idx_browser_time ON browser_visits(visit_time)")
        raw.commit()
        raw.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_browser%'")
        old_indexes = [row[0] for row in cursor.fetchall()]
        assert old_indexes == [], f"Old indexes should be dropped: {old_indexes}"

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_visits%'")
        new_indexes = [row[0] for row in cursor.fetchall()]
        assert len(new_indexes) >= 5, f"Expected at least 5 idx_visits_* indexes, got {len(new_indexes)}"
        db.close()


class TestRemoteFKRename:
    """Verify _migrate_schema() renames indexed_drive_id → remote_file_id
    and indexed_drive_folder_id → remote_folder_id on legacy databases."""

    def test_migrate_renames_drive_fk_columns(self, temp_db):
        """Legacy DB with indexed_drive_id/indexed_drive_folder_id gains
        remote_file_id/remote_folder_id; data survives."""
        from footprinter.ingest.database import Database

        # Create a proper DB, then simulate legacy state by adding old column names
        db = Database(temp_db)
        db.conn.execute("ALTER TABLE files ADD COLUMN indexed_drive_id INTEGER")
        db.conn.execute("ALTER TABLE folders ADD COLUMN indexed_drive_folder_id INTEGER")
        db.conn.execute(
            "INSERT INTO files (source, name, path, indexed_drive_id) VALUES ('local', 'test.txt', '/tmp/test.txt', 42)"
        )
        db.conn.execute(
            "INSERT INTO folders (path, relative_path, name, indexed_drive_folder_id)"
            " VALUES ('/Work', 'Work', 'Work', 7)"
        )
        db.conn.commit()
        db.close()

        # Re-open: init_db() → _migrate_schema() renames the columns
        db2 = Database(temp_db)
        cursor = db2.conn.cursor()

        # Verify files column renamed
        cursor.execute("PRAGMA table_info(files)")
        file_cols = {row[1] for row in cursor.fetchall()}
        assert "remote_file_id" in file_cols, "remote_file_id missing after migration"
        assert "indexed_drive_id" not in file_cols, "indexed_drive_id still present"

        # Verify folders column renamed
        cursor.execute("PRAGMA table_info(folders)")
        folder_cols = {row[1] for row in cursor.fetchall()}
        assert "remote_folder_id" in folder_cols, "remote_folder_id missing after migration"
        assert "indexed_drive_folder_id" not in folder_cols, "indexed_drive_folder_id still present"

        # Verify data survived the rename
        row = cursor.execute("SELECT remote_file_id FROM files WHERE name = 'test.txt'").fetchone()
        assert row is not None, "pre-existing file row lost during migration"
        assert row[0] == 42, f"expected remote_file_id=42, got {row[0]!r}"

        folder_row = cursor.execute("SELECT remote_folder_id FROM folders WHERE path = '/Work'").fetchone()
        assert folder_row is not None, "pre-existing folder row lost during migration"
        assert folder_row[0] == 7, f"expected remote_folder_id=7, got {folder_row[0]!r}"
        db2.close()


class TestDriveColumnRenameCorrections:
    """Verify _migrate_schema() renames legacy drive_* columns on pre-existing databases.

    Legacy databases have indexed_drive_id, indexed_drive_folder_id, direct_in_drive,
    total_in_drive, and last_drive_check. Migration must rename all five to their
    current names (remote_file_id, remote_folder_id, etc.) with data preserved.
    """

    # Legacy files DDL with indexed_drive_id (the actual old column name).
    _LEGACY_FILES_DDL = """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT,
            account TEXT,
            name TEXT NOT NULL,
            path TEXT,
            content_type TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            created_at DATETIME,
            modified_at DATETIME,
            accessed_at DATETIME,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            content_preview TEXT,
            sha256_hash TEXT,
            vectorized_at DATETIME,
            vectorized_chunks INTEGER DEFAULT 0,
            project_id INTEGER,
            client_id INTEGER,
            assignment_source TEXT,
            metadata TEXT,
            folder_id INTEGER,
            md5_hash TEXT,
            status TEXT DEFAULT 'active',
            status_reason TEXT,
            status_changed_at DATETIME,
            mcp_read TEXT DEFAULT 'inherit',
            mcp_view TEXT DEFAULT 'inherit',
            summary TEXT,
            summarized_at DATETIME,
            indexed_drive_id INTEGER
        )
    """

    # Legacy folders DDL with actual old column names.
    _LEGACY_FOLDERS_DDL = """
        CREATE TABLE folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_path TEXT,
            file_count INTEGER DEFAULT 0,
            scanned_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project_id INTEGER,
            source TEXT DEFAULT 'local',
            external_id TEXT,
            account TEXT,
            web_link TEXT,
            parent_folder_id INTEGER,
            direct_file_count INTEGER DEFAULT 0,
            total_file_count INTEGER DEFAULT 0,
            total_size_bytes INTEGER DEFAULT 0,
            stats_updated_at DATETIME,
            mcp_view TEXT DEFAULT 'inherit',
            mcp_read TEXT DEFAULT 'inherit',
            indexed_drive_folder_id INTEGER,
            direct_in_drive INTEGER DEFAULT 0,
            total_in_drive INTEGER DEFAULT 0,
            last_drive_check DATETIME
        )
    """

    def test_drive_columns_renamed_on_files(self, temp_db):
        """Legacy files.indexed_drive_id renamed to remote_file_id; data survives."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_FILES_DDL)
        raw.execute(
            "INSERT INTO files (source, name, path, indexed_drive_id) VALUES ('local', 'test.txt', '/tmp/test.txt', 42)"
        )
        raw.commit()
        raw.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(files)")
        cols = {row[1] for row in cursor.fetchall()}

        assert "remote_file_id" in cols, "remote_file_id missing after migration"
        assert "indexed_drive_id" not in cols, "indexed_drive_id still present"

        row = cursor.execute("SELECT remote_file_id FROM files WHERE name = 'test.txt'").fetchone()
        assert row is not None and row[0] == 42, f"expected 42, got {row}"
        db.close()

    def test_drive_columns_renamed_on_folders(self, temp_db):
        """Legacy folders drive_* columns renamed to remote_*; data survives."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_FOLDERS_DDL)
        raw.execute(
            "INSERT INTO folders (path, relative_path, name,"
            " indexed_drive_folder_id, direct_in_drive, total_in_drive, last_drive_check)"
            " VALUES ('/Work', 'Work', 'Work', 7, 3, 10, '2025-06-01T00:00:00')"
        )
        raw.commit()
        raw.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(folders)")
        cols = {row[1] for row in cursor.fetchall()}

        # All four should be renamed
        assert "remote_folder_id" in cols, "remote_folder_id missing"
        assert "indexed_drive_folder_id" not in cols, "indexed_drive_folder_id still present"
        assert "remote_file_count" in cols, "remote_file_count missing"
        assert "direct_in_drive" not in cols, "direct_in_drive still present"
        assert "remote_file_count_recursive" in cols, "remote_file_count_recursive missing"
        assert "total_in_drive" not in cols, "total_in_drive still present"
        assert "remote_checked_at" in cols, "remote_checked_at missing"
        assert "last_drive_check" not in cols, "last_drive_check still present"

        row = cursor.execute(
            "SELECT remote_folder_id, remote_file_count,"
            " remote_file_count_recursive, remote_checked_at"
            " FROM folders WHERE path = '/Work'"
        ).fetchone()
        assert row[0] == 7, f"expected remote_folder_id=7, got {row[0]}"
        assert row[1] == 3, f"expected remote_file_count=3, got {row[1]}"
        assert row[2] == 10, f"expected remote_file_count_recursive=10, got {row[2]}"
        assert row[3] == "2025-06-01T00:00:00", f"expected remote_checked_at timestamp, got {row[3]}"
        db.close()


class TestArtifactCountRenames:
    """Verify _migrate_schema() renames direct_artifact_count/total_artifact_count on folders.

    These renames were missing entirely from the migration code.
    """

    _LEGACY_FOLDERS_DDL = """
        CREATE TABLE folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            parent_path TEXT,
            file_count INTEGER DEFAULT 0,
            scanned_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project_id INTEGER,
            source TEXT DEFAULT 'local',
            external_id TEXT,
            account TEXT,
            web_link TEXT,
            parent_folder_id INTEGER,
            total_size_bytes INTEGER DEFAULT 0,
            stats_updated_at DATETIME,
            mcp_view TEXT DEFAULT 'inherit',
            mcp_read TEXT DEFAULT 'inherit',
            direct_artifact_count INTEGER DEFAULT 0,
            total_artifact_count INTEGER DEFAULT 0
        )
    """

    def test_artifact_count_renamed(self, temp_db):
        """Legacy folders artifact_count columns renamed to file_count; data survives."""
        import sqlite3 as _sqlite3

        from footprinter.ingest.database import Database

        raw = _sqlite3.connect(temp_db)
        raw.execute(self._LEGACY_FOLDERS_DDL)
        raw.execute(
            "INSERT INTO folders (path, relative_path, name,"
            " direct_artifact_count, total_artifact_count)"
            " VALUES ('/Work', 'Work', 'Work', 5, 20)"
        )
        raw.commit()
        raw.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(folders)")
        cols = {row[1] for row in cursor.fetchall()}

        assert "direct_file_count" in cols, "direct_file_count missing after migration"
        assert "direct_artifact_count" not in cols, "direct_artifact_count still present"
        assert "total_file_count" in cols, "total_file_count missing after migration"
        assert "total_artifact_count" not in cols, "total_artifact_count still present"

        row = cursor.execute("SELECT direct_file_count, total_file_count FROM folders WHERE path = '/Work'").fetchone()
        assert row[0] == 5, f"expected direct_file_count=5, got {row[0]}"
        assert row[1] == 20, f"expected total_file_count=20, got {row[1]}"
        db.close()


class TestOrphanTableCleanup:
    """Verify _migrate_schema() drops orphan tables from old schema.

    artifact_sync_state, file_ai_analysis, permission_defaults,
    visibility_defaults are empty with no code references.
    """

    _ORPHAN_TABLES = (
        "artifact_sync_state",
        "file_ai_analysis",
        "permission_defaults",
        "visibility_defaults",
    )

    def test_orphan_tables_dropped(self, temp_db):
        """Orphan tables are dropped after init_db() re-opens the database."""
        from footprinter.ingest.database import Database

        # First init to get a clean DB
        db = Database(temp_db)
        # Manually create orphan tables
        for table in self._ORPHAN_TABLES:
            db.conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        db.conn.commit()
        db.close()

        # Re-open — _migrate_schema() should drop them
        db2 = Database(temp_db)
        cursor = db2.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?, ?)",
            self._ORPHAN_TABLES,
        )
        remaining = {row[0] for row in cursor.fetchall()}
        assert remaining == set(), f"Orphan tables not dropped: {remaining}"
        db2.close()


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


class TestMigrateColumnAdditions:
    """Verify _migrate_schema() adds missing columns to pre-existing tables.

    Each test creates a realistic old-schema table (all columns that init_db
    indexes reference, minus the specific columns being tested) then runs
    Database(path) and asserts the migration added the missing columns.
    """

    def _get_columns(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def _get_column_defaults(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1]: row[4] for row in cursor.fetchall()}

    def test_migrate_adds_emails_status(self, temp_db):
        """Old emails table without status column gets it after migration."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE emails ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "message_id TEXT NOT NULL, "
            "thread_id TEXT NOT NULL, "
            "account TEXT NOT NULL, "
            "from_address TEXT, "
            "from_name TEXT, "
            "to_addresses TEXT, "
            "cc_addresses TEXT, "
            "subject TEXT, "
            "body_preview TEXT, "
            "received_at DATETIME NOT NULL, "
            "labels TEXT, "
            "has_attachments BOOLEAN DEFAULT 0, "
            "is_read BOOLEAN DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "metadata TEXT, "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "summary TEXT, "
            "summarized_at DATETIME, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "project_id INTEGER, "
            "UNIQUE(message_id, account))"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "emails")
        assert "status" in columns, f"emails missing status column. Columns: {columns}"

        defaults = self._get_column_defaults(db, "emails")
        assert defaults["status"] == "'active'", f"Expected status DEFAULT 'active', got {defaults['status']!r}"
        db.close()

    def test_migrate_adds_files_client_id(self, temp_db):
        """Old files table without client_id gets it via migration."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "sha256_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "files")
        assert "client_id" in columns, f"files missing client_id. Columns: {columns}"
        db.close()

    def test_migrate_adds_mcp_columns(self, temp_db):
        """Old files table without mcp_read/mcp_view gets them with correct defaults."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "sha256_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        defaults = self._get_column_defaults(db, "files")
        assert "mcp_read" in defaults, f"files missing mcp_read. Columns: {set(defaults)}"
        assert "mcp_view" in defaults, f"files missing mcp_view. Columns: {set(defaults)}"
        assert defaults["mcp_read"] == "'inherit'", f"Expected mcp_read DEFAULT 'inherit', got {defaults['mcp_read']!r}"
        assert defaults["mcp_view"] == "'inherit'", f"Expected mcp_view DEFAULT 'inherit', got {defaults['mcp_view']!r}"
        db.close()


class TestMigrateColumnRenames:
    """Verify _migrate_schema() renames old column names to current ones."""

    def _get_columns(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def test_migrate_renames_content_hash_to_sha256(self, temp_db):
        """files.content_hash is renamed to sha256_hash."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "content_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "files")
        assert "sha256_hash" in columns, "files missing sha256_hash after rename"
        assert "content_hash" not in columns, "files still has content_hash after rename"
        db.close()

    def test_migrate_renames_last_scanned_at(self, temp_db):
        """folders.last_scanned_at is renamed to scanned_at."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE folders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT NOT NULL, "
            "relative_path TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "parent_path TEXT, "
            "file_count INTEGER DEFAULT 0, "
            "last_scanned_at DATETIME, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "project_id INTEGER, "
            "source TEXT DEFAULT 'local', "
            "external_id TEXT, "
            "account TEXT, "
            "web_link TEXT, "
            "parent_folder_id INTEGER, "
            "direct_file_count INTEGER DEFAULT 0, "
            "total_file_count INTEGER DEFAULT 0, "
            "total_size_bytes INTEGER DEFAULT 0, "
            "counts_updated_at DATETIME, "
            "mcp_view TEXT DEFAULT 'inherit', "
            "mcp_read TEXT DEFAULT 'inherit')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "folders")
        assert "scanned_at" in columns, "folders missing scanned_at after rename"
        assert "last_scanned_at" not in columns, "folders still has last_scanned_at after rename"
        db.close()

    def test_migrate_renames_indexed_drive_id(self, temp_db):
        """files.indexed_drive_id is renamed to remote_file_id."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "content_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "indexed_drive_id TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        columns = self._get_columns(db, "files")
        assert "indexed_drive_id" not in columns, "files still has indexed_drive_id — rename to remote_file_id failed"
        assert "remote_file_id" in columns, "files missing remote_file_id after rename"
        db.close()


class TestMigrateDataPreservation:
    """Verify migration preserves existing row data."""

    def test_migrate_preserves_file_rows(self, temp_db):
        """Existing file rows survive migration with original values intact."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "content_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )
        conn.execute("INSERT INTO files (source, name, path, size_bytes) VALUES ('local', 'a.txt', '/tmp/a.txt', 100)")
        conn.execute("INSERT INTO files (source, name, path, size_bytes) VALUES ('local', 'b.py', '/tmp/b.py', 200)")
        conn.execute(
            "INSERT INTO files (source, name, path, size_bytes, content_hash) "
            "VALUES ('WorkDrive', 'c.pdf', '/drive/c.pdf', 300, 'abc123')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM files")
        assert cursor.fetchone()[0] == 3, "Migration lost file rows"

        # Columns: name, path, size_bytes
        cursor.execute("SELECT name, path, size_bytes FROM files ORDER BY size_bytes")
        rows = cursor.fetchall()
        assert rows[0][0] == "a.txt"
        assert rows[0][1] == "/tmp/a.txt"
        assert rows[0][2] == 100
        assert rows[1][0] == "b.py"
        assert rows[2][0] == "c.pdf"
        assert rows[2][2] == 300
        db.close()

    def test_migrate_preserves_email_rows(self, temp_db):
        """Existing email rows survive migration with original values."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE emails ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "message_id TEXT NOT NULL, "
            "thread_id TEXT NOT NULL, "
            "account TEXT NOT NULL, "
            "from_address TEXT, "
            "from_name TEXT, "
            "to_addresses TEXT, "
            "cc_addresses TEXT, "
            "subject TEXT, "
            "body_preview TEXT, "
            "received_at DATETIME NOT NULL, "
            "labels TEXT, "
            "has_attachments BOOLEAN DEFAULT 0, "
            "is_read BOOLEAN DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "metadata TEXT, "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "summary TEXT, "
            "summarized_at DATETIME, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "project_id INTEGER, "
            "UNIQUE(message_id, account))"
        )
        conn.execute(
            "INSERT INTO emails (message_id, thread_id, account, subject, received_at) "
            "VALUES ('msg-1', 'thread-1', 'personal', 'Hello', '2026-03-01')"
        )
        conn.execute(
            "INSERT INTO emails (message_id, thread_id, account, subject, received_at) "
            "VALUES ('msg-2', 'thread-2', 'work', 'Meeting', '2026-03-02')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM emails")
        assert cursor.fetchone()[0] == 2, "Migration lost email rows"

        # Columns: subject, status
        cursor.execute("SELECT subject, status FROM emails ORDER BY received_at")
        rows = cursor.fetchall()
        assert rows[0][0] == "Hello"
        assert rows[0][1] == "active"  # default applied by migration
        assert rows[1][0] == "Meeting"
        db.close()

    def test_migrate_moves_md5_from_content_hash(self, temp_db):
        """Drive files with MD5 in content_hash get it moved to md5_hash."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "content_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "mcp_read TEXT DEFAULT 'inherit', "
            "mcp_view TEXT DEFAULT 'inherit', "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )
        # Drive file with MD5 stored in content_hash
        conn.execute(
            "INSERT INTO files (source, name, content_hash) "
            "VALUES ('WorkDrive', 'drive_file.pdf', 'd41d8cd98f00b204e9800998ecf8427e')"
        )
        # Local file with SHA256 in content_hash — should NOT be moved
        conn.execute(
            "INSERT INTO files (source, name, content_hash) "
            "VALUES ('local', 'local_file.txt', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Drive file: md5_hash populated, sha256_hash cleared
        # Columns: md5_hash, sha256_hash
        cursor.execute("SELECT md5_hash, sha256_hash FROM files WHERE source = 'WorkDrive'")
        drive_row = cursor.fetchone()
        assert drive_row[0] == "d41d8cd98f00b204e9800998ecf8427e", f"Drive file md5_hash not populated: {drive_row[0]}"
        assert drive_row[1] is None, f"Drive file sha256_hash should be NULL, got {drive_row[1]}"

        # Local file: content_hash renamed to sha256_hash, value preserved
        # Columns: md5_hash, sha256_hash
        cursor.execute("SELECT md5_hash, sha256_hash FROM files WHERE source = 'local'")
        local_row = cursor.fetchone()
        assert local_row[1] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", (
            f"Local file sha256_hash should preserve original content_hash, got {local_row[1]!r}"
        )
        assert local_row[0] is None, f"Local file md5_hash should be NULL, got {local_row[0]}"
        db.close()


class TestMigrateTableRenames:
    """Verify _migrate_schema() renames tables and preserves their data."""

    def test_migrate_renames_browser_visits_to_visits(self, temp_db):
        """browser_visits table is renamed to visits."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE browser_visits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL, "
            "title TEXT, "
            "visit_time DATETIME NOT NULL, "
            "browser TEXT NOT NULL, "
            "visit_count INTEGER DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='visits'")
        assert cursor.fetchone() is not None, "visits table not found after rename"

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='browser_visits'")
        assert cursor.fetchone() is None, "browser_visits still exists after rename"
        db.close()

    def test_migrate_browser_visits_data_preserved(self, temp_db):
        """Rows in browser_visits appear in visits after migration."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE browser_visits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL, "
            "title TEXT, "
            "visit_time DATETIME NOT NULL, "
            "browser TEXT NOT NULL, "
            "visit_count INTEGER DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser) "
            "VALUES ('https://example.com', 'Example', '2026-03-15 10:00:00', 'Safari')"
        )
        conn.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser) "
            "VALUES ('https://docs.python.org', 'Python Docs', '2026-03-15 11:00:00', 'Chrome')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM visits")
        assert cursor.fetchone()[0] == 2, "Migration lost browser_visits rows"

        # Columns: url, browser
        cursor.execute("SELECT url, browser FROM visits ORDER BY visit_time")
        rows = cursor.fetchall()
        assert rows[0][0] == "https://example.com"
        assert rows[0][1] == "Safari"
        assert rows[1][0] == "https://docs.python.org"
        assert rows[1][1] == "Chrome"
        db.close()

    def test_migrate_resolves_both_browser_visits_and_visits(self, temp_db):
        """Legacy DBs where both browser_visits and visits exist resolve to a single
        visits table after init.

        Pre-fix: ``ALTER TABLE browser_visits RENAME TO visits`` raises
        OperationalError("table visits already exists"), the bare ``except``
        swallows it, and ``browser_visits`` survives — re-firing the
        ``chats_fts`` drop guard on every subsequent init.
        """
        from footprinter.ingest.database import Database

        # First init: produces the canonical visits table (and full schema).
        db = Database(temp_db)
        db.close()

        # Re-introduce a stale browser_visits alongside the canonical visits to
        # simulate the buggy state seen in production DBs that survived the
        # silent-rename failure.
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE browser_visits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL, "
            "title TEXT, "
            "visit_time DATETIME NOT NULL, "
            "browser TEXT NOT NULL, "
            "visit_count INTEGER DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO browser_visits (url, title, visit_time, browser) "
            "VALUES ('https://legacy.example.com', 'Legacy', '2026-03-15 10:00:00', 'Safari')"
        )
        conn.commit()
        conn.close()

        # Second init: migration must drop the legacy table now that visits exists.
        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='browser_visits'")
        assert cursor.fetchone() is None, "browser_visits should be dropped when canonical visits already exists"

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='visits'")
        assert cursor.fetchone() is not None, "canonical visits table should remain"
        db.close()

    def test_migrate_preserves_browser_visits_rows_in_dual_state(self, temp_db):
        """Legacy rows in browser_visits must survive into visits when both tables exist,
        even when the two tables have OVERLAPPING low ids.

        Production scenario: an earlier partial init created an empty ``visits`` table;
        the rename then failed silently and the user's pre-failure visit history sits
        in ``browser_visits`` with ids 1..N, while ``visits`` accumulated post-failure
        rows that also start from id=1.  Carrying ids across the merge would make
        INSERT OR IGNORE silently drop legacy rows on PRIMARY KEY collision (the very
        thing the merge is meant to prevent).  The fix excludes ``id`` from the
        intersection, lets ``visits`` assign fresh AUTOINCREMENT ids, and uses
        ``idx_visits_unique`` on ``(url, visit_time, browser)`` as the natural conflict
        arbiter.
        """
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.close()

        conn = sqlite3.connect(temp_db)
        # Legacy table: three rows with low ids matching what would exist in production.
        conn.execute(
            "CREATE TABLE browser_visits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL, "
            "title TEXT, "
            "visit_time DATETIME NOT NULL, "
            "browser TEXT NOT NULL, "
            "visit_count INTEGER DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO browser_visits (id, url, title, visit_time, browser) VALUES "
            "(1, 'https://legacy-1.example.com', 'Legacy One',   '2026-01-10 10:00:00', 'Safari'), "
            "(2, 'https://legacy-2.example.com', 'Legacy Two',   '2026-01-10 11:00:00', 'Safari'), "
            "(3, 'https://shared.example.com',   'Shared',       '2026-02-01 09:00:00', 'Chrome')"
        )
        # Canonical visits has post-failure rows starting from id=1 — directly
        # overlapping with the legacy ids above.  Includes one (url, visit_time,
        # browser) tuple that ALSO appears in browser_visits so we can verify
        # dedup via the unique index rather than via PK collision.
        conn.execute(
            "INSERT INTO visits (id, url, title, visit_time, browser) VALUES "
            "(1, 'https://canonical-1.example.com', 'Canonical One', '2026-04-01 09:00:00', 'Firefox'), "
            "(2, 'https://shared.example.com',     'Canonical Shared (wins)', '2026-02-01 09:00:00', 'Chrome')"
        )
        conn.commit()
        conn.close()

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT url, title FROM visits ORDER BY url")
        rows = {row[0]: row[1] for row in cursor.fetchall()}

        # Both legacy-only rows must survive even though their original ids
        # collided with canonical rows — the merge dropped the id column and
        # AUTOINCREMENT assigned fresh ones.
        assert "https://legacy-1.example.com" in rows, "Legacy row at id=1 must survive (no silent PK collision)"
        assert "https://legacy-2.example.com" in rows, "Legacy row at id=2 must survive (no silent PK collision)"
        assert "https://canonical-1.example.com" in rows, "Canonical row must remain"

        # The shared (url, visit_time, browser) tuple is deduped by the
        # UNIQUE INDEX — canonical title wins because INSERT OR IGNORE
        # skips the legacy duplicate.
        assert rows["https://shared.example.com"] == "Canonical Shared (wins)"

        # Total: 3 legacy + 2 canonical, minus 1 (url, visit_time, browser) duplicate = 4.
        cursor.execute("SELECT COUNT(*) FROM visits")
        assert cursor.fetchone()[0] == 4, (
            "Expected 4 distinct visits after merge (5 inputs minus 1 (url,time,browser) dupe)"
        )

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='browser_visits'")
        assert cursor.fetchone() is None, "browser_visits should be dropped after merge"
        db.close()


class TestChatsFtsRecreateBackfill:
    """Regression tests for FPR-1638.

    When the migration drops ``chats_fts`` (because legacy ``browser_visits``
    is present), init must recreate AND repopulate the FTS inverted index.
    The pre-fix gate ``SELECT COUNT(*) FROM chats_fts == 0`` is unreliable
    for FTS5 external-content tables because ``COUNT(*)`` is delegated to
    the content (``chats``) table.  Backfill therefore never ran, and the
    ``chats_fts_au`` trigger DELETE on the empty index raised
    ``sqlite3.DatabaseError: database disk image is malformed``.
    """

    @staticmethod
    def _stage_legacy_browser_visits(temp_db: str) -> None:
        """Insert a stale browser_visits table after a healthy first init.

        Triggers the migration's drop-chats_fts guard on the next ``Database()``
        open, simulating the production state described in FPR-1638.
        """
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "CREATE TABLE browser_visits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL, "
            "title TEXT, "
            "visit_time DATETIME NOT NULL, "
            "browser TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

    def test_chats_fts_repopulated_after_migration_drop(self, temp_db):
        """After migration drops chats_fts, init backfills the inverted index."""
        from footprinter.ingest.database import Database

        # First init: full schema; insert a chat row (trigger populates FTS).
        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO chats (external_id, account, title, summary, message_count) "
            "VALUES ('chat-canary', 'claude', 'Canary Chat', 'A unique canaryword summary', 1)"
        )
        db.conn.commit()
        db.close()

        # Stage the legacy table so migration drops chats_fts on next init.
        self._stage_legacy_browser_visits(temp_db)

        # Second init: migration drops chats_fts, schema recreates it empty,
        # backfill must repopulate from chats.
        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT rowid FROM chats_fts WHERE chats_fts MATCH 'canaryword'")
        rows = cursor.fetchall()
        assert len(rows) == 1, (
            f"Expected chats_fts to contain the seeded chat after recreate+backfill; got {rows}"
        )
        db.close()

    def test_update_chat_summary_after_recreate_does_not_raise(self, temp_db):
        """UPDATE on chats.summary post-init must not raise malformed-image error."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO chats (external_id, account, title, summary, message_count) "
            "VALUES ('chat-update', 'claude', 'Update Chat', 'Original summary', 1)"
        )
        db.conn.commit()
        db.close()

        self._stage_legacy_browser_visits(temp_db)

        db = Database(temp_db)
        # Pre-fix: this UPDATE fires the chats_fts_au trigger which DELETEs
        # from the empty FTS index → "database disk image is malformed".
        db.conn.execute(
            "UPDATE chats SET summary = 'updated freshword summary' WHERE external_id = 'chat-update'"
        )
        db.conn.commit()

        cursor = db.conn.cursor()
        cursor.execute("SELECT rowid FROM chats_fts WHERE chats_fts MATCH 'freshword'")
        assert cursor.fetchone() is not None, "Updated summary should be searchable in FTS"
        db.close()

    def test_mcp_view_filtering_preserved_in_recreate_backfill(self, temp_db):
        """Opaque/hidden chat summaries must NOT appear in FTS after recreate.

        Locks in that we use ``_fts_backfill_sql`` (which NULLs content for
        opaque/hidden rows) rather than FTS5 ``rebuild`` (which would leak
        content of opaque chats into the index).
        """
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO chats (external_id, account, title, summary, message_count, mcp_view) "
            "VALUES ('chat-vis', 'claude', 'Visible Chat', 'Public summary visibleword', 1, 'visible')"
        )
        db.conn.execute(
            "INSERT INTO chats (external_id, account, title, summary, message_count, mcp_view) "
            "VALUES ('chat-opa', 'claude', 'Opaque Chat', 'Private summary opaqueword', 1, 'opaque')"
        )
        db.conn.commit()
        db.close()

        self._stage_legacy_browser_visits(temp_db)

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT rowid FROM chats_fts WHERE chats_fts MATCH 'visibleword'")
        assert cursor.fetchone() is not None, "Visible chat summary should be in FTS after recreate"

        cursor.execute("SELECT rowid FROM chats_fts WHERE chats_fts MATCH 'opaqueword'")
        assert cursor.fetchone() is None, (
            "Opaque chat summary must NOT be in FTS — backfill must apply mcp_view filtering"
        )
        db.close()

    def test_backfill_idempotent_on_second_init(self, temp_db):
        """Re-opening a healthy DB must NOT re-run the FTS backfill.

        The init-time backfill fires when EITHER the FTS table is freshly
        created OR its inverted index is empty (per the spec at the top of
        the FTS5 Backfill block in schema.py).  On a healthy reopen neither
        gate triggers — the table exists from the first init AND its index
        is non-empty — so the row count in the FTS5 shadow ``_data`` table
        must match across opens.  Verified via ``_data`` (not the FTS view
        itself) because that count is not delegated to the content table.
        """
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO chats (external_id, account, title, summary, message_count) "
            "VALUES ('chat-idemp', 'claude', 'Idempotency Chat', 'Idempotency summary', 1)"
        )
        db.conn.commit()
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats_fts_data")
        first_count = cursor.fetchone()[0]
        db.close()

        # Re-open without staging legacy artefacts: migration's chats_fts
        # drop guard must NOT fire (browser_visits absent), and the init
        # backfill must not re-run.
        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats_fts_data")
        second_count = cursor.fetchone()[0]
        assert second_count == first_count, (
            f"chats_fts_data row count changed across reopen: {first_count} → {second_count}; "
            "backfill is not idempotent"
        )
        db.close()

    def test_empty_but_present_fts_index_is_repaired_on_init(self, temp_db):
        """An FTS table that exists but has an empty inverted index must be backfilled.

        Covers the latent regression in the table-creation-only gate: after a manual
        repair (DELETE FROM <fts>) or a future migration that empties an FTS table
        without dropping it, the next ``Database()`` open must repopulate the index.
        Detection uses the FTS5 ``_docsize`` shadow table (one row per indexed doc,
        not delegated to the content table — unlike ``COUNT(*)`` on the FTS view).
        """
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
            "VALUES ('readme.md', '/tmp/readme.md', 'local', 'active', 'markdown', 100)"
        )
        db.conn.commit()

        # Force the empty-but-present state: drop the FTS triggers so the
        # DELETE doesn't recurse, then DELETE everything from files_fts.
        db.drop_fts_triggers()
        db.conn.execute("DELETE FROM files_fts")
        db.conn.commit()

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files_fts_docsize")
        assert cursor.fetchone()[0] == 0, "Precondition: files_fts inverted index should be empty"
        db.close()

        # Re-open: the table still exists, but the index is empty. The fix must
        # detect this via _docsize and re-run backfill.
        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files_fts_docsize")
        assert cursor.fetchone()[0] == 1, (
            "Empty-but-present files_fts must be backfilled on init "
            "(detected via _docsize, which is not delegated to the content table)"
        )
        cursor.execute("SELECT rowid FROM files_fts WHERE files_fts MATCH 'readme'")
        assert cursor.fetchone() is not None, "Repaired index must be searchable"
        db.close()


class TestMigrateDeadTableCleanup:
    """Verify _migrate_schema() drops dead tables and migrates their data."""

    def test_migrate_drops_pipeline_watermarks(self, temp_db):
        """pipeline_watermarks table is dropped after migration."""
        conn = sqlite3.connect(temp_db)
        conn.execute("CREATE TABLE pipeline_watermarks (stage TEXT PRIMARY KEY, last_completed_at DATETIME)")
        conn.execute(
            "INSERT INTO pipeline_watermarks (stage, last_completed_at) VALUES ('browser', '2026-03-15 12:00:00')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_watermarks'")
        assert cursor.fetchone() is None, "pipeline_watermarks still exists after migration"
        db.close()

    def test_migrate_watermark_data_migrated_to_ingests(self, temp_db):
        """pipeline_watermarks rows become completed ingests entries."""
        conn = sqlite3.connect(temp_db)
        conn.execute("CREATE TABLE pipeline_watermarks (stage TEXT PRIMARY KEY, last_completed_at DATETIME)")
        conn.execute(
            "INSERT INTO pipeline_watermarks (stage, last_completed_at) VALUES ('browser', '2026-03-15 12:00:00')"
        )
        conn.execute(
            "INSERT INTO pipeline_watermarks (stage, last_completed_at) VALUES ('local_files', '2026-03-15 13:00:00')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Columns: pipe, completed_at, status
        cursor.execute("SELECT pipe, completed_at, status FROM ingests ORDER BY pipe")
        rows = cursor.fetchall()
        pipes = {row[0] for row in rows}
        assert "browser" in pipes, "browser watermark not migrated to ingests"
        assert "local_files" in pipes, "local_files watermark not migrated to ingests"

        expected_timestamps = {
            "browser": "2026-03-15 12:00:00",
            "local_files": "2026-03-15 13:00:00",
        }
        for row in rows:
            if row[0] in expected_timestamps:
                assert row[2] == "completed", (
                    f"Migrated ingest for {row[0]} has status {row[2]!r}, expected 'completed'"
                )
                assert row[1] == expected_timestamps[row[0]], (
                    f"Migrated ingest for {row[0]} has completed_at "
                    f"{row[1]!r}, expected "
                    f"{expected_timestamps[row[0]]!r}"
                )
        db.close()


class TestFullOldSchemaConvergence:
    """End-to-end test: a DB with ALL old-schema patterns converges to current DDL."""

    def _get_columns(self, db, table):
        cursor = db.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def test_full_old_schema_converges(self, temp_db):
        """A DB with every old-schema pattern converges to current DDL.

        Creates tables with:
        - Old column names (content_hash, last_scanned_at, counts_updated_at,
          info_vectorized_at, indexed_remote_id, indexed_remote_folder_id)
        - Missing columns (emails.status, files.client_id,
          mcp_read/mcp_view on all tables)
        - Old table names (browser_visits instead of visits)
        - Dead tables (pipeline_watermarks)

        After Database(path), all tables must match the current DDL column sets.
        """
        conn = sqlite3.connect(temp_db)

        # files — old column names, missing client_id/mcp columns
        conn.execute(
            "CREATE TABLE files ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, "
            "external_id TEXT, "
            "account TEXT, "
            "name TEXT NOT NULL, "
            "path TEXT, "
            "content_type TEXT, "
            "mime_type TEXT, "
            "size_bytes INTEGER, "
            "created_at DATETIME, "
            "modified_at DATETIME, "
            "accessed_at DATETIME, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "content_preview TEXT, "
            "content_hash TEXT, "
            "vectorized_at DATETIME, "
            "vectorized_chunks INTEGER DEFAULT 0, "
            "project_id INTEGER, "
            "metadata TEXT, "
            "folder_id INTEGER, "
            "md5_hash TEXT, "
            "indexed_remote_id TEXT, "
            "status TEXT DEFAULT 'active', "
            "status_reason TEXT, "
            "status_changed_at DATETIME, "
            "summary TEXT, "
            "summarized_at DATETIME)"
        )

        # folders — old column names
        conn.execute(
            "CREATE TABLE folders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT NOT NULL, "
            "relative_path TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "parent_path TEXT, "
            "file_count INTEGER DEFAULT 0, "
            "last_scanned_at DATETIME, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "project_id INTEGER, "
            "source TEXT DEFAULT 'local', "
            "external_id TEXT, "
            "account TEXT, "
            "web_link TEXT, "
            "parent_folder_id INTEGER, "
            "indexed_remote_folder_id TEXT, "
            "direct_file_count INTEGER DEFAULT 0, "
            "total_file_count INTEGER DEFAULT 0, "
            "total_size INTEGER DEFAULT 0, "
            "total_size_bytes INTEGER DEFAULT 0, "
            "counts_updated_at DATETIME)"
        )

        # emails — missing status column
        conn.execute(
            "CREATE TABLE emails ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "message_id TEXT NOT NULL, "
            "thread_id TEXT NOT NULL, "
            "account TEXT NOT NULL, "
            "from_address TEXT, "
            "from_name TEXT, "
            "to_addresses TEXT, "
            "cc_addresses TEXT, "
            "subject TEXT, "
            "body_preview TEXT, "
            "received_at DATETIME NOT NULL, "
            "labels TEXT, "
            "has_attachments BOOLEAN DEFAULT 0, "
            "is_read BOOLEAN DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "metadata TEXT, "
            "summary TEXT, "
            "summarized_at DATETIME, "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "project_id INTEGER, "
            "UNIQUE(message_id, account))"
        )

        # chats — old vectorization column name
        conn.execute(
            "CREATE TABLE chats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "external_id TEXT UNIQUE NOT NULL, "
            "account TEXT NOT NULL, "
            "title TEXT, "
            "summary TEXT, "
            "created_at DATETIME, "
            "updated_at DATETIME, "
            "message_count INTEGER DEFAULT 0, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "metadata TEXT, "
            "info_vectorized_at DATETIME, "
            "status TEXT DEFAULT 'active', "
            "client_id INTEGER, "
            "assignment_source TEXT, "
            "project_id INTEGER, "
            "merged_into_id INTEGER)"
        )

        # browser_visits (old table name — will be renamed to visits)
        conn.execute(
            "CREATE TABLE browser_visits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "url TEXT NOT NULL, "
            "title TEXT, "
            "visit_time DATETIME NOT NULL, "
            "browser TEXT NOT NULL, "
            "visit_count INTEGER DEFAULT 1, "
            "indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )

        # Dead tables that should be cleaned up
        conn.execute("CREATE TABLE pipeline_watermarks (stage TEXT PRIMARY KEY, last_completed_at DATETIME)")
        conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, stage TEXT, started_at DATETIME)")

        # Insert some data to verify it survives
        conn.execute("INSERT INTO files (source, name, path) VALUES ('local', 'survivor.txt', '/tmp/survivor.txt')")
        conn.execute(
            "INSERT INTO browser_visits (url, visit_time, browser) "
            "VALUES ('https://survive.me', '2026-03-15 10:00:00', 'Safari')"
        )
        conn.commit()
        conn.close()

        from footprinter.ingest.database import Database

        db = Database(temp_db)

        # Verify each table's column set matches current DDL
        for table, expected_cols in EXPECTED_COLUMNS.items():
            actual = self._get_columns(db, table)
            # Old schemas may have extra columns from renames (e.g. remote_file_id).
            # The critical check is that all current-DDL columns are present.
            missing = expected_cols - actual
            assert not missing, (
                f"{table} missing columns after migration: {missing}\n"
                f"  Expected: {sorted(expected_cols)}\n"
                f"  Actual:   {sorted(actual)}"
            )

        # Verify dead tables are gone
        cursor = db.conn.cursor()
        for dead_table in ("pipeline_watermarks", "runs", "browser_visits"):
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (dead_table,),
            )
            assert cursor.fetchone() is None, f"Dead table {dead_table} still exists after migration"

        # Verify data survived
        cursor.execute("SELECT COUNT(*) FROM files WHERE name = 'survivor.txt'")
        assert cursor.fetchone()[0] == 1, "File row lost during migration"

        cursor.execute("SELECT COUNT(*) FROM visits WHERE url = 'https://survive.me'")
        assert cursor.fetchone()[0] == 1, "Visit row lost during migration"

        # Verify key column defaults were applied by migration.
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        expected_defaults = {
            "emails": {"status": "'active'", "mcp_read": "'inherit'", "mcp_view": "'inherit'"},
            "files": {"mcp_read": "'inherit'", "mcp_view": "'inherit'"},
            "visits": {"status": "'active'", "mcp_read": "'inherit'", "mcp_view": "'inherit'"},
            "chats": {"mcp_read": "'inherit'", "mcp_view": "'inherit'"},
        }
        for table, col_defaults in expected_defaults.items():
            cursor.execute(f"PRAGMA table_info({table})")
            col_info = {row[1]: row[4] for row in cursor.fetchall()}
            for col, expected_default in col_defaults.items():
                assert col_info.get(col) == expected_default, (
                    f"{table}.{col} default should be {expected_default}, got {col_info.get(col)!r}"
                )

        db.close()


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
# The status/mcp_read/mcp_view values are placeholders replaced by the tests.
_ENTITY_INSERTS = {
    "files": (
        "INSERT INTO files (source, name, status, mcp_read, mcp_view)"
        " VALUES ('local', 'x', {status}, {mcp_read}, {mcp_view})"
    ),
    "folders": (
        "INSERT INTO folders (path, relative_path, name, status, mcp_read, mcp_view)"
        " VALUES ('/x', 'x', 'x', {status}, {mcp_read}, {mcp_view})"
    ),
    "visits": (
        "INSERT INTO visits (url, visit_time, browser, status, mcp_read, mcp_view)"
        " VALUES ('http://x', '2025-01-01', 'chrome', {status}, {mcp_read}, {mcp_view})"
    ),
    "projects": (
        "INSERT INTO projects (project_name, status, mcp_read, mcp_view)"
        " VALUES ('x', {status}, {mcp_read}, {mcp_view})"
    ),
    "chats": (
        "INSERT INTO chats (external_id, account, status, mcp_read, mcp_view)"
        " VALUES ('x', 'a', {status}, {mcp_read}, {mcp_view})"
    ),
    "messages": (
        "INSERT INTO messages (chat_id, role, status, mcp_read, mcp_view)"
        " VALUES (0, 'user', {status}, {mcp_read}, {mcp_view})"
    ),
    "emails": (
        "INSERT INTO emails (message_id, thread_id, account, received_at,"
        " status, mcp_read, mcp_view)"
        " VALUES ('x', 't', 'a', '2025-01-01',"
        " {status}, {mcp_read}, {mcp_view})"
    ),
    "clients": (
        "INSERT INTO clients (name, slug, client_type, status, mcp_read, mcp_view)"
        " VALUES ('x', 'x', 'org', {status}, {mcp_read}, {mcp_view})"
    ),
}

_ENTITY_TABLES = list(_ENTITY_INSERTS.keys())


class TestCheckConstraints:
    """Verify CHECK constraints on status, mcp_read, mcp_view columns."""

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
            mcp_read="'inherit'",
            mcp_view="'inherit'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    def test_invalid_mcp_read_rejected(self, temp_db, table):
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status="'active'",
            mcp_read="'bogus'",
            mcp_view="'inherit'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    @pytest.mark.parametrize("table", _ENTITY_TABLES)
    def test_invalid_mcp_view_rejected(self, temp_db, table):
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS[table].format(
            status="'active'",
            mcp_read="'inherit'",
            mcp_view="'bogus'",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql)
        db.close()

    @pytest.mark.parametrize("status", ["'active'", "'hidden'", "'removed'"])
    def test_valid_status_accepted(self, temp_db, status):
        """All three status values should be accepted on files table."""
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS["files"].format(
            status=status,
            mcp_read="'inherit'",
            mcp_view="'inherit'",
        )
        db.conn.execute(sql)  # should not raise
        db.close()

    def test_null_status_passes_check(self, temp_db):
        """NULL status should pass CHECK (SQLite evaluates NULL IN (...) as NULL → pass)."""
        db = self._get_fresh_db(temp_db)
        sql = _ENTITY_INSERTS["files"].format(
            status="NULL",
            mcp_read="'inherit'",
            mcp_view="'inherit'",
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
        db.conn.execute("INSERT INTO projects (project_name) VALUES ('footprinter')")
        row = db.conn.execute("SELECT display_name FROM projects WHERE project_name = 'footprinter'").fetchone()
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
        for keyword in ("status", "created_at", "display_name", "mcp_read", "mcp_view"):
            assert keyword in source, f"Schema header should document '{keyword}'"


# ========================================
# Migration separation tests
# ========================================


class TestMigrationSeparation:
    """Verify migration is in a dedicated module and only runs on existing DBs."""

    def test_migrate_schema_importable(self):
        """migrate_schema is importable from the dedicated migration module."""
        from footprinter.ingest.db.migration import migrate_schema

        assert callable(migrate_schema)

    def test_fresh_db_skips_migration(self, temp_db):
        """Fresh Database on an empty file should NOT invoke migrate_schema."""
        import os
        from unittest.mock import patch

        # Remove the temp_db file so we start truly fresh
        os.unlink(temp_db)

        with patch("footprinter.ingest.db.migration.migrate_schema") as mock_migrate:
            from footprinter.ingest.database import Database

            db = Database(temp_db)
            db.close()

        mock_migrate.assert_not_called()

    def test_existing_db_runs_migration(self, temp_db):
        """Database with pre-existing tables SHOULD invoke migrate_schema."""
        import sqlite3 as _sqlite3
        from unittest.mock import patch

        # Seed a minimal legacy table so the DB looks pre-existing.
        # The table is intentionally minimal — we only need it to exist
        # so the sqlite_master check in init_db() detects an existing DB.
        raw = _sqlite3.connect(temp_db)
        raw.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, source TEXT, name TEXT, path TEXT)")
        raw.commit()
        raw.close()

        with patch("footprinter.ingest.db.migration.migrate_schema") as mock_migrate:
            from footprinter.ingest.database import Database

            try:
                db = Database(temp_db)
                db.close()
            except Exception:
                pass  # init_db may fail on the stub table — we only check the call

        mock_migrate.assert_called_once()


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
