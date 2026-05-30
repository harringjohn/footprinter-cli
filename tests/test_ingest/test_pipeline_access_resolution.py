"""Tests for access_resolution pipeline stage — runner, registration, last-run tracking."""

import sqlite3
from datetime import datetime, timezone

import pytest

from footprinter.ingest.adapters.protocol import ErrorType, PipeStatus


@pytest.fixture
def conn(tool_db):
    """Full-schema database for access resolution tests."""
    yield tool_db


def _seed_entities(conn, indexed_at=None):
    """Insert minimal rows with NULL visibility/permissions.

    Returns dict of inserted IDs by entity type.
    """
    ts = indexed_at or datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()

    # Client
    cur.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'TestCo', 'testco', 'external')")
    # Project
    cur.execute(
        "INSERT INTO projects (id, name, client_id) VALUES (1, 'TestProj', 1)"
    )
    # Files
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id, indexed_at) "
        "VALUES (1, 'local', 'a.py', '/Users/me/Work/test/a.py', 'work', 1, ?)",
        (ts,),
    )
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, indexed_at) "
        "VALUES (2, 'local', 'b.py', '/Users/me/Personal/b.py', 'personal', ?)",
        (ts,),
    )
    # Email
    cur.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, subject, "
        "received_at, project_id, client_id, indexed_at) "
        "VALUES (10, 'msg1', 't1', 'personal', 'Hello', '2024-01-01', 1, 1, ?)",
        (ts,),
    )
    # Chat
    cur.execute(
        "INSERT INTO chats (id, external_id, account, title, project_id, client_id, indexed_at) "
        "VALUES (20, 'chat1', 'claude', 'Debug session', 1, 1, ?)",
        (ts,),
    )
    # Visit
    cur.execute(
        "INSERT INTO visits (id, url, title, visit_time, browser, indexed_at) "
        "VALUES (40, 'https://example.com', 'Example', '2024-01-15T10:00:00', 'chrome', ?)",
        (ts,),
    )
    conn.commit()
    return {"file": [1, 2], "email": [10], "chat": [20], "visit": [40]}


def _add_more_entities(conn, indexed_at=None):
    """Add additional entities after the initial seed.

    Returns dict of inserted IDs by entity type.
    """
    ts = indexed_at or datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, indexed_at) "
        "VALUES (3, 'local', 'c.py', '/Users/me/Work/c.py', 'work', ?)",
        (ts,),
    )
    cur.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, subject, "
        "received_at, indexed_at) "
        "VALUES (11, 'msg2', 't2', 'work', 'Meeting', '2024-01-02', ?)",
        (ts,),
    )
    conn.commit()
    return {"file": [3], "email": [11]}


class TestLastRunTracking:
    """Tests for the last-run read helpers (backed by ingests table)."""

    def test_last_run_read_returns_none_when_empty(self, conn):
        """Reading last run for a pipe with no rows returns None."""
        from footprinter.ingest.processing import _read_last_run

        # ingests table exists via tool_db fixture (init_db)
        assert _read_last_run(conn, "access_resolution") is None

    def test_read_last_run_reads_service_rows(self, conn):
        """_read_last_run picks up IngestService-written rows (with mode/trigger set)."""
        from footprinter.ingest.processing import _read_last_run

        ts = "2026-03-22T08:00:00+00:00"
        conn.execute(
            "INSERT INTO ingests (pipe, started_at, completed_at, status, mode, trigger)"
            " VALUES (?, ?, ?, 'completed', 'incremental', 'cli')",
            ("access_resolution", ts, ts),
        )
        conn.commit()
        assert _read_last_run(conn, "access_resolution") == ts


class TestAccessResolutionRunner:
    """Tests for run_access_resolution() — full and incremental modes."""

    def test_full_mode_stamps_all_entities(self, conn):
        """Full mode stamps visibility and permissions on all entities."""
        _seed_entities(conn)
        from footprinter.ingest.processing import run_access_resolution

        # Wrap conn in a Database-like object for the runner
        db = _make_db_wrapper(conn)
        result = run_access_resolution(db, full_mode=True)

        assert result.status == PipeStatus.COMPLETED

        # All files should have non-NULL visibility
        rows = conn.execute("SELECT visibility, access FROM files").fetchall()
        for row in rows:
            assert row["visibility"] is not None
            assert row["access"] is not None

        # Emails
        rows = conn.execute("SELECT visibility, access FROM emails").fetchall()
        for row in rows:
            assert row["visibility"] is not None
            assert row["access"] is not None

        # Chats
        rows = conn.execute("SELECT visibility, access FROM chats").fetchall()
        for row in rows:
            assert row["visibility"] is not None
            assert row["access"] is not None

    def test_incremental_stamps_only_new_entities(self, conn):
        """Incremental mode only stamps entities newer than last run."""
        t1 = "2026-03-20T10:00:00+00:00"
        t2 = "2026-03-20T12:00:00+00:00"

        _seed_entities(conn, indexed_at=t1)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import run_access_resolution

        # First run — full (stamps everything)
        run_access_resolution(db, full_mode=True)

        # Simulate IngestService writing the completed row (runner no longer does)
        conn.execute(
            "INSERT INTO ingests (pipe, started_at, completed_at, status, mode, trigger)"
            " VALUES ('access_resolution', ?, ?, 'completed', 'full', 'cli')",
            (t1, t1),
        )
        conn.commit()

        # Reset visibility to NULL to detect what gets re-stamped
        conn.execute("UPDATE files SET visibility = NULL, access = NULL")
        conn.execute("UPDATE emails SET visibility = NULL, access = NULL")
        conn.execute("UPDATE chats SET visibility = NULL, access = NULL")
        conn.commit()

        # Add new entities with a later timestamp
        _add_more_entities(conn, indexed_at=t2)

        # Incremental run
        result = run_access_resolution(db, full_mode=False)

        assert result.status == PipeStatus.COMPLETED

        # New file (id=3) should be stamped
        row = conn.execute("SELECT visibility FROM files WHERE id = 3").fetchone()
        assert row["visibility"] is not None

        # Old file (id=1) should still be NULL (wasn't re-processed)
        row = conn.execute("SELECT visibility FROM files WHERE id = 1").fetchone()
        assert row["visibility"] is None

    def test_incremental_with_no_last_run_stamps_all(self, conn):
        """First incremental run (no last run) behaves like full mode."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import run_access_resolution

        result = run_access_resolution(db, full_mode=False)

        assert result.status == PipeStatus.COMPLETED

        # All files should be stamped
        rows = conn.execute("SELECT visibility FROM files WHERE visibility IS NOT NULL").fetchall()
        assert len(rows) == 2  # both seeded files

    def test_no_bare_last_run_row_after_run(self, conn):
        """run_access_resolution does not write bare last-run rows (IngestService does)."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import run_access_resolution

        run_access_resolution(db, full_mode=True)

        # No rows with NULL mode — bare last-run rows should not exist
        count = conn.execute(
            "SELECT COUNT(*) FROM ingests WHERE pipe = 'access_resolution' AND mode IS NULL"
        ).fetchone()[0]
        assert count == 0

    def test_runner_does_not_persist_last_run(self, conn):
        """Runner alone writes no last-run row — IngestService owns that lifecycle."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import _read_last_run, run_access_resolution

        run_access_resolution(db, full_mode=True)

        # Without IngestService wrapping, no completed row should exist
        assert _read_last_run(conn, "access_resolution") is None

    def test_returns_stats_dict(self, conn):
        """Return value contains status and per-entity-type counts."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import run_access_resolution

        result = run_access_resolution(db, full_mode=True)

        assert result.status == PipeStatus.COMPLETED
        # Should have counts for entity types that were stamped
        assert "file" in result.data or "email" in result.data or "chat" in result.data


class TestAccessResolutionErrorHandling:
    """Tests for error handling in run_access_resolution()."""

    def test_full_mode_returns_error_on_exception(self, conn, monkeypatch):
        """Full mode: if recalculate_access raises, return error PipeResult, no last-run row."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import _read_last_run, run_access_resolution

        monkeypatch.setattr(
            "footprinter.access_stamper.recalculate_access",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = run_access_resolution(db, full_mode=True)

        assert result.status == PipeStatus.ERROR
        assert result.error_type == ErrorType.RUNTIME
        assert "boom" in result.error
        # Last-run row should NOT be written on error
        assert _read_last_run(conn, "access_resolution") is None

    def test_incremental_returns_error_on_exception(self, conn, monkeypatch):
        """Incremental mode: if batch_resolve_visibility raises, return error PipeResult."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import (
            _read_last_run,
            run_access_resolution,
        )

        # Run once to stamp entities, then simulate IngestService last-run record
        run_access_resolution(db, full_mode=True)
        last_run_ts = "2026-03-20T12:00:00+00:00"
        conn.execute(
            "INSERT INTO ingests (pipe, started_at, completed_at, status, mode, trigger)"
            " VALUES ('access_resolution', ?, ?, 'completed', 'full', 'cli')",
            (last_run_ts, last_run_ts),
        )
        conn.commit()
        old_last_run = _read_last_run(conn, "access_resolution")
        assert old_last_run is not None

        # Add new entities so incremental has work to do
        _add_more_entities(conn, indexed_at="2099-01-01T00:00:00+00:00")

        # Patch batch_resolve_visibility where stamp_entities uses it
        monkeypatch.setattr(
            "footprinter.access_stamper.batch_resolve_visibility",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("vis crash")),
        )

        result = run_access_resolution(db, full_mode=False)

        assert result.status == PipeStatus.ERROR
        assert result.error_type == ErrorType.RUNTIME
        assert "vis crash" in result.error
        # Last-run row should stay at the old value
        assert _read_last_run(conn, "access_resolution") == old_last_run

    def test_incremental_error_mid_loop_returns_error_and_preserves_last_run(self, conn, monkeypatch):
        """Mid-loop failure on Nth entity type: error PipeResult returned, last-run row unchanged."""
        _seed_entities(conn)
        db = _make_db_wrapper(conn)

        from footprinter.ingest.processing import _read_last_run, run_access_resolution

        # Run full first, then simulate IngestService last-run record
        run_access_resolution(db, full_mode=True)
        last_run_ts = "2026-03-20T12:00:00+00:00"
        conn.execute(
            "INSERT INTO ingests (pipe, started_at, completed_at, status, mode, trigger)"
            " VALUES ('access_resolution', ?, ?, 'completed', 'full', 'cli')",
            (last_run_ts, last_run_ts),
        )
        conn.commit()
        old_last_run = _read_last_run(conn, "access_resolution")

        # Add new entities across multiple types
        _add_more_entities(conn, indexed_at="2099-01-01T00:00:00+00:00")

        # Save original so file processing succeeds, email processing fails
        import footprinter.access_stamper as access_mod

        original_batch_vis = access_mod.batch_resolve_visibility
        processed_types = []

        def selective_raise(conn, entity_type, ids):
            processed_types.append(entity_type)
            if entity_type == "email":
                raise RuntimeError("email vis crash")
            return original_batch_vis(conn, entity_type, ids)

        monkeypatch.setattr(
            "footprinter.access_stamper.batch_resolve_visibility",
            selective_raise,
        )

        result = run_access_resolution(db, full_mode=False)

        assert result.status == PipeStatus.ERROR
        assert result.error_type == ErrorType.RUNTIME
        assert "email vis crash" in result.error
        # At least one entity type was processed before the failure
        assert "file" in processed_types
        # Last-run row unchanged — next run will re-process these entities
        assert _read_last_run(conn, "access_resolution") == old_last_run


class TestAccessResolutionRegistration:
    """Tests for access_resolution appearing in pipeline definitions."""

    def test_access_resolution_in_local_pipeline(self):
        """local pipeline includes access_resolution after data-source pipes."""
        from footprinter.ingest.registry import CORE_PIPES, get_pipelines

        pipelines = get_pipelines({})
        stages = pipelines["local"]
        assert "access_resolution" in stages
        # Runs after all data-source pipes (post-processing slot)
        ar_idx = stages.index("access_resolution")
        for core in CORE_PIPES:
            assert stages.index(core) < ar_idx

    def test_access_resolution_in_all_pipeline(self):
        """all pipeline includes access_resolution after data-source pipes."""
        from footprinter.ingest.registry import CORE_PIPES, get_pipelines

        pipelines = get_pipelines({})
        stages = pipelines["all"]
        assert "access_resolution" in stages
        ar_idx = stages.index("access_resolution")
        for core in CORE_PIPES:
            assert stages.index(core) < ar_idx

    def test_access_resolution_in_all_sources(self):
        """access_resolution is a valid source name."""
        from footprinter.ingest.registry import get_all_pipes

        assert "access_resolution" in get_all_pipes({})

    def test_standalone_via_runner(self, conn, monkeypatch, tmp_path):
        """PipeRunner.run_pipes(['access_resolution']) dispatches through processing pipeline.

        Goes directly through the runner because the orchestrator's
        user-facing run_pipes() rejects explicit post-pipe invocation.
        """
        _seed_entities(conn)

        # Patch get_db_path to use our test db
        db_path = tmp_path / "orch_test.db"
        # Copy conn data to a file-backed db for the orchestrator
        file_conn = sqlite3.connect(str(db_path))
        conn.backup(file_conn)
        file_conn.close()

        monkeypatch.setattr(
            "footprinter.ingest.orchestrator.get_db_path",
            lambda: db_path,
        )
        monkeypatch.setattr(
            "footprinter.ingest.orchestrator.get_config_path",
            lambda: tmp_path / "config.yaml",
        )
        # Write minimal config
        (tmp_path / "config.yaml").write_text("directories:\n  work: ~/Work\n  personal: ~/Personal\n")

        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator(config_path=str(tmp_path / "config.yaml"))
        try:
            results = orch.runner.run_pipes(["access_resolution"])
            assert len(results) == 1
            assert results[0]["status"] in ("completed", "info")
        finally:
            orch.close()


class TestFullModeLargeDatabase:
    """Tests for large databases exceeding SQLite's variable limit."""

    def test_full_mode_large_database(self, conn):
        """Full mode completes without error on a database with >999 files."""
        # Lower SQLite variable limit to reproduce production crash
        old_limit = conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        try:
            count = 1200
            ts = datetime.now(timezone.utc).isoformat()
            cur = conn.cursor()
            for i in range(1, count + 1):
                cur.execute(
                    "INSERT INTO files (id, source, name, path, account, indexed_at) "
                    "VALUES (?, 'local', ?, ?, 'work', ?)",
                    (i, f"f{i}.py", f"/Users/me/Work/f{i}.py", ts),
                )
            conn.commit()

            from footprinter.ingest.processing import run_access_resolution

            db = _make_db_wrapper(conn)
            result = run_access_resolution(db, full_mode=True)

            assert result.status == PipeStatus.COMPLETED
            assert result.data.get("file", 0) == count
        finally:
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, old_limit)


class TestNoPrivateImports:
    """Verify processing.py doesn't reach into access.py internals."""

    def test_processing_does_not_import_private_access_helpers(self):
        """processing.py should not import _write_back_* from access.py."""
        import ast
        from pathlib import Path

        src = Path("footprinter/ingest/processing.py").read_text()
        tree = ast.parse(src)

        private_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "footprinter.access_stamper":
                for alias in node.names:
                    if alias.name.startswith("_"):
                        private_names.append(alias.name)

        assert private_names == [], f"processing.py imports private access helpers: {private_names}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_wrapper(conn):
    """Create a minimal Database-like wrapper around a raw connection.

    The runner needs db.conn and db.db_path.
    """

    class _DbWrapper:
        def __init__(self, connection):
            self.conn = connection
            self.db_path = ":memory:"

    return _DbWrapper(conn)
