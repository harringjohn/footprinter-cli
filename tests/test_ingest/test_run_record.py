"""Tests for run record path helpers and persistence."""

import json
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# RED 1 — Path helpers
# ---------------------------------------------------------------------------


def test_get_run_logs_dir_returns_logs_subdir(monkeypatch, tmp_path):
    """get_run_logs_dir() returns ~/.footprinter/logs/."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    # Re-import to pick up env override
    from footprinter.paths import get_run_logs_dir

    result = get_run_logs_dir()
    assert result == tmp_path / "logs"
    assert result.exists()  # should create on access


def test_get_last_run_path_returns_json_file(monkeypatch, tmp_path):
    """get_last_run_path() returns ~/.footprinter/last_run.json."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.paths import get_last_run_path

    result = get_last_run_path()
    assert result == tmp_path / "last_run.json"


# ---------------------------------------------------------------------------
# RED 2 — Run record persistence
# ---------------------------------------------------------------------------


def test_save_run_record_writes_json(monkeypatch, tmp_path):
    """save_run_record() writes results + metadata to JSON."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import save_run_record

    results = [
        {"stage": "local_files", "status": "completed", "elapsed_seconds": 1.5, "files_indexed": 100},
    ]
    started = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    path = save_run_record(results, mode="incremental", started_at=started)

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["mode"] == "incremental"
    assert data["stages"] == results


def test_load_run_record_reads_json(monkeypatch, tmp_path):
    """load_run_record() reads back the saved record."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import load_run_record, save_run_record

    results = [{"stage": "browser", "status": "completed", "elapsed_seconds": 0.5}]
    started = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    save_run_record(results, mode="full", started_at=started)

    record = load_run_record()
    assert record is not None
    assert record["mode"] == "full"
    assert len(record["stages"]) == 1


def test_load_run_record_returns_none_when_missing(monkeypatch, tmp_path):
    """Returns None if no file exists."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import load_run_record

    assert load_run_record() is None


def test_save_run_record_includes_metadata(monkeypatch, tmp_path):
    """Record has started_at, completed_at, mode, total_elapsed_seconds, stages."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import save_run_record

    results = [
        {"stage": "a", "status": "completed", "elapsed_seconds": 2.0},
        {"stage": "b", "status": "completed", "elapsed_seconds": 3.0},
    ]
    started = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    path = save_run_record(results, mode="incremental", started_at=started)

    data = json.loads(path.read_text())
    assert "started_at" in data
    assert "completed_at" in data
    assert data["total_elapsed_seconds"] == 5.0
    assert data["mode"] == "incremental"
    assert len(data["stages"]) == 2


# ---------------------------------------------------------------------------
# RED 3 — Interrupted run record
# ---------------------------------------------------------------------------


def test_save_run_record_interrupted_flag(monkeypatch, tmp_path):
    """interrupted=True persists in JSON without mutating mode."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import save_run_record

    results = [
        {"stage": "local_files", "status": "completed", "elapsed_seconds": 1.5, "files_indexed": 50},
    ]
    started = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    path = save_run_record(results, mode="incremental", started_at=started, interrupted=True)

    data = json.loads(path.read_text())
    assert data["interrupted"] is True
    assert data["mode"] == "incremental"


def test_save_run_record_default_not_interrupted(monkeypatch, tmp_path):
    """Default call (no interrupted kwarg) stores interrupted=False."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import save_run_record

    results = [{"stage": "browser", "status": "completed", "elapsed_seconds": 0.5}]
    started = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    path = save_run_record(results, mode="full", started_at=started)

    data = json.loads(path.read_text())
    assert data["interrupted"] is False


# ---------------------------------------------------------------------------
# RED 4 — Integration: _run_with_logging saves on interrupt
# ---------------------------------------------------------------------------


def test_run_with_logging_saves_partial_on_interrupt(monkeypatch, tmp_path):
    """KeyboardInterrupt mid-pipeline saves a partial run record."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))

    completed_result = {
        "stage": "local_files",
        "status": "completed",
        "elapsed_seconds": 2.0,
        "files_indexed": 100,
    }

    class FakeOrchestrator:
        def run_pipes(self, stages, on_pipe_start=None, on_pipe_end=None, **kwargs):
            # First stage completes
            if on_pipe_start:
                on_pipe_start("local_files")
            if on_pipe_end:
                on_pipe_end("local_files", completed_result)
            # Second stage interrupted
            if on_pipe_start:
                on_pipe_start("browser")
            raise KeyboardInterrupt

        def close(self):
            pass

    from footprinter.cli.ingest import _run_with_logging

    with pytest.raises(KeyboardInterrupt):
        _run_with_logging(
            FakeOrchestrator(),
            pipes=["local_files", "browser"],
            mode="incremental",
            quiet=True,
        )

    # Verify run record was saved
    last_run = tmp_path / "last_run.json"
    assert last_run.exists()
    data = json.loads(last_run.read_text())
    assert data["interrupted"] is True
    assert data["mode"] == "incremental"
    assert len(data["stages"]) == 1
    assert data["stages"][0]["stage"] == "local_files"


# ---------------------------------------------------------------------------
# RED 5 — Interrupt with zero completed stages still saves
# ---------------------------------------------------------------------------


def test_run_with_logging_saves_empty_on_interrupt(monkeypatch, tmp_path):
    """KeyboardInterrupt on first stage (no completions) still saves run record."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))

    class FakeOrchestrator:
        def run_pipes(self, stages, on_pipe_start=None, on_pipe_end=None, **kwargs):
            # Interrupt immediately on first stage start
            if on_pipe_start:
                on_pipe_start("drive_folders")
            raise KeyboardInterrupt

        def close(self):
            pass

    from footprinter.cli.ingest import _run_with_logging

    with pytest.raises(KeyboardInterrupt):
        _run_with_logging(
            FakeOrchestrator(),
            pipes=["drive_folders"],
            mode="incremental",
            quiet=True,
        )

    # Verify run record was saved even with no completed stages
    last_run = tmp_path / "last_run.json"
    assert last_run.exists()
    data = json.loads(last_run.read_text())
    assert data["interrupted"] is True
    assert data["mode"] == "incremental"
    assert data["stages"] == []


# ---------------------------------------------------------------------------
# RED 6 — Run record session-window accumulation
# ---------------------------------------------------------------------------


def test_save_run_record_merges_within_session_window(monkeypatch, tmp_path):
    """Sequential saves within 10 minutes merge stages."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import load_run_record, save_run_record

    t1 = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    results1 = [{"stage": "local_folders", "status": "completed", "elapsed_seconds": 1.0}]
    save_run_record(results1, mode="incremental", started_at=t1)

    # Second save 2 minutes later — should merge
    t2 = t1 + timedelta(minutes=2)
    results2 = [{"stage": "project_links", "status": "completed", "elapsed_seconds": 0.5}]
    save_run_record(results2, mode="incremental", started_at=t2)

    record = load_run_record()
    assert len(record["stages"]) == 2
    stage_names = [s["stage"] for s in record["stages"]]
    assert "local_folders" in stage_names
    assert "project_links" in stage_names


def test_save_run_record_replaces_outside_session_window(monkeypatch, tmp_path):
    """Saves outside 10-minute window start fresh."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import load_run_record, save_run_record

    t1 = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    results1 = [{"stage": "local_folders", "status": "completed", "elapsed_seconds": 1.0}]
    save_run_record(results1, mode="incremental", started_at=t1)

    # Second save 30 minutes later — should replace
    t2 = t1 + timedelta(minutes=30)
    results2 = [{"stage": "browser", "status": "completed", "elapsed_seconds": 0.5}]
    save_run_record(results2, mode="full", started_at=t2)

    record = load_run_record()
    assert len(record["stages"]) == 1
    assert record["stages"][0]["stage"] == "browser"
    assert record["mode"] == "full"


def test_save_run_record_merge_updates_metadata(monkeypatch, tmp_path):
    """Merged record keeps original started_at but updates completed_at and total_elapsed."""
    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
    from footprinter.ingest.run_record import load_run_record, save_run_record

    t1 = datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc)
    results1 = [{"stage": "local_folders", "status": "completed", "elapsed_seconds": 2.0}]
    save_run_record(results1, mode="incremental", started_at=t1)

    t2 = t1 + timedelta(minutes=1)
    results2 = [{"stage": "project_links", "status": "completed", "elapsed_seconds": 3.0}]
    save_run_record(results2, mode="incremental", started_at=t2)

    record = load_run_record()
    assert record["started_at"] == t1.isoformat()  # Keeps original start
    assert record["total_elapsed_seconds"] == 5.0  # Sums both
