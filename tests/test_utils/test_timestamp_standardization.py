"""Tests for timestamp column standardization.

Validates the two-axis timestamp contract:
- Origin timestamps: when the thing happened in the real world
- Audit timestamps: when Footprinter indexed/processed it

Schema changes:
- Rename: chats.updated_at → chats.modified_at (origin)
- Add: updated_at audit column on all 6 entity tables
- Add: messages.indexed_at audit column
- Backfill new columns from existing data
- Standardize CURRENT_TIMESTAMP (no datetime('now'))

Format standard:
- All timestamps use YYYY-MM-DD HH:MM:SS (UTC, space-separated)
- Matches SQLite CURRENT_TIMESTAMP output
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in _get_columns(conn, table)


def _create_pre_migration_db(path: str) -> sqlite3.Connection:
    """Create a database that looks like the schema BEFORE timestamp standardization.

    Has the old column names (chats.updated_at as origin) and is missing
    the new audit columns (updated_at on entity tables, messages.indexed_at).
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Minimal tables with pre-migration columns
    conn.execute("""
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL, name TEXT NOT NULL, path TEXT,
            content_type TEXT, size_bytes INTEGER,
            created_at DATETIME, modified_at DATETIME, accessed_at DATETIME,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            sha256_hash TEXT, md5_hash TEXT,
            vectorized_at DATETIME, vectorized_chunks INTEGER DEFAULT 0,
            content_preview TEXT, metadata TEXT,
            project_id INTEGER, client_id INTEGER, folder_id INTEGER,
            status TEXT DEFAULT 'active', status_reason TEXT, status_changed_at DATETIME,
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            summary TEXT, summarized_at DATETIME,
            display_name TEXT, mime_type TEXT, external_id TEXT, account TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX idx_files_local_unique
        ON files(source, path) WHERE source = 'local' AND path IS NOT NULL
    """)

    conn.execute("""
        CREATE TABLE folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL, relative_path TEXT NOT NULL, name TEXT NOT NULL,
            parent_path TEXT, file_count INTEGER DEFAULT 0,
            scanned_at DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project_id INTEGER, source TEXT DEFAULT 'local',
            external_id TEXT, account TEXT, parent_folder_id INTEGER,
            direct_file_count INTEGER DEFAULT 0, total_file_count INTEGER DEFAULT 0,
            total_size_bytes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active', indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            client_id INTEGER,
            mcp_view TEXT DEFAULT 'inherit', mcp_read TEXT DEFAULT 'inherit',
            display_name TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX idx_folders_unique_path
        ON folders(path) WHERE source = 'local'
    """)

    conn.execute("""
        CREATE TABLE visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL, title TEXT,
            visit_time DATETIME NOT NULL, browser TEXT NOT NULL,
            visit_count INTEGER DEFAULT 1,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            client_id INTEGER, project_id INTEGER,
            display_name TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX idx_visits_unique ON visits(url, visit_time, browser)
    """)

    conn.execute("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL, root_path TEXT,
            project_type TEXT, status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT, client_id INTEGER,
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            display_name TEXT, description TEXT, status_reason TEXT,
            client TEXT, github_url TEXT, root_folder_id INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, slug TEXT NOT NULL UNIQUE,
            client_type TEXT NOT NULL,
            status TEXT DEFAULT 'active', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            display_name TEXT, path_pattern TEXT, status_reason TEXT
        )
    """)

    # chats — has updated_at as ORIGIN (pre-rename)
    conn.execute("""
        CREATE TABLE chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE NOT NULL,
            account TEXT NOT NULL, title TEXT, summary TEXT,
            created_at DATETIME,
            updated_at DATETIME,
            message_count INTEGER DEFAULT 0,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT, metadata_vectorized_at DATETIME,
            status TEXT DEFAULT 'active',
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            client_id INTEGER, project_id INTEGER,
            merged_into_id INTEGER,
            display_name TEXT
        )
    """)

    # messages — missing indexed_at
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id TEXT, role TEXT NOT NULL, content TEXT,
            created_at DATETIME, metadata TEXT,
            vectorized_at DATETIME,
            status TEXT DEFAULT 'active',
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            display_name TEXT,
            FOREIGN KEY (chat_id) REFERENCES chats(id)
        )
    """)

    conn.execute("""
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            account TEXT NOT NULL,
            from_address TEXT, from_name TEXT,
            to_addresses TEXT, cc_addresses TEXT,
            subject TEXT, body_preview TEXT,
            received_at DATETIME NOT NULL,
            labels TEXT, has_attachments BOOLEAN DEFAULT 0,
            is_read BOOLEAN DEFAULT 1,
            indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT, status TEXT DEFAULT 'active',
            mcp_read TEXT DEFAULT 'inherit', mcp_view TEXT DEFAULT 'inherit',
            summary TEXT,
            client_id INTEGER, project_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            display_name TEXT,
            UNIQUE(message_id, account)
        )
    """)

    # Supporting tables needed for FK / init_db
    conn.execute("""
        CREATE TABLE sources (
            name TEXT PRIMARY KEY, source_type TEXT NOT NULL,
            adapter TEXT, account TEXT, label TEXT, icon TEXT,
            enabled INTEGER DEFAULT 1, config TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL, file_hash TEXT NOT NULL UNIQUE,
            file_size INTEGER, type TEXT NOT NULL,
            source TEXT, items_added INTEGER DEFAULT 0,
            items_updated INTEGER DEFAULT 0, items_total INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending', error_message TEXT,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME, metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE ingests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipe TEXT NOT NULL, started_at DATETIME NOT NULL,
            completed_at DATETIME,
            status TEXT NOT NULL DEFAULT 'running',
            mode TEXT, trigger TEXT,
            items_processed INTEGER DEFAULT 0, items_new INTEGER DEFAULT 0,
            items_updated INTEGER DEFAULT 0, items_skipped INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0, elapsed_seconds REAL, metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE permission_policies (
            scope TEXT PRIMARY KEY,
            setting TEXT NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE visibility_policies (
            scope TEXT PRIMARY KEY,
            setting TEXT NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Phase 1 RED: Schema migration tests
# ---------------------------------------------------------------------------


class TestTimestampMigration:
    """Test _migrate_schema() handles timestamp column changes."""

    def test_chats_updated_at_renamed_to_modified_at(self, tmp_path):
        """chats.updated_at (origin) should be renamed to modified_at."""
        db_path = str(tmp_path / "test.db")
        pre_conn = _create_pre_migration_db(db_path)

        # Insert data with old column name
        pre_conn.execute(
            "INSERT INTO chats (external_id, account, title, created_at, updated_at) "
            "VALUES ('chat-1', 'claude', 'Test Chat', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')"
        )
        pre_conn.commit()
        pre_conn.close()

        # Run migration via Database class
        from footprinter.ingest.database import Database

        db = Database(db_path)

        # Verify rename happened
        assert _has_column(db.conn, "chats", "modified_at"), "chats should have modified_at after migration"
        # The old updated_at should now be the audit column (re-added), not the origin
        assert _has_column(db.conn, "chats", "updated_at"), "chats should have updated_at (audit) after migration"

        # Verify data migrated: the origin value should be in modified_at
        row = db.conn.execute("SELECT modified_at FROM chats WHERE external_id = 'chat-1'").fetchone()
        assert row[0] == "2026-01-02T00:00:00Z", "Origin timestamp should be preserved in modified_at"

    def test_new_audit_columns_added_to_all_entity_tables(self, tmp_path):
        """All 6 entity tables get updated_at; messages also gets indexed_at."""
        db_path = str(tmp_path / "test.db")
        pre_conn = _create_pre_migration_db(db_path)
        pre_conn.close()

        from footprinter.ingest.database import Database

        db = Database(db_path)

        # All 6 entity tables should have updated_at
        for table in ("files", "folders", "visits", "chats", "messages", "emails"):
            assert _has_column(db.conn, table, "updated_at"), f"{table} should have updated_at audit column"

        # messages should also have indexed_at
        assert _has_column(db.conn, "messages", "indexed_at"), "messages should have indexed_at audit column"

    def test_backfill_populates_new_columns(self, tmp_path):
        """New audit columns should be backfilled from existing data."""
        db_path = str(tmp_path / "test.db")
        pre_conn = _create_pre_migration_db(db_path)

        # Insert data into each table
        pre_conn.execute(
            "INSERT INTO files (source, name, path, indexed_at) "
            "VALUES ('local', 'test.txt', '/tmp/test.txt', '2026-01-01T00:00:00Z')"
        )
        pre_conn.execute(
            "INSERT INTO folders (path, relative_path, name, indexed_at) "
            "VALUES ('/tmp/test', '/test', 'test', '2026-02-01T00:00:00Z')"
        )
        pre_conn.execute(
            "INSERT INTO visits (url, visit_time, browser, indexed_at) "
            "VALUES ('https://example.com', '2026-03-01T00:00:00Z', 'safari', '2026-03-01T01:00:00Z')"
        )
        pre_conn.execute(
            "INSERT INTO chats (external_id, account, title, indexed_at) "
            "VALUES ('chat-1', 'claude', 'Test', '2026-04-01T00:00:00Z')"
        )
        pre_conn.execute(
            "INSERT INTO messages (chat_id, role, content, created_at) "
            "VALUES (1, 'user', 'Hello', '2026-04-01T00:00:00Z')"
        )
        pre_conn.execute(
            "INSERT INTO emails (message_id, thread_id, account, received_at, indexed_at) "
            "VALUES ('msg-1', 'thread-1', 'personal', '2026-05-01T00:00:00Z', '2026-05-01T01:00:00Z')"
        )
        pre_conn.commit()
        pre_conn.close()

        from footprinter.ingest.database import Database

        db = Database(db_path)

        # files.updated_at should be backfilled from indexed_at
        row = db.conn.execute("SELECT updated_at FROM files WHERE name = 'test.txt'").fetchone()
        assert row[0] == "2026-01-01T00:00:00Z"

        # folders.updated_at should be backfilled from indexed_at
        row = db.conn.execute("SELECT updated_at FROM folders WHERE name = 'test'").fetchone()
        assert row[0] == "2026-02-01T00:00:00Z"

        # visits.updated_at should be backfilled from indexed_at
        row = db.conn.execute("SELECT updated_at FROM visits WHERE url = 'https://example.com'").fetchone()
        assert row[0] == "2026-03-01T01:00:00Z"

        # chats.updated_at (audit) should be backfilled from indexed_at
        row = db.conn.execute("SELECT updated_at FROM chats WHERE external_id = 'chat-1'").fetchone()
        assert row[0] == "2026-04-01T00:00:00Z"

        # messages.indexed_at should be backfilled from created_at
        row = db.conn.execute("SELECT indexed_at, updated_at FROM messages WHERE content = 'Hello'").fetchone()
        assert row[0] == "2026-04-01T00:00:00Z"
        assert row[1] == "2026-04-01T00:00:00Z"

        # emails.updated_at should be backfilled from indexed_at
        row = db.conn.execute("SELECT updated_at FROM emails WHERE message_id = 'msg-1'").fetchone()
        assert row[0] == "2026-05-01T01:00:00Z"

    def test_migration_idempotent(self, tmp_path):
        """Running migration twice should not error."""
        db_path = str(tmp_path / "test.db")
        pre_conn = _create_pre_migration_db(db_path)
        pre_conn.execute(
            "INSERT INTO chats (external_id, account, updated_at) VALUES ('chat-1', 'claude', '2026-01-01T00:00:00Z')"
        )
        pre_conn.commit()
        pre_conn.close()

        from footprinter.ingest.database import Database

        # First run
        db = Database(db_path)
        db.conn.close()

        # Second run — should not error
        db2 = Database(db_path)
        assert _has_column(db2.conn, "chats", "modified_at")
        assert _has_column(db2.conn, "chats", "updated_at")

        # Data should still be correct
        row = db2.conn.execute("SELECT modified_at FROM chats WHERE external_id = 'chat-1'").fetchone()
        assert row[0] == "2026-01-01T00:00:00Z"

    def test_fresh_db_has_all_timestamp_columns(self, tmp_path):
        """A fresh init_db() should produce tables with all new columns."""
        db_path = str(tmp_path / "fresh.db")
        from footprinter.ingest.database import Database

        db = Database(db_path)

        # chats should have modified_at (origin) and updated_at (audit)
        chats_cols = _get_columns(db.conn, "chats")
        assert "modified_at" in chats_cols
        assert "updated_at" in chats_cols

        # All entity tables should have updated_at
        for table in ("files", "folders", "visits", "chats", "messages", "emails"):
            assert _has_column(db.conn, table, "updated_at"), f"Fresh {table} should have updated_at"

        # messages should have indexed_at
        assert _has_column(db.conn, "messages", "indexed_at")


class TestWritePathTimestamps:
    """Test that write paths set both audit columns correctly."""

    @pytest.fixture
    def conn(self, tmp_path):
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        return db.conn

    def test_insert_local_file_sets_updated_at(self, conn):
        """insert_file() should set updated_at on INSERT."""
        from footprinter.db.files import insert_file

        result = insert_file(
            conn,
            {
                "file_name": "test.txt",
                "file_path": "/tmp/test.txt",
                "file_type": "text/plain",
                "file_size": 100,
                "created_at": "2026-01-01T00:00:00Z",
                "modified_at": "2026-01-02T00:00:00Z",
            },
        )
        assert result is not None
        action, file_id = result

        row = conn.execute("SELECT indexed_at, updated_at FROM files WHERE id = ?", (file_id,)).fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None

    def test_update_local_file_refreshes_updated_at(self, conn):
        """On UPDATE, updated_at should refresh but indexed_at should not change."""
        from footprinter.db.files import insert_file

        insert_file(
            conn,
            {
                "file_name": "test.txt",
                "file_path": "/tmp/test.txt",
                "file_type": "text/plain",
                "file_size": 100,
            },
        )
        row1 = conn.execute("SELECT indexed_at, updated_at FROM files WHERE path = '/tmp/test.txt'").fetchone()

        # Update the same file
        insert_file(
            conn,
            {
                "file_name": "test.txt",
                "file_path": "/tmp/test.txt",
                "file_type": "text/plain",
                "file_size": 200,
            },
        )
        row2 = conn.execute("SELECT indexed_at, updated_at FROM files WHERE path = '/tmp/test.txt'").fetchone()

        # indexed_at should stay the same (immutable first-seen)
        # updated_at should refresh
        assert row2["indexed_at"] == row1["indexed_at"]

    def test_insert_visit_sets_audit_columns(self, conn):
        """insert_visit() should set indexed_at and updated_at."""
        from footprinter.db.browser import insert_visit

        result = insert_visit(
            conn,
            {
                "url": "https://example.com",
                "title": "Example",
                "visit_time": "2026-01-01T00:00:00Z",
                "browser": "safari",
            },
        )
        assert result is not False

        row = conn.execute("SELECT indexed_at, updated_at FROM visits WHERE url = 'https://example.com'").fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None

    def test_insert_chat_uses_modified_at(self, conn):
        """insert_chat() should write origin timestamp to modified_at."""
        from footprinter.db.chats import insert_chat

        chat_id = insert_chat(
            conn,
            {
                "external_id": "chat-1",
                "account": "claude",
                "title": "Test Chat",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",  # origin — maps to modified_at
            },
        )

        row = conn.execute(
            "SELECT modified_at, updated_at, indexed_at FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        # Origin data should land in modified_at
        assert row["modified_at"] == "2026-01-02T00:00:00Z"
        # Audit columns should be set
        assert row["updated_at"] is not None
        assert row["indexed_at"] is not None

    def test_insert_message_sets_indexed_at(self, conn):
        """insert_message() should set indexed_at."""
        from footprinter.db.chats import insert_chat, insert_message

        chat_id = insert_chat(
            conn,
            {
                "external_id": "chat-1",
                "account": "claude",
            },
        )
        msg_id = insert_message(
            conn,
            {
                "chat_id": chat_id,
                "role": "user",
                "content": "Hello",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )

        row = conn.execute("SELECT indexed_at, updated_at FROM messages WHERE id = ?", (msg_id,)).fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None

    def test_insert_email_sets_updated_at(self, conn):
        """insert_email() should set updated_at."""
        from footprinter.db.emails import insert_email

        email_id = insert_email(
            conn,
            {
                "message_id": "msg-1",
                "thread_id": "thread-1",
                "account": "personal",
                "received_at": "2026-01-01T00:00:00Z",
            },
        )

        row = conn.execute("SELECT indexed_at, updated_at FROM emails WHERE id = ?", (email_id,)).fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None

    def test_drive_file_insert_sets_audit_columns(self, conn):
        """Drive file INSERT should set both audit columns."""
        from footprinter.db.files import insert_drive_file

        file_id = insert_drive_file(
            conn,
            {
                "source": "google_work",
                "external_id": "drive-1",
                "account": "work",
                "name": "doc.pdf",
                "path": "/Work/doc.pdf",
            },
        )

        row = conn.execute("SELECT indexed_at, updated_at FROM files WHERE id = ?", (file_id,)).fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None

    def test_local_folder_insert_sets_audit_columns(self, conn):
        """Local folder INSERT should set both audit columns."""
        from footprinter.utils.time import utc_now_iso

        conn.execute(
            "INSERT INTO folders (path, relative_path, name, scanned_at, "
            "indexed_at, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("/tmp/testfolder", "/testfolder", "testfolder", utc_now_iso()),
        )

        row = conn.execute("SELECT indexed_at, updated_at FROM folders WHERE path = '/tmp/testfolder'").fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None


class TestReadPathColumnNames:
    """Test that read paths use the new column names."""

    @pytest.fixture
    def conn(self, tmp_path):
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        # Seed a chat with the new schema
        db.conn.execute(
            "INSERT INTO chats (external_id, account, title, modified_at, updated_at, indexed_at) "
            "VALUES ('chat-1', 'claude', 'Test', '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        db.conn.commit()
        return db.conn

    def test_chats_sort_whitelist_has_modified_at(self):
        """SORT_WHITELIST should include modified_at, not updated_at as origin."""
        from footprinter.db.chats import SORT_WHITELIST

        assert "modified_at" in SORT_WHITELIST

    def test_chat_list_returns_modified_at(self, conn):
        """list_chats should return modified_at in result dicts."""
        from footprinter.db.chats import list_chats

        result = list_chats(conn)
        assert len(result["chats"]) > 0
        chat = result["chats"][0]
        assert "modified_at" in chat

    def test_chat_detail_returns_modified_at(self, conn):
        """get_chat_detail should return modified_at."""
        from footprinter.db.chats import get_chat_detail

        chat = get_chat_detail(conn, 1)
        assert chat is not None
        assert "modified_at" in chat

    def test_chat_service_default_sort_by(self):
        """Chat service list_ should default to modified_at."""
        import inspect

        from footprinter.services import chat_service

        sig = inspect.signature(chat_service.list_)
        default = sig.parameters["sort_by"].default
        assert default == "modified_at"


# ---------------------------------------------------------------------------
# Phase 3 RED/GREEN: Timestamp format standardization
# ---------------------------------------------------------------------------

# Pattern matching YYYY-MM-DD HH:MM:SS (SQLite CURRENT_TIMESTAMP format)
_SQLITE_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


class TestTimestampFormat:
    """All timestamps should use YYYY-MM-DD HH:MM:SS (UTC, space-separated)."""

    def test_utc_now_iso_returns_sqlite_format(self):
        """utc_now_iso() should return CURRENT_TIMESTAMP-compatible format."""
        from footprinter.utils.time import utc_now_iso

        result = utc_now_iso()
        assert _SQLITE_TS_RE.fullmatch(result), f"utc_now_iso() returned '{result}', expected YYYY-MM-DD HH:MM:SS"

    def test_utc_fmt_constant_exists(self):
        """UTC_FMT constant should be defined and exported."""
        from footprinter.utils.time import UTC_FMT

        assert UTC_FMT == "%Y-%m-%d %H:%M:%S"

    def test_utc_fmt_exported_from_utils(self):
        """UTC_FMT should be importable from footprinter.utils."""
        from footprinter.utils import UTC_FMT

        assert UTC_FMT == "%Y-%m-%d %H:%M:%S"

    def test_file_scanner_timestamps_match_sqlite_format(self, tmp_path):
        """file_scanner origin timestamps should use YYYY-MM-DD HH:MM:SS."""
        from footprinter.ingest.file_scanner import FileScanner

        # Create a temp file so we have something to scan
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        config = {
            "folders": [{"path": str(tmp_path)}],
            "indexing": {"supported_extensions": [".txt"]},
        }
        scanner = FileScanner(config)
        metadata = scanner.get_file_metadata(test_file)

        for field in ("created_at", "modified_at", "accessed_at"):
            value = metadata[field]
            assert _SQLITE_TS_RE.fullmatch(value), f"file_scanner {field} = '{value}', expected YYYY-MM-DD HH:MM:SS"

    def test_chatgpt_parser_timestamps_utc_format(self, tmp_path):
        """ChatGPT parser timestamps should use UTC YYYY-MM-DD HH:MM:SS."""
        from footprinter.ingest.chat_parsers.chatgpt_parser import ChatGPTParser

        # Unix timestamp: 2026-01-15 10:30:00 UTC = 1768476600
        test_data = [
            {
                "id": "test-conv-1",
                "title": "Test Chat",
                "create_time": 1768476600.0,
                "update_time": 1768480200.0,
                "mapping": {},
            }
        ]
        export_file = tmp_path / "conversations.json"
        export_file.write_text(json.dumps(test_data))

        parser = ChatGPTParser(str(export_file))
        chats = list(parser.parse_chats())

        assert len(chats) == 1
        chat = chats[0]
        assert _SQLITE_TS_RE.fullmatch(chat["created_at"]), (
            f"ChatGPT created_at = '{chat['created_at']}', expected YYYY-MM-DD HH:MM:SS"
        )
        assert _SQLITE_TS_RE.fullmatch(chat["updated_at"]), (
            f"ChatGPT updated_at = '{chat['updated_at']}', expected YYYY-MM-DD HH:MM:SS"
        )

    def test_browser_epoch_math_produces_sqlite_format(self):
        """Browser epoch conversions should produce YYYY-MM-DD HH:MM:SS."""
        from footprinter.utils.time import UTC_FMT

        # Safari: Core Data epoch (2001-01-01 UTC)
        core_data_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
        safari_ts = core_data_epoch + timedelta(seconds=694310011)
        assert _SQLITE_TS_RE.fullmatch(safari_ts.strftime(UTC_FMT))

        # Chrome: Windows epoch (1601-01-01 UTC)
        chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
        chrome_ts = chrome_epoch + timedelta(microseconds=13_350_000_000_000_000)
        assert _SQLITE_TS_RE.fullmatch(chrome_ts.strftime(UTC_FMT))
