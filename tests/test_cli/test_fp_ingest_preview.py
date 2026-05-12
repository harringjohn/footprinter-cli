"""Tests for `fp ingest --preview` (FPR-1723).

The preview path runs the FileScanner against the configured directories,
aggregates a ScanSummary, prints it, and exits without invoking the
DataPipelineOrchestrator or vectorization stage.
"""

from unittest.mock import patch

from conftest import run_fp


class TestPreviewFlag:
    def test_help_lists_preview_flag(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        assert code == 0
        assert "--preview" in stdout + stderr

    def test_preview_does_not_invoke_orchestrator(self, tmp_path):
        """`--preview` must skip the orchestrator entirely."""
        (tmp_path / "a.py").write_text("print('x')\n")
        (tmp_path / "b.txt").write_text("hello\n")
        config = {
            "directories": [str(tmp_path)],
            "exclusions": {"always": [], "sensitive": []},
            "indexing": {},
        }

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator") as mock_orch,
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage") as mock_vec,
        ):
            stdout, stderr, code = run_fp("ingest", "--preview", "--quiet")

        assert code == 0
        mock_orch.assert_not_called()
        mock_vec.assert_not_called()

    def test_preview_renders_summary(self, tmp_path):
        """`--preview` writes a summary including totals and an extension count."""
        (tmp_path / "a.py").write_text("print('x')\n")
        (tmp_path / "b.py").write_text("print('y')\n")
        (tmp_path / "c.txt").write_text("hello world\n")
        config = {
            "directories": [str(tmp_path)],
            "exclusions": {"always": [], "sensitive": []},
            "indexing": {},
        }

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator"),
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage"),
        ):
            stdout, stderr, code = run_fp("ingest", "--preview")

        output = stdout + stderr
        assert code == 0
        assert "Preview" in output
        assert ".py" in output
        # 3 files total
        assert "3" in output

    def test_preview_quiet_mode_skips_prompt(self, tmp_path):
        """`--quiet` exits 0 without an approval prompt."""
        (tmp_path / "a.py").write_text("hi\n")
        config = {
            "directories": [str(tmp_path)],
            "exclusions": {"always": [], "sensitive": []},
            "indexing": {},
        }

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator") as mock_orch,
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage") as mock_vec,
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input") as mock_input,
        ):
            _, _, code = run_fp("ingest", "--preview", "--quiet")

        assert code == 0
        mock_input.assert_not_called()
        mock_orch.assert_not_called()
        mock_vec.assert_not_called()

    def test_preview_non_tty_skips_prompt(self, tmp_path):
        """Non-TTY (CI, scripts) skips the prompt and exits."""
        (tmp_path / "a.py").write_text("hi\n")
        config = {
            "directories": [str(tmp_path)],
            "exclusions": {"always": [], "sensitive": []},
            "indexing": {},
        }

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator") as mock_orch,
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage") as mock_vec,
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input") as mock_input,
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_input.assert_not_called()
        mock_orch.assert_not_called()
        mock_vec.assert_not_called()

    def test_preview_tty_prompt_yes_proceeds(self, tmp_path):
        """Interactive `y` answer proceeds to the real ingest pipeline."""
        (tmp_path / "a.py").write_text("hi\n")
        config = {
            "directories": [str(tmp_path)],
            "exclusions": {"always": [], "sensitive": []},
            "indexing": {},
        }

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.cli.ingest._ingest_pipeline") as mock_pipeline,
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_pipeline.assert_called_once()

    def test_preview_tty_prompt_no_aborts(self, tmp_path):
        """Anything other than `y` aborts cleanly."""
        (tmp_path / "a.py").write_text("hi\n")
        config = {
            "directories": [str(tmp_path)],
            "exclusions": {"always": [], "sensitive": []},
            "indexing": {},
        }

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.cli.ingest._ingest_pipeline") as mock_pipeline,
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_pipeline.assert_not_called()
