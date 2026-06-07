"""Integration tests: MCP tools survive transient schema migration errors.

Two strategies:

TestSchemaMigrationResilience — simulates transient errors by patching
open_checked_connection to raise OperationalError, then succeed on retry.

TestConcurrentMigrationReads — exercises the retry path with a real
_migrate_access_columns running in a background thread against a
pre-migration database.
"""

import logging
import sqlite3
import threading
from unittest.mock import patch

import pytest

from footprinter.mcp.tools.search import footprinter_search
from footprinter.mcp.tools.status import footprinter_status


class _RetryLogHandler(logging.Handler):
    """Captures retry log messages from handle_db_errors and signals an event."""

    def __init__(self, event: threading.Event) -> None:
        super().__init__()
        self.event = event
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "Transient schema error" in record.getMessage():
            self.count += 1
            self.event.set()


class _GatedConn:
    """Wraps a sqlite3.Connection, gating commit() on threading events.

    The migration thread sets *migration_ready* when it reaches commit, then
    blocks until *release_gate* is set — either by a retry log (search) or
    by a reader completing its first call (status).
    """

    def __init__(
        self,
        real_conn: sqlite3.Connection,
        migration_ready: threading.Event,
        release_gate: threading.Event,
    ) -> None:
        self._real = real_conn
        self._migration_ready = migration_ready
        self._release_gate = release_gate

    def commit(self) -> None:
        self._migration_ready.set()
        self._release_gate.wait(timeout=5)
        self._real.commit()

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _gate_migration(original, migration_ready: threading.Event, release_gate: threading.Event):
    """Return a wrapper for _migrate_access_columns that gates its commit."""
    def wrapper(self):
        real_conn = self.conn
        self.conn = _GatedConn(real_conn, migration_ready, release_gate)
        try:
            original(self)
        finally:
            self.conn = real_conn
    return wrapper


class TestSchemaMigrationResilience:

    def _patch_transient_then_succeed(self, fail_count=1):
        """Return a side_effect that raises transient errors then delegates to the real impl."""
        from footprinter.db_base import open_checked_connection as real_open

        call_count = 0

        def side_effect(*, read_only=False):
            nonlocal call_count
            call_count += 1
            if call_count <= fail_count:
                raise sqlite3.OperationalError("database schema has changed")
            return real_open(read_only=read_only)

        return side_effect

    def test_search_survives_transient_schema_error(self, tool_db, tmp_path):
        db_path = tmp_path / "test.db"
        se = self._patch_transient_then_succeed(fail_count=1)
        with patch("footprinter.db_base.get_db_path", return_value=db_path), \
             patch("footprinter.mcp.db.open_checked_connection", side_effect=se):
            result = footprinter_search("test")
        assert "error_code" not in result or result.get("error_code") != "DATABASE_ERROR"

    def test_status_survives_transient_schema_error(self, tool_db, tmp_path):
        db_path = tmp_path / "test.db"
        se = self._patch_transient_then_succeed(fail_count=1)
        with patch("footprinter.db_base.get_db_path", return_value=db_path), \
             patch("footprinter.mcp.db.open_checked_connection", side_effect=se):
            result = footprinter_status()
        assert "error_code" not in result or result.get("error_code") != "DATABASE_ERROR"

    def test_all_retries_exhausted_returns_database_error(self):
        def always_fail(*, read_only=False):
            raise sqlite3.OperationalError("database schema has changed")

        with patch("footprinter.mcp.db.open_checked_connection", side_effect=always_fail):
            result = footprinter_search("test")
        assert result["error_code"] == "DATABASE_ERROR"

    def test_error_response_hides_sql_details(self):
        def always_fail(*, read_only=False):
            raise sqlite3.OperationalError("no such column: visibility")

        with patch("footprinter.mcp.db.open_checked_connection", side_effect=always_fail):
            result = footprinter_search("test")
        assert result["error_code"] == "DATABASE_ERROR"
        assert result["error"] == "Unreachable"
        assert "hint" in result
        assert "visibility" not in result["error"]


@pytest.mark.slow
class TestConcurrentMigrationReads:
    """Real concurrency: _migrate_access_columns in a background thread + MCP reads."""

    _ROW_COUNT = 800

    @staticmethod
    def _prepare_pre_migration_db(db_path: str) -> None:
        """Create a post-init DB then downgrade columns to the old mcp_view/mcp_read names.

        Inserts _ROW_COUNT files with visibility='visible' so the migration UPDATE
        takes measurable time, widening the race window.
        """
        from footprinter.ingest.database import Database
        from footprinter.ingest.db.schema import ACCESS_CONTROL_TABLES

        db = Database(db_path)
        db.close()

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")

        for table in ACCESS_CONTROL_TABLES:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN visibility TO mcp_view")
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN access TO mcp_read")
            try:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN visibility_source TO mcp_view_source")
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN access_source TO mcp_read_source")
            except sqlite3.OperationalError:
                pass

        conn.execute("PRAGMA writable_schema = ON")
        for table in list(ACCESS_CONTROL_TABLES) + ["visibility_policies"]:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not row:
                continue
            conn.execute(
                "UPDATE sqlite_master SET sql = ? WHERE type='table' AND name=?",
                (row[0].replace("'full'", "'visible'"), table),
            )
        conn.execute("PRAGMA writable_schema = OFF")
        v = conn.execute("PRAGMA schema_version").fetchone()[0]
        conn.execute(f"PRAGMA schema_version = {v + 1}")

        conn.execute("PRAGMA ignore_check_constraints = ON")
        for i in range(TestConcurrentMigrationReads._ROW_COUNT):
            conn.execute(
                "INSERT INTO files (source, name, mcp_view, mcp_read, mcp_view_source, mcp_read_source) "
                "VALUES ('local', ?, 'visible', 'allow', 'policy:global', 'policy:global')",
                (f"file_{i}.txt",),
            )
        conn.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) "
            "VALUES ('global', 'visible')"
        )
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()
        conn.close()

    def _run_with_gated_migration(
        self, tmp_path, tool_fn, tool_args=(), num_readers=1, reads_per_reader=5,
        assert_retry=True,
    ):
        """Run tool_fn concurrently with a gated migration.

        Holds the migration's commit until the release_gate fires, guaranteeing
        that reads overlap the migration window.

        When *assert_retry* is True (search), the gate releases on the first
        retry log message — proving the retry decorator fired.  When False
        (status), the gate releases after the first read completes — status
        handles transient errors internally via _safe_query, so the retry
        decorator is not involved.
        """
        db_path = str(tmp_path / "concurrent.db")
        self._prepare_pre_migration_db(db_path)

        from footprinter.ingest.database import Database
        from footprinter.ingest.db.ddl import DDLMixin

        migration_ready = threading.Event()
        retry_detected = threading.Event()
        reads_started = threading.Event()
        handler = _RetryLogHandler(retry_detected)
        release_gate = retry_detected if assert_retry else reads_started

        db_logger = logging.getLogger("footprinter.mcp.db")
        original_level = db_logger.level
        db_logger.setLevel(logging.DEBUG)
        db_logger.addHandler(handler)

        migration_errors: list[Exception] = []
        all_results: list[list[dict]] = [[] for _ in range(num_readers)]

        def migrate():
            try:
                Database(db_path).close()
            except Exception as exc:
                migration_errors.append(exc)

        def read_loop(idx):
            try:
                migration_ready.wait(timeout=5)
                for j in range(reads_per_reader):
                    all_results[idx].append(tool_fn(*tool_args))
                    if j == 0:
                        reads_started.set()
            except Exception as exc:
                migration_errors.append(exc)

        try:
            gated = _gate_migration(
                DDLMixin._migrate_access_columns, migration_ready, release_gate,
            )
            with patch.object(DDLMixin, "_migrate_access_columns", gated), \
                 patch("footprinter.db_base.get_db_path", return_value=tmp_path / "concurrent.db"):
                threads = [threading.Thread(target=migrate)]
                for i in range(num_readers):
                    threads.append(threading.Thread(target=read_loop, args=(i,)))
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=15)
        finally:
            db_logger.removeHandler(handler)
            db_logger.setLevel(original_level)

        assert not migration_errors, f"Migration raised: {migration_errors}"
        flat = [r for batch in all_results for r in batch]
        db_errors = [r for r in flat if r.get("error_code") == "DATABASE_ERROR"]
        assert not db_errors, f"{len(db_errors)}/{len(flat)} reads returned DATABASE_ERROR"
        if assert_retry:
            assert handler.count >= 1, "Retry path never fired — migration window was not traversed"
        return flat

    def test_search_survives_concurrent_migration(self, tmp_path):
        self._run_with_gated_migration(tmp_path, footprinter_search, tool_args=("file",))

    def test_status_survives_concurrent_migration(self, tmp_path):
        # status uses _safe_query fallback (not the retry decorator) for
        # transient column errors, so we gate on reads_started instead.
        self._run_with_gated_migration(tmp_path, footprinter_status, assert_retry=False)

    def test_post_migration_reads_return_correct_data(self, tmp_path):
        db_path = str(tmp_path / "concurrent.db")
        self._prepare_pre_migration_db(db_path)

        from footprinter.ingest.database import Database

        Database(db_path).close()

        with patch("footprinter.db_base.get_db_path", return_value=tmp_path / "concurrent.db"):
            search_result = footprinter_search("file_0")
            status_result = footprinter_status()

        assert "error_code" not in search_result, f"Search failed: {search_result}"
        assert "error_code" not in status_result, f"Status failed: {status_result}"

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT visibility FROM files WHERE name = 'file_0.txt'").fetchone()
        assert row[0] == "full", f"Migration should convert 'visible' → 'full', got {row[0]!r}"
        src = conn.execute("SELECT visibility_source FROM files WHERE name = 'file_0.txt'").fetchone()
        assert src[0] == "policy:global", f"Expected source preserved, got {src[0]!r}"
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        assert integrity == "ok", f"Post-migration integrity check failed: {integrity}"

    def test_multiple_readers_during_migration(self, tmp_path):
        self._run_with_gated_migration(
            tmp_path, footprinter_search, tool_args=("file",), num_readers=3,
        )
