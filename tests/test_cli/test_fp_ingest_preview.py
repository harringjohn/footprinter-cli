"""Tests for `fp ingest --preview` (FPR-1723).

The preview path runs the FileScanner against the configured directories,
aggregates a ScanSummary, prints it, and exits without invoking the
DataPipelineOrchestrator or vectorization stage. The interactive prompt
is gated on stdout being a TTY (so piped output skips it). The same
exclusive run lock as `fp ingest` is acquired during the preview scan.
"""

import fcntl
from unittest.mock import patch

from conftest import run_fp


def _config_for(tmp_path) -> dict:
    return {
        "directories": [str(tmp_path)],
        "exclusions": {"always": [], "sensitive": []},
        "indexing": {},
    }


class TestPreviewFlag:
    def test_help_lists_preview_flag(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        assert code == 0
        assert "--preview" in stdout + stderr

    def test_preview_does_not_invoke_orchestrator(self, tmp_path):
        """`--preview` must skip the orchestrator entirely."""
        (tmp_path / "a.py").write_text("print('x')\n")
        (tmp_path / "b.txt").write_text("hello\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator") as mock_orch,
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage") as mock_vec,
        ):
            stdout, stderr, code = run_fp("ingest", "--preview", "--quiet")

        assert code == 0
        mock_orch.assert_not_called()
        mock_vec.assert_not_called()

    def test_preview_renders_summary(self, tmp_path):
        """`--preview` writes a Rich summary including totals and an extension count."""
        (tmp_path / "a.py").write_text("print('x')\n")
        (tmp_path / "b.py").write_text("print('y')\n")
        (tmp_path / "c.txt").write_text("hello world\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
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

    def test_preview_quiet_renders_plain_summary(self, tmp_path):
        """`--quiet --preview` still emits a one-line plain-text summary."""
        (tmp_path / "a.py").write_text("hi\n")
        (tmp_path / "b.txt").write_text("hi\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator") as mock_orch,
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage") as mock_vec,
            patch("footprinter.cli.ingest._stdout_is_tty", return_value=True),
            patch("builtins.input") as mock_input,
        ):
            stdout, stderr, code = run_fp("ingest", "--preview", "--quiet")

        output = stdout + stderr
        assert code == 0
        # Plain-text summary marker
        assert "preview:" in output
        assert "files=2" in output
        # No prompt, no pipeline
        mock_input.assert_not_called()
        mock_orch.assert_not_called()
        mock_vec.assert_not_called()

    def test_preview_non_tty_skips_prompt(self, tmp_path):
        """Non-TTY (CI, scripts, piped output) skips the prompt and exits."""
        (tmp_path / "a.py").write_text("hi\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator") as mock_orch,
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage") as mock_vec,
            patch("footprinter.cli.ingest._stdout_is_tty", return_value=False),
            patch("builtins.input") as mock_input,
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_input.assert_not_called()
        mock_orch.assert_not_called()
        mock_vec.assert_not_called()

    def test_preview_piped_stdout_skips_prompt(self, tmp_path):
        """`fp ingest --preview | less` (stdout piped) must not prompt.

        Regression guard: an earlier implementation checked ``sys.stdin.isatty()``
        which would still be True with a piped stdout — so the prompt fired even
        though the user couldn't see the rendered tables. The check must be on
        stdout, not stdin.
        """
        (tmp_path / "a.py").write_text("hi\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.ingest.orchestrator.DataPipelineOrchestrator"),
            patch("footprinter.cli._vectorize_stage.run_vectorization_stage"),
            patch("footprinter.cli.ingest._stdout_is_tty", return_value=False),
            patch("builtins.input") as mock_input,
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_input.assert_not_called()

    def test_preview_tty_prompt_yes_proceeds(self, tmp_path):
        """Interactive `y` answer proceeds to the real ingest pipeline."""
        (tmp_path / "a.py").write_text("hi\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.cli.ingest._ingest_pipeline") as mock_pipeline,
            patch("footprinter.cli.ingest._stdout_is_tty", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_pipeline.assert_called_once()

    def test_preview_tty_prompt_no_aborts(self, tmp_path):
        """Anything other than `y` aborts cleanly."""
        (tmp_path / "a.py").write_text("hi\n")

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.cli.ingest._ingest_pipeline") as mock_pipeline,
            patch("footprinter.cli.ingest._stdout_is_tty", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            _, _, code = run_fp("ingest", "--preview")

        assert code == 0
        mock_pipeline.assert_not_called()

    def test_preview_does_not_mutate_args_preview(self, tmp_path):
        """Re-entry guard: `_ingest_pipeline` receives args with `preview=True` unchanged.

        Confirms the dispatcher's immediate-return contract is the only loop guard,
        not a hidden in-place mutation of the argparse Namespace.
        """
        (tmp_path / "a.py").write_text("hi\n")

        captured = {}

        def capture(args_):
            captured["preview"] = getattr(args_, "preview", None)

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.cli.ingest._ingest_pipeline", side_effect=capture),
            patch("footprinter.cli.ingest._stdout_is_tty", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            run_fp("ingest", "--preview")

        assert captured.get("preview") is True


class TestPreviewLocking:
    def test_preview_rejects_when_lock_held(self, tmp_path):
        """A second invocation while the run lock is held exits 1 with an error."""
        (tmp_path / "a.py").write_text("hi\n")
        lock_file = tmp_path / "run.lock"

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.paths.get_run_lock_path", return_value=lock_file),
        ):
            # Pre-acquire the lock
            fd = open(lock_file, "w")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                stdout, stderr, code = run_fp("ingest", "--preview", "--quiet")
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()

        assert code == 1
        output = stdout + stderr
        assert "already" in output.lower() and "in progress" in output.lower()

    def test_preview_releases_lock(self, tmp_path):
        """Lock is released after a preview so subsequent runs can acquire it."""
        (tmp_path / "a.py").write_text("hi\n")
        lock_file = tmp_path / "run.lock"

        with (
            patch("footprinter.source_registry.get_config", return_value=_config_for(tmp_path)),
            patch("footprinter.paths.get_run_lock_path", return_value=lock_file),
        ):
            run_fp("ingest", "--preview", "--quiet")

        # Should be able to acquire after preview returns
        fd = open(lock_file, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fd.close()
            raise AssertionError("Lock was not released after preview")
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
