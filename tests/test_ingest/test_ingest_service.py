"""Tests for IngestService — the single authority on ingest tracking."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from footprinter.services.ingest_service import IngestService


class TestBegin:
    def test_begin_inserts_running_record(self, tool_db):
        svc = IngestService(tool_db)
        ingest_id = svc.begin("browser", mode="incremental", trigger="cli:ingest")

        assert isinstance(ingest_id, int)
        row = tool_db.execute("SELECT * FROM ingests WHERE id = ?", (ingest_id,)).fetchone()
        assert row["status"] == "running"
        assert row["pipe"] == "browser"
        assert row["mode"] == "incremental"
        assert row["trigger"] == "cli:ingest"
        assert row["started_at"] is not None

    def test_begin_minimal(self, tool_db):
        svc = IngestService(tool_db)
        ingest_id = svc.begin("browser")

        row = tool_db.execute("SELECT * FROM ingests WHERE id = ?", (ingest_id,)).fetchone()
        assert row["mode"] is None
        assert row["trigger"] is None


class TestComplete:
    def test_complete_updates_record(self, tool_db):
        svc = IngestService(tool_db)
        ingest_id = svc.begin("browser")
        svc.complete(
            ingest_id,
            result={
                "items_processed": 100,
                "items_new": 50,
                "items_updated": 30,
                "items_skipped": 20,
                "errors": 0,
                "elapsed_seconds": 12.5,
            },
        )

        row = tool_db.execute("SELECT * FROM ingests WHERE id = ?", (ingest_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        assert row["items_processed"] == 100
        assert row["items_new"] == 50
        assert row["items_updated"] == 30
        assert row["items_skipped"] == 20
        assert row["errors"] == 0
        assert row["elapsed_seconds"] == 12.5

    def test_complete_with_metadata(self, tool_db):
        svc = IngestService(tool_db)
        ingest_id = svc.begin("browser")
        svc.complete(ingest_id, metadata={"flags": ["full_rebuild"]})

        row = tool_db.execute("SELECT metadata FROM ingests WHERE id = ?", (ingest_id,)).fetchone()
        assert row["metadata"] is not None
        parsed = json.loads(row["metadata"])
        assert parsed == {"flags": ["full_rebuild"]}


class TestFail:
    def test_fail_records_error(self, tool_db):
        svc = IngestService(tool_db)
        ingest_id = svc.begin("browser")
        svc.fail(ingest_id, error="Connection refused")

        row = tool_db.execute("SELECT * FROM ingests WHERE id = ?", (ingest_id,)).fetchone()
        assert row["status"] == "failed"
        assert row["completed_at"] is not None
        parsed = json.loads(row["metadata"])
        assert parsed["error"] == "Connection refused"


class TestLastRun:
    def test_last_run_returns_completed_timestamp(self, tool_db):
        svc = IngestService(tool_db)

        id1 = svc.begin("browser")
        svc.complete(id1)
        time.sleep(0.01)  # ensure distinct timestamps
        id2 = svc.begin("browser")
        svc.complete(id2)

        row2 = tool_db.execute("SELECT completed_at FROM ingests WHERE id = ?", (id2,)).fetchone()
        expected = datetime.fromisoformat(row2["completed_at"])

        result = svc.last_run("browser")
        assert isinstance(result, datetime)
        assert result == expected

    def test_last_run_ignores_failed(self, tool_db):
        svc = IngestService(tool_db)
        ingest_id = svc.begin("browser")
        svc.fail(ingest_id, error="boom")

        assert svc.last_run("browser") is None

    def test_last_run_no_history(self, tool_db):
        svc = IngestService(tool_db)
        assert svc.last_run("browser") is None


class TestHistory:
    def test_history_returns_records(self, tool_db):
        svc = IngestService(tool_db)
        for _ in range(3):
            iid = svc.begin("browser")
            svc.complete(iid)
            time.sleep(0.01)

        records = svc.history("browser")
        assert len(records) == 3
        # Most recent first
        assert records[0]["started_at"] >= records[1]["started_at"]
        assert records[1]["started_at"] >= records[2]["started_at"]
        # Expected keys present
        for r in records:
            assert "id" in r
            assert "pipe" in r
            assert "status" in r
            assert "started_at" in r

    def test_history_respects_limit(self, tool_db):
        svc = IngestService(tool_db)
        for _ in range(5):
            iid = svc.begin("browser")
            svc.complete(iid)

        records = svc.history("browser", limit=2)
        assert len(records) == 2

    def test_history_filters_by_pipe(self, tool_db):
        svc = IngestService(tool_db)
        for pipe in ("browser", "gmail", "browser"):
            iid = svc.begin(pipe)
            svc.complete(iid)

        records = svc.history("browser")
        assert len(records) == 2
        assert all(r["pipe"] == "browser" for r in records)


class _MockRunner:
    """Minimal PipeRunner stand-in for IngestService.run_pipe tests."""

    def __init__(self, result: dict | None = None):
        self.config: dict = {}
        self._result = result or {"stage": "browser", "status": "completed"}
        self.calls: list[tuple] = []
        self.last_last_run: object = "NOT_SET"

    def run_pipe(self, pipe: str, on_progress=None, last_run=None) -> dict:
        self.calls.append((pipe,))
        self.last_last_run = last_run
        return self._result


class TestRunPipe:
    def test_run_pipe_creates_ingest_record(self, tool_db):
        svc = IngestService(tool_db)
        runner = _MockRunner()
        svc.run_pipe("browser", runner=runner)

        row = tool_db.execute("SELECT * FROM ingests WHERE pipe = 'browser'").fetchone()
        assert row is not None
        assert row["status"] == "completed"

    def test_run_pipe_delegates_to_runner(self, tool_db):
        svc = IngestService(tool_db)
        runner = _MockRunner()
        svc.run_pipe("browser", runner=runner)

        assert runner.calls == [("browser",)]

    def test_run_pipe_returns_runner_result(self, tool_db):
        svc = IngestService(tool_db)
        expected = {"stage": "browser", "status": "completed"}
        runner = _MockRunner(result=expected)

        result = svc.run_pipe("browser", runner=runner)
        assert result == expected

    def test_run_pipe_records_failure_on_error(self, tool_db):
        svc = IngestService(tool_db)
        runner = _MockRunner(result={"status": "error", "error": "boom"})
        svc.run_pipe("browser", runner=runner)

        row = tool_db.execute("SELECT * FROM ingests WHERE pipe = 'browser'").fetchone()
        assert row["status"] == "failed"
        parsed = json.loads(row["metadata"])
        assert parsed["error"] == "boom"

    def test_run_pipe_injects_last_run(self, tool_db):
        svc = IngestService(tool_db)
        # Create a prior completed ingest
        prior_id = svc.begin("browser")
        svc.complete(prior_id)
        prior_row = tool_db.execute("SELECT completed_at FROM ingests WHERE id = ?", (prior_id,)).fetchone()
        expected_ts = datetime.fromisoformat(prior_row["completed_at"])

        runner = _MockRunner()
        svc.run_pipe("browser", runner=runner)

        assert isinstance(runner.last_last_run, datetime)
        assert runner.last_last_run == expected_ts

    def test_run_pipe_injects_none_when_no_history(self, tool_db):
        svc = IngestService(tool_db)
        runner = _MockRunner()
        svc.run_pipe("browser", runner=runner)

        assert runner.last_last_run is None

    def test_run_pipe_passes_mode_and_trigger(self, tool_db):
        svc = IngestService(tool_db)
        runner = _MockRunner()
        svc.run_pipe("browser", mode="full", trigger="cli:ingest", runner=runner)

        row = tool_db.execute("SELECT * FROM ingests WHERE pipe = 'browser'").fetchone()
        assert row["mode"] == "full"
        assert row["trigger"] == "cli:ingest"


class _MockBatchRunner:
    """PipeRunner stand-in for IngestService.run_pipes tests."""

    def __init__(self, results: list[dict] | None = None, error: Exception | None = None):
        self._results = results or [{"stage": "browser", "status": "completed"}]
        self._error = error
        self.run_pipes_calls: list[tuple] = []

    def run_pipes(self, pipes, on_pipe_start=None, on_pipe_end=None, on_progress=None, pipe_hook=None) -> list[dict]:
        self.run_pipes_calls.append((pipes, on_pipe_start, on_pipe_end, on_progress, pipe_hook))
        if self._error:
            raise self._error
        return self._results


# ── TestRunPipes ────────────────────────────────────────────────────


class TestRunPipes:
    """IngestService.run_pipes wraps PipeRunner with FTS management."""

    def test_run_pipes_drops_fts_in_full_mode(self, tool_db):
        """Full-mode run drops FTS triggers before pipes and rebuilds after."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.return_value = []
        mock_db.check_fts_health.return_value = {}
        svc = IngestService(tool_db, get_db=lambda: mock_db)
        runner = _MockBatchRunner()

        svc.run_pipes(
            ["browser"],
            runner=runner,
            full_mode=True,
        )

        mock_db.drop_fts_triggers.assert_called_once()
        mock_db.rebuild_fts_indexes.assert_called_once()
        # drop must happen before run_pipes delegates
        assert len(runner.run_pipes_calls) == 1

    def test_run_pipes_rebuilds_fts_even_on_error(self, tool_db):
        """FTS rebuild fires in finally even when runner raises."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.return_value = []
        mock_db.check_fts_health.return_value = {}
        svc = IngestService(tool_db, get_db=lambda: mock_db)
        runner = _MockBatchRunner(error=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            svc.run_pipes(["browser"], runner=runner, full_mode=True)

        mock_db.rebuild_fts_indexes.assert_called_once()

    def test_run_pipes_skips_fts_in_incremental(self, tool_db):
        """Incremental run doesn't drop or rebuild FTS."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.return_value = []
        mock_db.check_fts_health.return_value = {}
        svc = IngestService(tool_db, get_db=lambda: mock_db)
        runner = _MockBatchRunner()

        svc.run_pipes(["browser"], runner=runner, full_mode=False)

        mock_db.drop_fts_triggers.assert_not_called()
        mock_db.rebuild_fts_indexes.assert_not_called()

    def test_run_pipes_delegates_to_runner(self, tool_db):
        """Callbacks are forwarded to runner.run_pipes."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.return_value = []
        mock_db.check_fts_health.return_value = {}
        svc = IngestService(tool_db, get_db=lambda: mock_db)
        runner = _MockBatchRunner()
        start_cb = MagicMock()
        end_cb = MagicMock()
        progress_cb = MagicMock()
        hook_cb = MagicMock()

        svc.run_pipes(
            ["browser", "chat"],
            runner=runner,
            full_mode=False,
            on_pipe_start=start_cb,
            on_pipe_end=end_cb,
            on_progress=progress_cb,
            pipe_hook=hook_cb,
        )

        assert len(runner.run_pipes_calls) == 1
        pipes, on_start, on_end, on_prog, hook = runner.run_pipes_calls[0]
        assert pipes == ["browser", "chat"]
        assert on_start is start_cb
        assert on_end is end_cb
        assert on_prog is progress_cb
        assert hook is hook_cb


# ── TestEnsureFtsHealth ─────────────────────────────────────────────


class TestEnsureFtsHealth:
    """FTS health check on startup lives in IngestService."""

    def test_ensure_fts_health_restores_triggers_incremental(self, tool_db):
        """Incremental mode restores missing triggers and probes health."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.return_value = ["files_fts_insert", "files_fts_delete"]
        mock_db.check_fts_health.return_value = {"files_fts": {"status": "ok"}}
        svc = IngestService(tool_db, get_db=lambda: mock_db)

        svc.ensure_fts_health(full_mode=False)

        mock_db.create_fts_triggers.assert_called_once()
        mock_db.check_fts_health.assert_called_once()

    def test_ensure_fts_health_skips_restore_in_full_mode(self, tool_db):
        """Full mode doesn't restore triggers (they'll be dropped anyway)."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.return_value = []
        mock_db.check_fts_health.return_value = {}
        svc = IngestService(tool_db, get_db=lambda: mock_db)

        svc.ensure_fts_health(full_mode=True)

        mock_db.create_fts_triggers.assert_not_called()
        # Health probe still runs
        mock_db.check_fts_health.assert_called_once()

    def test_ensure_fts_health_catches_operational_error(self, tool_db):
        """OperationalError in health check is caught, not raised."""
        mock_db = MagicMock()
        mock_db.check_fts_triggers.side_effect = sqlite3.OperationalError("corrupt")
        svc = IngestService(tool_db, get_db=lambda: mock_db)

        # Should not raise
        svc.ensure_fts_health(full_mode=False)
