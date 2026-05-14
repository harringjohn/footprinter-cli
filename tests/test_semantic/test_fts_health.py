"""Tests for FTS health check and repair functionality."""

import pytest

from footprinter.ingest.database import Database


@pytest.fixture
def fts_db(tmp_path):
    """Create a Database with schema and sample data for FTS tests."""
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))

    # Insert test data into base tables
    cursor = db.conn.cursor()
    cursor.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('test.txt', '/tmp/test.txt', 'local', 'listed', 'text', 100)"
    )
    cursor.execute(
        "INSERT INTO emails (message_id, thread_id, account, subject, from_name, "
        "from_address, body_preview, received_at) "
        "VALUES ('msg-1', 'thread-1', 'personal', 'Hello', 'Alice', "
        "'alice@test.com', 'Preview text', '2026-01-01')"
    )
    cursor.execute(
        "INSERT INTO chats (external_id, account, title, summary, message_count) "
        "VALUES ('chat-1', 'personal', 'Test Chat', 'A summary', 1)"
    )
    db.conn.commit()
    yield db
    db.close()


class TestCheckFtsHealth:
    def test_check_fts_health_ok(self, fts_db):
        """Healthy FTS tables report 'ok'."""
        result = fts_db.check_fts_health()

        for table in ("files_fts", "emails_fts", "chats_fts"):
            assert table in result
            assert result[table]["status"] == "ok"

    def test_check_fts_health_no_drift_status(self, fts_db):
        """check_fts_health() never returns 'drift' — only 'ok' or 'error'."""
        result = fts_db.check_fts_health()

        for table in ("files_fts", "emails_fts", "chats_fts"):
            assert result[table]["status"] in ("ok", "error"), (
                f"{table} returned status '{result[table]['status']}', expected 'ok' or 'error'"
            )

    def test_check_fts_health_queryable_is_ok(self, fts_db):
        """FTS table reports 'ok' even when stale (triggers dropped, new rows added)."""
        fts_db.drop_fts_triggers()
        fts_db.conn.execute(
            "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
            "VALUES ('stale.txt', '/tmp/stale.txt', 'local', 'listed', 'text', 75)"
        )
        fts_db.conn.commit()

        result = fts_db.check_fts_health()

        # Table is queryable → status should be "ok" regardless of staleness
        assert result["files_fts"]["status"] == "ok"

    def test_check_fts_health_detects_corruption(self, fts_db):
        """Dropped FTS table is detected as 'error'."""
        fts_db.conn.execute("DROP TABLE files_fts")
        fts_db.conn.commit()

        result = fts_db.check_fts_health()

        assert result["files_fts"]["status"] == "error"
        assert "message" in result["files_fts"]
        # Other tables should still be ok
        assert result["emails_fts"]["status"] == "ok"
        assert result["chats_fts"]["status"] == "ok"

    def test_check_fts_health_ok_after_rebuild(self, fts_db):
        """Health check reports 'ok' after rebuild with additional data."""
        fts_db.drop_fts_triggers()
        fts_db.conn.execute(
            "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
            "VALUES ('extra.txt', '/tmp/extra.txt', 'local', 'listed', 'text', 50)"
        )
        fts_db.conn.commit()

        fts_db.rebuild_fts_indexes()

        result = fts_db.check_fts_health()
        assert result["files_fts"]["status"] == "ok"


class TestRepairFts:
    def test_repair_fts_recovers_from_dropped_tables(self, fts_db):
        """Repair restores FTS tables after they've been dropped."""
        fts_db.conn.execute("DROP TABLE IF EXISTS files_fts")
        fts_db.conn.execute("DROP TABLE IF EXISTS emails_fts")
        fts_db.conn.execute("DROP TABLE IF EXISTS chats_fts")
        fts_db.conn.commit()

        fts_db.repair_fts()

        # Verify tables exist and have correct row counts
        for fts_table, expected in [("files_fts", 1), ("emails_fts", 1), ("chats_fts", 1)]:
            count = fts_db.conn.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
            assert count == expected, f"{fts_table} has {count} rows, expected {expected}"

    def test_repair_fts_idempotent(self, fts_db):
        """Running repair twice produces no errors and correct counts."""
        result1 = fts_db.repair_fts()
        result2 = fts_db.repair_fts()

        # Both should succeed with same after counts
        for table in ("files_fts", "emails_fts", "chats_fts"):
            assert result1[table]["after"] == result2[table]["after"]

    def test_repair_fts_reports_counts(self, fts_db):
        """Repair returns a dict with before/after row counts per FTS table."""
        result = fts_db.repair_fts()

        for table in ("files_fts", "emails_fts", "chats_fts"):
            assert table in result
            assert "before" in result[table]
            assert "after" in result[table]
            assert isinstance(result[table]["after"], int)


class TestStatusIncludesFts:
    def test_status_includes_fts_health(self, fts_db):
        """get_status() includes FTS row count data."""
        from footprinter.ingest.status import get_status

        status = get_status(db_path=str(fts_db.db_path))

        assert "fts" in status
        assert "files_fts" in status["fts"]
        assert "base_rows" in status["fts"]["files_fts"]
        assert "fts_rows" in status["fts"]["files_fts"]


class TestFtsExcludesOpaque:
    """FTS5 indexes must not contain content columns for opaque/hidden files."""

    def test_fts_excludes_opaque_content(self, fts_db):
        """Opaque file content_preview is not searchable via FTS, but name is."""
        cursor = fts_db.conn.cursor()
        # Insert an opaque file with distinctive content
        cursor.execute(
            "INSERT INTO files (name, path, source, status, content_type, "
            "size_bytes, content_preview, mcp_view) "
            "VALUES ('budget.xlsx', '/tmp/budget.xlsx', 'local', 'listed', "
            "'spreadsheet', 200, 'classified financial data', 'opaque')"
        )
        # Insert a visible file with different content
        cursor.execute(
            "INSERT INTO files (name, path, source, status, content_type, "
            "size_bytes, content_preview, mcp_view) "
            "VALUES ('report.txt', '/tmp/report.txt', 'local', 'listed', "
            "'text', 100, 'public quarterly report', 'visible')"
        )
        fts_db.conn.commit()

        # FTS MATCH on opaque content term should return 0 results
        rows = cursor.execute(
            "SELECT rowid FROM files_fts WHERE files_fts MATCH ?",
            ('"classified"*',),
        ).fetchall()
        assert len(rows) == 0, "Opaque file content should not be in FTS index"

        # FTS MATCH on opaque file's name should still work (metadata is indexed)
        rows = cursor.execute(
            "SELECT rowid FROM files_fts WHERE files_fts MATCH ?",
            ('"budget"*',),
        ).fetchall()
        assert len(rows) == 1, "Opaque file name should still be searchable"

    def test_fts_visibility_change_updates_index(self, fts_db):
        """Changing mcp_view updates FTS content accordingly."""
        cursor = fts_db.conn.cursor()
        # Insert a visible file with searchable content
        cursor.execute(
            "INSERT INTO files (name, path, source, status, content_type, "
            "size_bytes, content_preview, mcp_view) "
            "VALUES ('memo.txt', '/tmp/memo.txt', 'local', 'listed', "
            "'text', 50, 'sensitive merger details', 'visible')"
        )
        fts_db.conn.commit()

        # Content should be searchable while visible
        rows = cursor.execute(
            "SELECT rowid FROM files_fts WHERE files_fts MATCH ?",
            ('"merger"*',),
        ).fetchall()
        assert len(rows) == 1, "Visible file content should be in FTS index"

        # Change to opaque — content should no longer match
        cursor.execute("UPDATE files SET mcp_view = 'opaque' WHERE name = 'memo.txt'")
        fts_db.conn.commit()

        rows = cursor.execute(
            "SELECT rowid FROM files_fts WHERE files_fts MATCH ?",
            ('"merger"*',),
        ).fetchall()
        assert len(rows) == 0, "Opaque file content should be removed from FTS"

        # Change back to visible — content should match again
        cursor.execute("UPDATE files SET mcp_view = 'visible' WHERE name = 'memo.txt'")
        fts_db.conn.commit()

        rows = cursor.execute(
            "SELECT rowid FROM files_fts WHERE files_fts MATCH ?",
            ('"merger"*',),
        ).fetchall()
        assert len(rows) == 1, "Restored visible file content should be in FTS"


class TestCheckFtsTriggers:
    """Tests for FTS trigger detection and auto-recovery."""

    def test_check_fts_triggers_reports_missing(self, fts_db):
        """Dropped triggers are reported as missing."""
        fts_db.drop_fts_triggers()
        missing = fts_db.check_fts_triggers()
        assert len(missing) == len(fts_db._FTS_TRIGGER_NAMES)
        assert set(missing) == set(fts_db._FTS_TRIGGER_NAMES)

    def test_check_fts_triggers_reports_none_missing(self, fts_db):
        """Healthy DB reports no missing triggers."""
        missing = fts_db.check_fts_triggers()
        assert missing == []

    def test_check_fts_health_includes_trigger_status(self, fts_db):
        """check_fts_health() includes triggers_missing key when triggers are dropped."""
        fts_db.drop_fts_triggers()
        result = fts_db.check_fts_health()
        for table in ("files_fts", "emails_fts", "chats_fts"):
            assert "triggers_missing" in result[table]
            assert len(result[table]["triggers_missing"]) > 0

    def test_check_fts_health_triggers_empty_when_healthy(self, fts_db):
        """check_fts_health() reports empty triggers_missing when all present."""
        result = fts_db.check_fts_health()
        for table in ("files_fts", "emails_fts", "chats_fts"):
            assert "triggers_missing" in result[table]
            assert result[table]["triggers_missing"] == []

    def test_ingest_service_restores_missing_triggers(self, fts_db):
        """IngestService.ensure_fts_health() auto-restores missing FTS triggers."""
        import sqlite3

        from footprinter.services.ingest_service import IngestService

        fts_db.drop_fts_triggers()

        # Verify triggers are gone
        assert len(fts_db.check_fts_triggers()) == len(fts_db._FTS_TRIGGER_NAMES)

        # IngestService with get_db pointing to the real database
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        svc = IngestService(conn, get_db=lambda: fts_db)
        svc.ensure_fts_health(full_mode=False)

        # Verify triggers are restored
        assert fts_db.check_fts_triggers() == []


class TestCliRepairFtsFlag:
    def test_run_cli_repair_fts_flag(self):
        """--repair-fts is accepted by the argparser and routes to handler."""
        from unittest.mock import patch

        with patch("footprinter.ingest.vector_ops._repair_fts") as mock_repair:
            from tests.conftest import run_fp

            stdout, stderr, code = run_fp("ingest", "--repair-fts")
            assert code == 0
            mock_repair.assert_called_once()
