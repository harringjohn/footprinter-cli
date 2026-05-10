"""Tests for fp ingest — pipeline execution module.

Validates:
  1. fp ingest --help lists available subcommands and pipeline flags
  2. Pipeline routing: bare ingest, --pipe, --full, --rebuild-vectors
  3. Status: get_status() + print_status() or JSON output
  4. Import: ChatIndexer.upload() routing
  5. Refresh: validates source, runs correct stages in full_mode
  6. Gated commands: classify/backfill/purge/report not registered
  7. fp run removed: backward-compat alias stripped
"""

import fcntl
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest import run_fp

# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestIngestHelp:
    """fp ingest --help exits 0 and lists subcommands + pipeline flags."""

    def test_help_exits_zero(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        assert code == 0

    def test_help_lists_subcommands(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        output = stdout + stderr
        for name in ("status", "import", "refresh"):
            assert name in output, f"'{name}' not in fp ingest --help"

    def test_help_excludes_gated_commands(self):
        """classify/backfill/purge/report are excluded from v1.0."""
        stdout, stderr, code = run_fp("ingest", "--help")
        output = stdout + stderr
        for name in ("classify", "backfill", "purge", "report"):
            assert name not in output, f"'{name}' should not be in fp ingest --help"

    def test_help_shows_pipe_flag(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        output = stdout + stderr
        assert "--pipe" in output, "'--pipe' not in fp ingest --help"
        assert "--full" in output, "'--full' not in fp ingest --help"
        assert "--quiet" in output, "'--quiet' not in fp ingest --help"
        for removed in ("--stages", "--pipeline", "--connector"):
            assert removed not in output, f"'{removed}' should be removed from fp ingest --help"

    def test_pipeline_flag_rejected(self):
        """--pipeline should be rejected as an unrecognized argument."""
        _, _, code = run_fp("ingest", "--pipeline", "local", "--quiet")
        assert code != 0

    def test_source_flag_rejected(self):
        """--source should be rejected as an unrecognized argument."""
        _, _, code = run_fp("ingest", "--source", "google", "--quiet")
        assert code != 0


# ---------------------------------------------------------------------------
# 2. Pipeline routing
# ---------------------------------------------------------------------------


class TestIngestPipeline:
    """Bare ingest, --pipe, --full, --rebuild-vectors."""

    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_bare_ingest_calls_all_pipeline(self, mock_orch_cls, _print):
        mock_orch = MagicMock()
        mock_orch.run_pipeline.return_value = []
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "--quiet")

        mock_orch.run_pipeline.assert_called_once()
        assert mock_orch.run_pipeline.call_args[0][0] == "all"
        mock_orch.close.assert_called()

    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_pipe_flag(self, mock_orch_cls, _print):
        mock_orch = MagicMock()
        mock_orch.run_pipes.return_value = []
        mock_orch.runner.validate_pipes.return_value = None
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "--pipe", "local_files", "--quiet")

        mock_orch.runner.validate_pipes.assert_called_once_with(["local_files"])
        mock_orch.run_pipes.assert_called_once()
        assert mock_orch.run_pipes.call_args[0][0] == ["local_files"]

    def test_stages_flag_rejected(self):
        """--stages should be rejected (removed backward-compat alias)."""
        _, _, code = run_fp("ingest", "--stages", "local_files", "--quiet")
        assert code != 0

    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_full_flag_sets_full_mode(self, mock_orch_cls, _print):
        mock_orch = MagicMock()
        mock_orch.run_pipeline.return_value = []
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "--full", "--quiet")

        assert mock_orch.full_mode is True

    @patch("footprinter.ingest.cli._rebuild_vectors")
    def test_rebuild_vectors(self, mock_rebuild):
        run_fp("ingest", "--rebuild-vectors")

        mock_rebuild.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Status
# ---------------------------------------------------------------------------


class TestIngestStatus:
    """fp ingest status calls get_status() and supports --json."""

    @patch("footprinter.ingest.status.print_status")
    @patch("footprinter.ingest.status.get_status")
    def test_status_calls_get_status(self, mock_get, _print, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_get.return_value = {"files_total": 42}

        with patch("footprinter.paths.get_db_path", return_value=db_file):
            run_fp("ingest", "status")

        mock_get.assert_called_once()

    @patch("footprinter.ingest.status.get_status")
    def test_status_json_output(self, mock_get, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_get.return_value = {"files_total": 42}

        with patch("footprinter.paths.get_db_path", return_value=db_file):
            stdout, stderr, code = run_fp("ingest", "status", "--json")

        assert code == 0
        data = json.loads(stdout)
        assert data["files_total"] == 42


# ---------------------------------------------------------------------------
# 4. Import
# ---------------------------------------------------------------------------


class TestIngestImport:
    """fp ingest import <path> routes to ChatIndexer.upload()."""

    @patch("footprinter.ingest.chat_indexer.ChatIndexer")
    @patch("footprinter.ingest.database.Database")
    @patch("footprinter.paths.get_db_path")
    def test_import_calls_upload(self, mock_db_path, mock_db_cls, mock_mgr_cls):
        mock_db_path.return_value = "/tmp/test.db"
        mock_mgr = MagicMock()
        mock_mgr.upload.return_value = {
            "status": "success",
            "chats_added": 5,
            "chats_updated": 0,
            "messages_imported": 50,
        }
        mock_mgr_cls.return_value = mock_mgr

        stdout, stderr, code = run_fp("ingest", "import", "/path/to/export.zip")

        assert code == 0
        mock_mgr.upload.assert_called_once()

    @patch("footprinter.ingest.chat_indexer.ChatIndexer")
    @patch("footprinter.ingest.database.Database")
    @patch("footprinter.paths.get_db_path")
    def test_import_duplicate_shows_warning(self, mock_db_path, mock_db_cls, mock_mgr_cls):
        mock_db_path.return_value = "/tmp/test.db"
        mock_mgr = MagicMock()
        mock_mgr.upload.return_value = {
            "status": "duplicate",
            "previous_upload": {"uploaded_at": "2024-01-01"},
        }
        mock_mgr_cls.return_value = mock_mgr

        stdout, stderr, code = run_fp("ingest", "import", "/path/to/export.zip")

        assert code == 0
        output = stdout + stderr
        assert "already" in output.lower() or "duplicate" in output.lower()

    def test_import_missing_path_exits_nonzero(self):
        _, _, code = run_fp("ingest", "import")
        assert code != 0


# ---------------------------------------------------------------------------
# 5. Refresh
# ---------------------------------------------------------------------------


class TestIngestRefresh:
    """fp ingest refresh <source> validates source and defaults to incremental mode."""

    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_refresh_local_runs_correct_stages(self, mock_orch_cls, _print):
        # Realistic registry shape — includes access_resolution post-processing pipe.
        mock_orch = MagicMock()
        mock_orch.run_refresh.return_value = []
        mock_orch.refresh_pipes = {
            "local": ["local_folders", "local_files", "access_resolution"],
        }
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "refresh", "local")

        assert mock_orch.full_mode is False
        # CLI must dispatch via run_refresh (which bypasses the POST_PIPES guard).
        mock_orch.run_refresh.assert_called_once()
        assert mock_orch.run_refresh.call_args[0][0] == "local"
        # run_pipes must NOT be called with a list containing access_resolution
        # (it would raise ValueError — this is the bug we're fixing).
        if mock_orch.run_pipes.called:
            called_pipes = mock_orch.run_pipes.call_args[0][0]
            assert "access_resolution" not in called_pipes, (
                "refresh must not dispatch access_resolution through run_pipes — "
                "run_pipes rejects POST_PIPES"
            )

    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_refresh_full_flag_sets_full_mode(self, mock_orch_cls, _print):
        mock_orch = MagicMock()
        mock_orch.run_refresh.return_value = []
        mock_orch.refresh_pipes = {
            "local": ["local_folders", "local_files", "access_resolution"],
        }
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "refresh", "local", "--full")

        assert mock_orch.full_mode is True
        mock_orch.run_refresh.assert_called_once()

    def test_refresh_invalid_source_exits_nonzero(self):
        _, _, code = run_fp("ingest", "refresh", "bogus")
        assert code != 0

    def test_refresh_missing_source_exits_nonzero(self):
        _, _, code = run_fp("ingest", "refresh")
        assert code != 0

    def test_refresh_help_no_google_references(self):
        """Refresh help text should not mention gmail or drive as example sources."""
        stdout, _, code = run_fp("ingest", "refresh", "--help")
        assert code == 0
        for banned in ("gmail", "drive"):
            assert banned not in stdout.lower(), f"fp ingest refresh --help should not contain '{banned}'"


# ---------------------------------------------------------------------------
# 6. Startup banner
# ---------------------------------------------------------------------------


class TestIngestSourceBanner:
    """Startup banner showing active/inactive sources."""

    def _google_connectors(self):
        from footprinter.connectors import AuthType, ConnectorSpec

        return {
            "google": ConnectorSpec(
                name="google",
                extra="google",
                description="Google Drive and Gmail integration",
                pipes=("drive_folders", "drive_files", "gmail"),
                probe_module="google.auth",
                config_sections=("google_drive", "gmail"),
                setup_hook="footprinter.cli.google_setup.run_google_setup",
                remove_packages=(),
                auth_type=AuthType.OAUTH2,
            )
        }

    def test_banner_shows_connected_connector(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"google_drive": {"enabled": True}, "gmail": {"enabled": True}}

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.connectors.discover_connectors", return_value=self._google_connectors()),
        ):
            _print_source_banner(config, console=test_console)

        output = buf.getvalue()
        assert "Google" in output or "google" in output
        assert "\u2713" in output  # checkmark

    def test_banner_shows_unconnected_hint(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {}  # nothing configured

        with (
            patch("footprinter.connectors.is_installed", return_value=False),
            patch("footprinter.connectors.discover_connectors", return_value=self._google_connectors()),
        ):
            _print_source_banner(config, console=test_console)

        output = buf.getvalue()
        assert "fp connect" in output

    def test_banner_hides_local_files_when_no_directories(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {}  # no directories key

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, console=test_console)

        output = buf.getvalue()
        # "Local files" should not have a green checkmark
        for line in output.splitlines():
            if "Local files" in line:
                assert "\u2713" not in line, "Local files should be inactive when directories not configured"
                break

    def test_banner_hides_browser_when_no_browsers(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"]}  # directories but no browsers

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, console=test_console)

        output = buf.getvalue()
        for line in output.splitlines():
            if "Browser history" in line:
                assert "\u2713" not in line, "Browser history should be inactive when browsers not configured"
                break

    def test_banner_shows_both_when_configured(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "browsers": ["safari"]}

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, console=test_console)

        output = buf.getvalue()
        local_found = False
        browser_found = False
        for line in output.splitlines():
            if "Local files" in line and "\u2713" in line:
                local_found = True
            if "Browser history" in line and "\u2713" in line:
                browser_found = True
        assert local_found, "Local files should show checkmark when directories configured"
        assert browser_found, "Browser history should show checkmark when browsers configured"

    def test_banner_pipe_filter_local_files_only(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "browsers": ["safari"]}

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, pipes=["local_files"], console=test_console)

        output = buf.getvalue()
        assert "Local files" in output
        assert "✓" in output  # at least one checkmark for the active source
        assert "Browser history" not in output, "Browser history should be hidden when not in --pipe list"

    def test_banner_pipe_filter_browser_only(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "browsers": ["safari"]}

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, pipes=["browser"], console=test_console)

        output = buf.getvalue()
        assert "Browser history" in output
        assert "✓" in output
        assert "Local files" not in output, "Local files should be hidden when not in --pipe list"

    def test_banner_pipe_filter_local_folders_alias(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "browsers": ["safari"]}

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, pipes=["local_folders"], console=test_console)

        output = buf.getvalue()
        assert "Local files" in output, "local_folders should map to the Local files display"
        assert "✓" in output
        assert "Browser history" not in output

    def test_banner_pipe_filter_multiple_pipes(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "browsers": ["safari"]}

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, pipes=["local_files", "browser"], console=test_console)

        output = buf.getvalue()
        local_active = any("Local files" in line and "✓" in line for line in output.splitlines())
        browser_active = any("Browser history" in line and "✓" in line for line in output.splitlines())
        assert local_active
        assert browser_active

    def test_banner_pipe_filter_hides_unrequested_connector(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "google_drive": {"enabled": True}}

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.connectors.discover_connectors", return_value=self._google_connectors()),
        ):
            _print_source_banner(config, pipes=["local_files"], console=test_console)

        output = buf.getvalue()
        assert "Google" not in output and "google" not in output, (
            "Google connector line should be hidden when none of its pipes are in --pipe"
        )

    def test_banner_pipe_filter_shows_requested_connector(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"google_drive": {"enabled": True}, "gmail": {"enabled": True}}

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.connectors.discover_connectors", return_value=self._google_connectors()),
        ):
            _print_source_banner(config, pipes=["drive_files"], console=test_console)

        output = buf.getvalue()
        assert "Google" in output or "google" in output
        assert "✓" in output

    def test_banner_no_pipe_filter_preserves_today_behavior(self):
        from io import StringIO

        from rich.console import Console

        from footprinter.cli.ingest import _print_source_banner

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False)
        config = {"directories": ["~/Work"], "browsers": ["safari"]}

        with patch("footprinter.connectors.is_installed", return_value=False):
            _print_source_banner(config, pipes=None, console=test_console)

        output = buf.getvalue()
        local_active = any("Local files" in line and "✓" in line for line in output.splitlines())
        browser_active = any("Browser history" in line and "✓" in line for line in output.splitlines())
        assert local_active, "pipes=None must show Local files (regression guard)"
        assert browser_active, "pipes=None must show Browser history (regression guard)"


# ---------------------------------------------------------------------------
# 7. Per-stage result reporting
# ---------------------------------------------------------------------------


class TestIngestStageReporting:
    """Per-stage result line with counts after each stage."""

    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_stage_result_line_printed(self, mock_orch_cls, _print, _save):
        """on_end prints a detail line (not just spinner update)."""
        mock_orch = MagicMock()
        mock_orch.config = {}

        def fake_run(pipeline_name, on_pipe_start=None, on_pipe_end=None, **kwargs):
            if on_pipe_start:
                on_pipe_start("browser")
            if on_pipe_end:
                on_pipe_end(
                    "browser",
                    {
                        "stage": "browser",
                        "status": "completed",
                        "elapsed_seconds": 1.5,
                        "urls_indexed": 42,
                    },
                )

        mock_orch.run_pipeline.side_effect = fake_run
        mock_orch_cls.return_value = mock_orch

        stdout, stderr, code = run_fp("ingest")

        output = stdout + stderr
        # Should contain detail string from _stage_detail_string
        assert "42" in output
        assert "urls" in output


# ---------------------------------------------------------------------------
# 7b. Run record log message
# ---------------------------------------------------------------------------


class TestRunRecordLogMessage:
    """Log message after save_run_record references the record path, not the log."""

    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_log_references_run_record_not_log_file(
        self,
        mock_orch_cls,
        _print,
        mock_save,
        caplog,
    ):
        """INFO log says 'Run record saved to …/last_run.json', not .log."""
        mock_orch = MagicMock()
        mock_orch.config = {}
        mock_orch.run_pipeline.return_value = None
        mock_orch_cls.return_value = mock_orch

        fake_record_path = Path.home() / ".footprinter" / "last_run.json"
        mock_save.return_value = fake_record_path

        with caplog.at_level(logging.INFO, logger="footprinter"):
            run_fp("ingest")

        saved_msgs = [r.message for r in caplog.records if "Run record saved" in r.message]
        assert saved_msgs, "Expected 'Run record saved' log message"
        msg = saved_msgs[0]
        assert "last_run.json" in msg
        assert ".log" not in msg


# ---------------------------------------------------------------------------
# 7c. Run record log message on KeyboardInterrupt
# ---------------------------------------------------------------------------


class TestRunRecordLogMessageOnInterrupt:
    """Log message after save_run_record on KeyboardInterrupt references the record path."""

    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_log_references_run_record_on_interrupt(
        self,
        mock_orch_cls,
        mock_save,
        caplog,
    ):
        """INFO log says 'Run record saved to …/last_run.json' even when interrupted."""
        mock_orch = MagicMock()
        mock_orch.config = {}
        mock_orch.run_pipeline.side_effect = KeyboardInterrupt
        mock_orch_cls.return_value = mock_orch

        fake_record_path = Path.home() / ".footprinter" / "last_run.json"
        mock_save.return_value = fake_record_path

        with caplog.at_level(logging.INFO, logger="footprinter"):
            _out, _err, code = run_fp("ingest")

        saved_msgs = [r.message for r in caplog.records if "Run record saved" in r.message]
        assert saved_msgs, "Expected 'Run record saved' log message on interrupt"
        msg = saved_msgs[0]
        assert "last_run.json" in msg


# ---------------------------------------------------------------------------
# 8. Gated commands — not registered in v1.0
# ---------------------------------------------------------------------------


class TestIngestGatedCommands:
    """classify/backfill/purge/report are not registered in v1.0."""

    def test_classify_not_registered(self):
        _, _, code = run_fp("ingest", "classify")
        assert code != 0

    def test_backfill_not_registered(self):
        _, _, code = run_fp("ingest", "backfill")
        assert code != 0

    def test_purge_not_registered(self):
        _, _, code = run_fp("ingest", "purge")
        assert code != 0

    def test_report_not_registered(self):
        _, _, code = run_fp("ingest", "report")
        assert code != 0


# ---------------------------------------------------------------------------
# 9. Ingest lock — concurrent execution prevention
# ---------------------------------------------------------------------------


class TestIngestLock:
    """fp ingest acquires a lockfile to prevent concurrent execution."""

    @patch("footprinter.paths.get_run_lock_path")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_concurrent_ingest_rejected(self, mock_orch_cls, mock_lock_path, tmp_path):
        """A second fp ingest is rejected while the lock is held."""
        lock_file = tmp_path / "run.lock"
        mock_lock_path.return_value = lock_file
        mock_orch = MagicMock()
        mock_orch_cls.return_value = mock_orch

        # Pre-acquire the lock
        fd = open(lock_file, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            stdout, stderr, code = run_fp("ingest", "--quiet")
            assert code == 1
            output = stdout + stderr
            assert "already" in output.lower() and "in progress" in output.lower()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    @patch("footprinter.paths.get_run_lock_path")
    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_lock_released_after_ingest(self, mock_orch_cls, _print, _save, mock_lock_path, tmp_path):
        """Lock is released after a successful ingest."""
        lock_file = tmp_path / "run.lock"
        mock_lock_path.return_value = lock_file
        mock_orch = MagicMock()
        mock_orch.run_pipeline.return_value = None
        mock_orch.config = {}
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "--quiet")

        # Lock should be released — acquiring it should succeed
        fd = open(lock_file, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fd.close()
            raise AssertionError("Lock was not released after successful ingest")
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()

    @patch("footprinter.paths.get_run_lock_path")
    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_lock_released_on_error(self, mock_orch_cls, _print, _save, mock_lock_path, tmp_path):
        """Lock is released even when the pipeline raises an error."""
        lock_file = tmp_path / "run.lock"
        mock_lock_path.return_value = lock_file
        mock_orch = MagicMock()
        mock_orch.run_pipeline.side_effect = ValueError("stage X not found")
        mock_orch.config = {}
        mock_orch_cls.return_value = mock_orch

        run_fp("ingest", "--quiet")

        # Lock should be released despite the error
        fd = open(lock_file, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fd.close()
            raise AssertionError("Lock was not released after pipeline error")
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


# ---------------------------------------------------------------------------
# 10. fp run removed — backward-compat alias stripped
# ---------------------------------------------------------------------------


class TestRunRemoved:
    """fp run is no longer a valid command."""

    def test_run_rejected(self):
        """fp run should be rejected as an invalid choice."""
        _, _, code = run_fp("run", "--help")
        assert code != 0
