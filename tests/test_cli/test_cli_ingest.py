"""Tests for footprinter.cli.ingest — pipeline execution CLI module."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class TestIngestStatusQuietFlag:
    """--quiet flag should suppress status output."""

    @patch("footprinter.ingest.status.print_status")
    @patch("footprinter.ingest.status.get_status")
    def test_quiet_flag_suppresses_status(self, mock_get_status, mock_print_status, tmp_path):
        """_ingest_status with quiet=True passes quiet=True to print_status."""
        from footprinter.cli.ingest import _ingest_status

        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_get_status.return_value = {"files_total": 0}
        args = SimpleNamespace(quiet=True, json=False)

        with patch("footprinter.paths.get_db_path", return_value=db_file):
            _ingest_status(args)

        mock_print_status.assert_called_once_with(
            {"files_total": 0},
            quiet=True,
        )

    @patch("footprinter.ingest.status.print_status")
    @patch("footprinter.ingest.status.get_status")
    def test_no_quiet_flag_prints_normally(self, mock_get_status, mock_print_status, tmp_path):
        """_ingest_status without quiet passes quiet=False to print_status."""
        from footprinter.cli.ingest import _ingest_status

        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_get_status.return_value = {"files_total": 0}
        args = SimpleNamespace(quiet=False, json=False)

        with patch("footprinter.paths.get_db_path", return_value=db_file):
            _ingest_status(args)

        mock_print_status.assert_called_once_with(
            {"files_total": 0},
            quiet=False,
        )


class TestIngestPipelineInvalidPipe:
    """Invalid --pipe should exit non-zero with helpful error."""

    @patch("footprinter.cli.ingest._run_with_logging")
    @patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator")
    def test_invalid_pipe_exits_nonzero(self, mock_orch_cls, mock_run):
        """_ingest_pipeline with bad pipe name prints error and exits 1."""
        from footprinter.cli.ingest import _ingest_pipeline

        mock_run.side_effect = ValueError("Unknown pipe(s): bad_name. Valid pipes: local_folders, local_files")

        args = SimpleNamespace(
            pipe="bad_name",
            full=False,
            quiet=False,
            verbose=False,
            rebuild_vectors=False,
            ingest_action=None,
        )

        with pytest.raises(SystemExit) as exc_info:
            _ingest_pipeline(args)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Connector architecture CLI alignment
# ---------------------------------------------------------------------------


class TestDefaultPipelineIsAll:
    """Default `fp ingest` (no flags) should use the 'all' pipeline, not 'full'."""

    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.ingest.status.print_results")
    def test_bare_ingest_defaults_to_all(self, mock_print, mock_save):
        """_run_with_logging with no pipes should default to 'all' pipeline."""
        from footprinter.cli.ingest import _run_with_logging

        mock_orch = MagicMock()
        mock_orch.run_pipeline.return_value = []
        mock_orch.config = {}

        _run_with_logging(
            mock_orch,
            pipes=None,
            mode="incremental",
            quiet=True,
        )

        mock_orch.run_pipeline.assert_called_once()
        call_args = mock_orch.run_pipeline.call_args
        assert call_args[0][0] == "all", f"Default pipeline should be 'all', got '{call_args[0][0]}'"


class TestRefreshSourcesGeneric:
    """Refresh sources should mention connectors, not hardcoded Google names."""

    def test_refresh_help_mentions_connectors(self):
        """The refresh subcommand help should mention connectors generically."""
        import argparse

        from footprinter.cli.ingest import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        ingest_parser = subs._name_parser_map.get("ingest")
        # Find the refresh subparser
        for action in ingest_parser._subparsers._actions:
            if hasattr(action, "_name_parser_map"):
                refresh_parser = action._name_parser_map.get("refresh")
                if refresh_parser:
                    desc = (refresh_parser.description or "") + (refresh_parser.epilog or "")
                    assert "connector" in desc, f"Refresh description doesn't mention 'connector': {desc}"
                    break
