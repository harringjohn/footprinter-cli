"""Tests for folder_stats pipeline stage — runner, registration, rollup.

Mirrors the structure of test_pipeline_access_resolution.py. The folder_stats
post-pipe wraps refresh_folder_counts() so that direct_file_count and
total_size_bytes on the folders table are refreshed at the end of every
ingest run.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from footprinter.ingest.adapters.protocol import PipeStatus


def _make_db_wrapper(conn):
    """Minimal Database-like wrapper exposing .conn for the runner."""

    class _DbWrapper:
        def __init__(self, connection):
            self.conn = connection
            self.db_path = ":memory:"

    return _DbWrapper(conn)


def _insert_folder(
    conn: sqlite3.Connection,
    folder_id: int,
    path: str,
    parent_folder_id: int | None = None,
) -> None:
    name = path.rstrip("/").rsplit("/", 1)[-1] or path
    conn.execute(
        "INSERT INTO folders (id, path, relative_path, name, parent_folder_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (folder_id, path, path, name, parent_folder_id),
    )


def _insert_file(
    conn: sqlite3.Connection,
    file_id: int,
    folder_id: int,
    path: str,
    size_bytes: int,
    status: str = "active",
) -> None:
    name = path.rsplit("/", 1)[-1]
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO files (id, source, name, path, account, folder_id, "
        "size_bytes, status, indexed_at) "
        "VALUES (?, 'local', ?, ?, 'work', ?, ?, ?, ?)",
        (file_id, name, path, folder_id, size_bytes, status, ts),
    )


@pytest.fixture
def conn(tool_db):
    """Full-schema database fixture (reused from tests/conftest.py)."""
    yield tool_db


class TestRunFolderStatsRunner:
    """Direct unit tests for run_folder_stats()."""

    def test_returns_completed_pipe_result(self, conn):
        """Runner returns PipeResult.completed with pipe='folder_stats'."""
        from footprinter.ingest.processing import run_folder_stats

        _insert_folder(conn, 1, "/Users/me/Work/proj")
        _insert_file(conn, 1, 1, "/Users/me/Work/proj/a.py", 100)
        conn.commit()

        db = _make_db_wrapper(conn)
        result = run_folder_stats(db)

        assert result.status == PipeStatus.COMPLETED
        assert result.stage == "folder_stats"

    def test_populates_direct_file_count_and_total_size(self, conn):
        """Direct counts + size sums are refreshed from the files table."""
        from footprinter.ingest.processing import run_folder_stats

        _insert_folder(conn, 1, "/Users/me/Work/proj")
        _insert_file(conn, 1, 1, "/Users/me/Work/proj/a.py", 100)
        _insert_file(conn, 2, 1, "/Users/me/Work/proj/b.py", 250)
        conn.commit()

        run_folder_stats(_make_db_wrapper(conn))

        row = conn.execute(
            "SELECT direct_file_count, total_size_bytes FROM folders WHERE id = 1"
        ).fetchone()
        assert row["direct_file_count"] == 2
        assert row["total_size_bytes"] == 350

    def test_excludes_removed_files(self, conn):
        """Files with status='removed' are not counted toward folder stats."""
        from footprinter.ingest.processing import run_folder_stats

        _insert_folder(conn, 1, "/Users/me/Work/proj")
        _insert_file(conn, 1, 1, "/Users/me/Work/proj/a.py", 100)
        _insert_file(conn, 2, 1, "/Users/me/Work/proj/old.py", 999, status="removed")
        conn.commit()

        run_folder_stats(_make_db_wrapper(conn))

        row = conn.execute(
            "SELECT direct_file_count, total_size_bytes FROM folders WHERE id = 1"
        ).fetchone()
        assert row["direct_file_count"] == 1
        assert row["total_size_bytes"] == 100

    def test_rolls_up_to_parent(self, conn):
        """Files in a child folder accumulate into the parent's total_*."""
        from footprinter.ingest.processing import run_folder_stats

        _insert_folder(conn, 1, "/Users/me/Work/proj")
        _insert_folder(conn, 2, "/Users/me/Work/proj/src", parent_folder_id=1)
        _insert_file(conn, 1, 2, "/Users/me/Work/proj/src/main.py", 500)
        conn.commit()

        run_folder_stats(_make_db_wrapper(conn))

        child = conn.execute(
            "SELECT direct_file_count, total_file_count, total_size_bytes "
            "FROM folders WHERE id = 2"
        ).fetchone()
        assert child["direct_file_count"] == 1
        assert child["total_file_count"] == 1
        assert child["total_size_bytes"] == 500

        parent = conn.execute(
            "SELECT direct_file_count, total_file_count, total_size_bytes "
            "FROM folders WHERE id = 1"
        ).fetchone()
        assert parent["direct_file_count"] == 0  # No files directly in parent
        assert parent["total_file_count"] == 1  # Rolled up from child
        assert parent["total_size_bytes"] == 500


class TestFolderStatsRegistration:
    """Pipeline registration: POST_PIPES list and orchestrator phase."""

    def test_registered_in_post_pipes(self):
        """folder_stats appears in the POST_PIPES list."""
        from footprinter.ingest.registry import POST_PIPES

        assert "folder_stats" in POST_PIPES

    def test_phase_registered_in_orchestrator(self):
        """DataPipelineOrchestrator registers folder_stats on its ProcessingPipeline."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator()
        try:
            assert orch.processing.is_processing_pipe("folder_stats") is True
        finally:
            if orch.db is not None:
                orch.db.close()

    def test_appears_in_every_resolved_pipeline(self):
        """get_pipelines() appends folder_stats to every pipeline (via POST_PIPES)."""
        from footprinter.ingest.registry import get_pipelines

        pipelines = get_pipelines({}, {})
        assert pipelines  # at minimum: 'local' and 'all'
        for name, stages in pipelines.items():
            assert "folder_stats" in stages, (
                f"folder_stats missing from pipeline '{name}': {stages}"
            )
