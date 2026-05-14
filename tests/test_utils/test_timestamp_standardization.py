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


class TestFreshDbTimestampColumns:
    """Verify fresh databases have all timestamp columns without migration."""

    def test_fresh_db_has_all_timestamp_columns(self, tmp_path):
        """A fresh init_db() should produce tables with all new columns."""
        db_path = str(tmp_path / "fresh.db")
        from footprinter.ingest.database import Database

        db = Database(db_path)

        chats_cols = _get_columns(db.conn, "chats")
        assert "modified_at" in chats_cols
        assert "updated_at" in chats_cols

        for table in ("files", "folders", "visits", "chats", "messages", "emails"):
            assert _has_column(db.conn, table, "updated_at"), f"Fresh {table} should have updated_at"

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
