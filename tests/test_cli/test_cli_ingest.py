"""Tests for footprinter.cli.ingest — pipeline execution CLI module."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


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
