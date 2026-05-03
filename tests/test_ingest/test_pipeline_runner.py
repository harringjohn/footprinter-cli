"""
Tests for PipeRunner.

Validates stage dispatch (adapter vs processing vs unknown), error type
classification, stage iteration with fatal/non-fatal handling, and
pipeline name resolution.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from footprinter.ingest.adapters.protocol import PipeContext, PipeResult

# ── Helpers ──────────────────────────────────────────────────────────

# Minimal adapter registry + pipelines for tests
_FAKE_ADAPTER_REGISTRY = {
    "local_folders": MagicMock,
    "local_files": MagicMock,
    "browser": MagicMock,
    "chat": MagicMock,
}

_FAKE_PIPELINES = {
    "local": ["local_folders", "local_files", "browser", "chat"],
    "all": ["local_folders", "local_files", "browser", "chat"],
}

_FAKE_ALL_SOURCES = ["local_folders", "local_files", "browser", "chat"]


def _make_runner(**overrides):
    """Build a PipeRunner with sensible defaults for testing."""
    from footprinter.ingest.pipe_runner import PipeRunner

    defaults = {
        "processing": MagicMock(),
        "get_db": MagicMock(),
        "config": {"directories": ["~/Work"]},
        "config_path": "/dev/null",
        "adapter_registry": dict(_FAKE_ADAPTER_REGISTRY),
        "pipelines": dict(_FAKE_PIPELINES),
        "all_pipes": list(_FAKE_ALL_SOURCES),
        "user_pipes": list(_FAKE_ALL_SOURCES),
    }
    defaults.update(overrides)
    return PipeRunner(**defaults)


# ── TestPipeRunnerInit ───────────────────────────────────────────


class TestPipeRunnerInit:
    """PipeRunner stores its dependencies on construction."""

    def test_stores_processing_pipeline(self):
        processing = MagicMock()
        runner = _make_runner(processing=processing)
        assert runner.processing is processing

    def test_stores_get_db_callable(self):
        get_db = MagicMock()
        runner = _make_runner(get_db=get_db)
        assert runner._get_db is get_db

    def test_stores_config(self):
        config = {"directories": ["~/Personal"]}
        runner = _make_runner(config=config)
        assert runner.config is config

    def test_stores_config_path(self):
        runner = _make_runner(config_path="/etc/footprinter.yaml")
        assert runner.config_path == "/etc/footprinter.yaml"

    def test_full_mode_defaults_false(self):
        runner = _make_runner()
        assert runner.full_mode is False

    def test_stores_adapter_registry(self):
        """PipeRunner accepts and stores adapter_registry."""
        registry = {"browser": MagicMock}
        runner = _make_runner(adapter_registry=registry)
        assert runner.adapter_registry is registry

    def test_stores_pipelines(self):
        """PipeRunner accepts and stores pipelines dict."""
        pipelines = {"local": ["browser"]}
        runner = _make_runner(pipelines=pipelines)
        assert runner.pipelines is pipelines

    def test_stores_all_sources(self):
        """PipeRunner accepts and stores all_pipes list."""
        all_pipes = ["browser", "chat"]
        runner = _make_runner(all_pipes=all_pipes)
        assert runner.all_pipes is all_pipes


# ── TestRunStage ─────────────────────────────────────────────────────


class TestRunStage:
    """Core dispatch: adapter → processing → unknown, plus error handling."""

    def test_dispatches_adapter_stage(self):
        """Adapter stage calls adapter_cls().run(db, ctx) and returns dict with elapsed_seconds."""
        mock_db = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser", urls=500)
        mock_cls = MagicMock(return_value=mock_adapter)

        runner = _make_runner(
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"browser": mock_cls},
        )
        runner.processing.is_processing_pipe.return_value = False

        result = runner.run_pipe("browser")

        mock_cls.assert_called_once()
        call_args = mock_adapter.run.call_args
        call_db, call_ctx = call_args[0]
        assert call_db is mock_db
        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.source_config["directories"] == ["~/Work"]
        assert result["status"] == "completed"
        assert "elapsed_seconds" in result

    def test_dispatches_processing_stage(self):
        """Processing stage calls processing.run_phase(stage, db) and returns dict."""
        mock_db = MagicMock()
        processing = MagicMock()
        processing.is_processing_pipe.return_value = True
        processing.run_phase.return_value = PipeResult.completed("drive_links", linked=10)

        runner = _make_runner(
            processing=processing,
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={},  # Not in adapter registry
        )

        result = runner.run_pipe("drive_links")

        processing.run_phase.assert_called_once_with("drive_links", mock_db)
        assert result["status"] == "completed"

    def test_unknown_stage_returns_error(self):
        """Unknown stage returns status=error with descriptive message."""
        runner = _make_runner(adapter_registry={})
        runner.processing.is_processing_pipe.return_value = False

        result = runner.run_pipe("nonexistent")

        assert result["status"] == "error"
        assert "Unknown pipe" in result["error"]

    def test_uninstalled_connector_stage_returns_skip(self):
        """Stage belonging to an uninstalled connector returns skip with install hint."""
        runner = _make_runner(
            adapter_registry={},  # No gmail adapter
            connector_pipe_map={"gmail": "google", "drive_folders": "google", "drive_files": "google"},
        )
        runner.processing.is_processing_pipe.return_value = False

        result = runner.run_pipe("gmail")

        assert result["status"] == "skipped"
        assert "not installed" in result["reason"]
        assert "fp connect install google" in result["hint"]

    def test_import_error_returns_skipped(self):
        """ImportError caught → status=skipped, error_type=missing_dependency."""
        mock_cls = MagicMock(side_effect=ImportError("no module 'google'"))
        runner = _make_runner(adapter_registry={"gmail": mock_cls})

        result = runner.run_pipe("gmail")

        assert result["status"] == "skipped"
        assert result["error_type"] == "missing_dependency"

    def test_database_error_returns_error(self):
        """sqlite3.OperationalError → status=error, error_type=database."""
        mock_cls = MagicMock(side_effect=sqlite3.OperationalError("database is locked"))
        runner = _make_runner(adapter_registry={"browser": mock_cls})

        result = runner.run_pipe("browser")

        assert result["status"] == "error"
        assert result["error_type"] == "database"
        assert "database is locked" in result["error"]

    def test_config_error_returns_error(self):
        """FileNotFoundError → status=error, error_type=config."""
        mock_cls = MagicMock(side_effect=FileNotFoundError("credentials.json not found"))
        runner = _make_runner(adapter_registry={"gmail": mock_cls})

        result = runner.run_pipe("gmail")

        assert result["status"] == "error"
        assert result["error_type"] == "config"

    def test_runtime_error_returns_error(self):
        """Generic Exception → status=error, error_type=runtime."""
        mock_cls = MagicMock(side_effect=RuntimeError("unexpected"))
        runner = _make_runner(adapter_registry={"local_files": mock_cls})

        result = runner.run_pipe("local_files")

        assert result["status"] == "error"
        assert result["error_type"] == "runtime"

    def test_elapsed_seconds_always_set(self):
        """Result always has elapsed_seconds as a float."""
        runner = _make_runner(adapter_registry={})
        runner.processing.is_processing_pipe.return_value = False

        result = runner.run_pipe("nonexistent")

        assert "elapsed_seconds" in result
        assert isinstance(result["elapsed_seconds"], float)

    def test_does_not_mutate_shared_config(self):
        """run_pipe must not inject config_path/full_mode into the shared config dict."""
        mock_db = MagicMock()
        config = {"directories": ["~/Work"]}
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser")
        mock_cls = MagicMock(return_value=mock_adapter)

        runner = _make_runner(
            config=config,
            config_path="/my/config.yaml",
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"browser": mock_cls},
        )
        runner.full_mode = True
        runner.processing.is_processing_pipe.return_value = False

        runner.run_pipe("browser")

        assert "config_path" not in config
        assert "full_mode" not in config

    def test_adapter_receives_config_path_and_full_mode(self):
        """Adapter's run() receives a PipeContext with config_path and full_mode."""
        mock_db = MagicMock()
        config = {"directories": ["~/Work"]}
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser")
        mock_cls = MagicMock(return_value=mock_adapter)

        runner = _make_runner(
            config=config,
            config_path="/my/config.yaml",
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"browser": mock_cls},
        )
        runner.full_mode = True
        runner.processing.is_processing_pipe.return_value = False

        runner.run_pipe("browser")

        call_args = mock_adapter.run.call_args
        call_ctx = call_args[0][1]
        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.config_path == "/my/config.yaml"
        assert call_ctx.full_mode is True
        assert call_ctx.source_config["directories"] == ["~/Work"]


# ── TestRunStages ────────────────────────────────────────────────────


class TestRunStages:
    """Stage iteration, filtering, callbacks, and fatal error handling."""

    def test_runs_stages_in_order(self):
        """Stages execute in list order."""
        call_order = []
        runner = _make_runner()

        def mock_run_stage(stage, **kwargs):
            call_order.append(stage)
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            results = runner.run_pipes(["local_folders", "local_files", "browser"])

        assert call_order == ["local_folders", "local_files", "browser"]
        assert len(results) == 3

    def test_unknown_stage_raises_valueerror(self):
        """Unknown stage name raises ValueError listing the bad name and valid stages."""
        runner = _make_runner()

        with pytest.raises(ValueError, match="nonexistent_stage") as exc_info:
            runner.run_pipes(["nonexistent_stage"])

        assert "Valid pipes" in str(exc_info.value)

    def test_unknown_pipe_error_excludes_post_pipes(self):
        """Error message reports user_pipes only — POST_PIPES are suppressed."""
        user_pipes = ["local_folders", "local_files", "browser", "chat"]
        runner = _make_runner(
            user_pipes=user_pipes,
            all_pipes=user_pipes + ["access_resolution"],
        )

        with pytest.raises(ValueError) as exc_info:
            runner.run_pipes(["nonsense"])

        message = str(exc_info.value)
        assert "nonsense" in message
        assert "local_folders" in message
        assert "chat" in message
        assert "access_resolution" not in message

    def test_future_stage_raises_valueerror(self):
        """Descoped future stages are rejected by --stages validation."""
        runner = _make_runner()

        with pytest.raises(ValueError, match="summaries"):
            runner.run_pipes(["summaries"])

    def test_mix_of_valid_and_invalid_raises_before_running(self):
        """Validation rejects before any stage executes."""
        runner = _make_runner()

        with patch.object(runner, "run_pipe") as mock_run_stage:
            with pytest.raises(ValueError, match="bad_stage"):
                runner.run_pipes(["local_folders", "bad_stage"])

        mock_run_stage.assert_not_called()

    def test_all_valid_stages_still_work(self):
        """Valid stage names execute normally — no regression."""
        runner = _make_runner()

        def mock_run_stage(stage, **kwargs):
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            results = runner.run_pipes(["local_folders", "browser"])

        assert [r["stage"] for r in results] == ["local_folders", "browser"]

    def test_calls_on_stage_start_callback(self):
        """Callback fires before each stage."""
        runner = _make_runner()
        starts = []

        def mock_run_stage(stage, **kwargs):
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            runner.run_pipes(
                ["local_folders", "browser"],
                on_pipe_start=lambda s: starts.append(s),
            )

        assert starts == ["local_folders", "browser"]

    def test_calls_on_stage_end_callback(self):
        """Callback fires after each stage with result."""
        runner = _make_runner()
        ends = []

        def mock_run_stage(stage, **kwargs):
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            runner.run_pipes(
                ["local_folders"],
                on_pipe_end=lambda s, r: ends.append((s, r["status"])),
            )

        assert len(ends) == 1
        assert ends[0] == ("local_folders", "completed")

    def test_fatal_database_error_stops_pipeline(self):
        """error_type=database stops iteration."""
        runner = _make_runner()

        def mock_run_stage(stage, **kwargs):
            if stage == "local_folders":
                return {
                    "stage": stage,
                    "status": "error",
                    "error": "db locked",
                    "error_type": "database",
                    "elapsed_seconds": 0.1,
                }
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            results = runner.run_pipes(["local_folders", "local_files"])

        assert len(results) == 1
        assert results[0]["error_type"] == "database"

    def test_fatal_config_error_stops_pipeline(self):
        """error_type=config stops iteration."""
        runner = _make_runner()

        def mock_run_stage(stage, **kwargs):
            if stage == "browser":
                return {
                    "stage": stage,
                    "status": "error",
                    "error": "file not found",
                    "error_type": "config",
                    "elapsed_seconds": 0.1,
                }
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            results = runner.run_pipes(["browser", "chat"])

        assert len(results) == 1
        assert results[0]["error_type"] == "config"

    def test_runtime_error_continues_pipeline(self):
        """error_type=runtime does not stop."""
        runner = _make_runner()

        def mock_run_stage(stage, **kwargs):
            if stage == "browser":
                return {
                    "stage": stage,
                    "status": "error",
                    "error": "timeout",
                    "error_type": "runtime",
                    "elapsed_seconds": 0.1,
                }
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            results = runner.run_pipes(["browser", "chat"])

        assert len(results) == 2
        assert results[0]["status"] == "error"
        assert results[1]["status"] == "completed"


# ── TestStageProgressCallback ───────────────────────────────────────


class TestStageProgressCallback:
    """on_progress callback is threaded into PipeContext."""

    def test_progress_callback_injected_into_context(self):
        """run_pipes(on_progress=cb) → adapter receives PipeContext with on_progress=cb."""
        mock_db = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser", urls=10)
        mock_cls = MagicMock(return_value=mock_adapter)
        progress_cb = MagicMock()

        runner = _make_runner(
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"browser": mock_cls},
        )
        runner.processing.is_processing_pipe.return_value = False

        runner.run_pipes(["browser"], on_progress=progress_cb)

        call_ctx = mock_adapter.run.call_args[0][1]
        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.on_progress is progress_cb

    def test_no_progress_callback_by_default(self):
        """Without on_progress, PipeContext.on_progress is None."""
        mock_db = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser", urls=10)
        mock_cls = MagicMock(return_value=mock_adapter)

        runner = _make_runner(
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"browser": mock_cls},
        )
        runner.processing.is_processing_pipe.return_value = False

        runner.run_pipes(["browser"])

        call_ctx = mock_adapter.run.call_args[0][1]
        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.on_progress is None


class TestStageScanRoots:
    """scan_roots is threaded into PipeContext."""

    def test_scan_roots_injected_into_context(self):
        """run_pipes(scan_roots=[...]) → adapter receives PipeContext with scan_roots."""
        mock_db = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("local_folders")
        mock_cls = MagicMock(return_value=mock_adapter)

        runner = _make_runner(
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"local_folders": mock_cls},
        )
        runner.processing.is_processing_pipe.return_value = False

        runner.run_pipes(["local_folders"], scan_roots=["/tmp/only-this"])

        call_ctx = mock_adapter.run.call_args[0][1]
        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.scan_roots == ["/tmp/only-this"]

    def test_no_scan_roots_by_default(self):
        """Without scan_roots, PipeContext.scan_roots is None — preserves fp ingest semantics."""
        mock_db = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("local_folders")
        mock_cls = MagicMock(return_value=mock_adapter)

        runner = _make_runner(
            get_db=MagicMock(return_value=mock_db),
            adapter_registry={"local_folders": mock_cls},
        )
        runner.processing.is_processing_pipe.return_value = False

        runner.run_pipes(["local_folders"])

        call_ctx = mock_adapter.run.call_args[0][1]
        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.scan_roots is None


# ── TestPipeRunnerNoFTS ────────────────────────────────────────────


class TestPipeRunnerNoFTS:
    """PipeRunner.run_pipes must contain no FTS-related code."""

    def test_run_pipes_does_not_call_fts_methods(self):
        """Even in full mode, PipeRunner never touches FTS methods on db."""
        mock_db = MagicMock()
        runner = _make_runner(get_db=MagicMock(return_value=mock_db))
        runner.full_mode = True

        def mock_run_stage(stage, **kwargs):
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(runner, "run_pipe", side_effect=mock_run_stage):
            runner.run_pipes(["local_folders"])

        mock_db.drop_fts_triggers.assert_not_called()
        mock_db.rebuild_fts_indexes.assert_not_called()
        mock_db.check_fts_triggers.assert_not_called()
        mock_db.check_fts_health.assert_not_called()
        mock_db.create_fts_triggers.assert_not_called()
