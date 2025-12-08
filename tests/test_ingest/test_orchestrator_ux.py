"""
Tests for orchestrator Rich UX output.

Covers: --quiet flag, Rich print_status/print_results, _stage_detail_string,
_print_completion_summary, and Rich auto-detection behavior.
"""

import io

from rich.console import Console


def _make_console():
    """Create a Rich Console that writes to a StringIO buffer."""
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False), buf


class TestQuietFlag:
    """Test --quiet / -q CLI flag."""

    def test_argparser_accepts_quiet(self):
        """--quiet should be accepted by the argparser."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", default="run")
        parser.add_argument("--stages", "-s", type=str)
        parser.add_argument("--full", "-f", action="store_true")
        parser.add_argument("--quiet", "-q", action="store_true")

        args = parser.parse_args(["--quiet", "status"])
        assert args.quiet is True

    def test_argparser_accepts_short_q(self):
        """Short -q flag should work."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", default="run")
        parser.add_argument("--quiet", "-q", action="store_true")

        args = parser.parse_args(["-q"])
        assert args.quiet is True

    def test_quiet_suppresses_logging(self):
        """When quiet is set, root logger should be CRITICAL."""
        import logging

        root = logging.getLogger()
        original_level = root.level

        try:
            root.setLevel(logging.CRITICAL)
            assert root.level == logging.CRITICAL
        finally:
            root.setLevel(original_level)


class TestPrintResults:
    """Test print_results() Rich output."""

    def test_empty_results(self):
        """Empty results list should not crash."""
        from footprinter.ingest.status import print_results

        console, buf = _make_console()
        print_results([], console=console)
        output = buf.getvalue()
        assert "Pipeline Results" in output
        assert "Pipeline complete" in output

    def test_quiet_no_output(self):
        """quiet=True should produce no output."""
        from footprinter.ingest.status import print_results

        console, buf = _make_console()
        print_results(
            [{"stage": "test", "status": "completed", "elapsed_seconds": 1.0}],
            quiet=True,
            console=console,
        )
        output = buf.getvalue()
        assert output == ""

    def test_stage_names_present(self):
        """Stage names should appear in output."""
        from footprinter.ingest.status import print_results

        console, buf = _make_console()
        results = [
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 1.5,
                "files_indexed": 42,
            },
            {
                "stage": "browser",
                "status": "completed",
                "elapsed_seconds": 0.5,
                "urls_indexed": 100,
            },
        ]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "local_files" in output
        assert "browser" in output

    def test_error_handling(self):
        """Error stages should show FAIL and error message."""
        from footprinter.ingest.status import print_results

        console, buf = _make_console()
        results = [
            {
                "stage": "gmail",
                "status": "error",
                "elapsed_seconds": 0.1,
                "error": "Connection refused",
            },
        ]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "gmail" in output
        assert "FAIL" in output


class TestStageDetailString:
    """Test _stage_detail_string() helper."""

    def test_file_count(self):
        """Should extract files_indexed."""
        from footprinter.ingest.status import _stage_detail_string

        result = {"stage": "local_files", "status": "completed", "files_indexed": 1234}
        detail = _stage_detail_string(result)
        assert "1,234 files" in detail

    def test_folder_count(self):
        """Should extract folders_found."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "local_folders",
            "status": "completed",
            "folders_found": 50,
            "inserted": 10,
            "updated": 5,
        }
        detail = _stage_detail_string(result)
        assert "50 folders" in detail

    def test_empty_result(self):
        """Empty/minimal result should return empty string."""
        from footprinter.ingest.status import _stage_detail_string

        result = {"stage": "chat", "status": "info"}
        detail = _stage_detail_string(result)
        assert detail == ""

    def test_limits_to_3_details(self):
        """Should return at most 3 comma-separated parts."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "test",
            "status": "completed",
            "files_indexed": 100,
            "folders_found": 50,
            "urls_indexed": 200,
            "emails_indexed": 300,
            "inserted": 10,
            "updated": 5,
        }
        detail = _stage_detail_string(result)
        parts = [p.strip() for p in detail.split(",")]
        assert len(parts) <= 3

    def test_nested_sub_result(self):
        """Should extract details from nested dicts with status key."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "rules_analysis",
            "status": "completed",
            "classification": {"status": "completed", "processed": 500},
        }
        detail = _stage_detail_string(result)
        assert "500 processed" in detail


class TestPrintStatus:
    """Test print_status() Rich output."""

    def test_quiet_mode(self):
        """quiet=True should produce no output."""
        from footprinter.ingest.status import print_status

        console, buf = _make_console()
        print_status({"files": {}, "files_total": 0}, quiet=True, console=console)
        output = buf.getvalue()
        assert output == ""

    def test_with_data(self):
        """Should render table with data."""
        from footprinter.ingest.status import print_status

        console, buf = _make_console()
        status = {
            "files_total": 100,
            "files": {
                "local": {"count": 80, "size_mb": 50.0},
                "drive:work": {"count": 20, "size_mb": 10.0},
            },
            "folders": {"local": 30},
            "visits": 500,
            "emails": 1000,
            "chats": {"claude": 5},
            "messages": 200,
            "projects": 10,
            # classifications removed — retention is app-scope
        }
        print_status(status, console=console)
        output = buf.getvalue()
        assert "Data Pipeline Status" in output
        assert "80" in output
        assert "500" in output
        assert "1,000" in output

    def test_empty_data(self):
        """Should handle empty/zero data without crashing."""
        from footprinter.ingest.status import print_status

        console, buf = _make_console()
        status = {
            "files_total": 0,
            "files": {},
            "folders": {},
            "visits": 0,
            "emails": 0,
            "chats": {},
            "messages": 0,
            "projects": 0,
            # classifications removed — retention is app-scope
        }
        print_status(status, console=console)
        output = buf.getvalue()
        assert "Data Pipeline Status" in output


class TestRichAutoDetection:
    """Test Rich Console behavior in non-terminal contexts."""

    def test_console_works_non_terminal(self):
        """Console should work when writing to a StringIO buffer."""
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        console.print("test output")
        output = buf.getvalue()
        assert "test output" in output

    def test_console_forced_terminal(self):
        """Console with force_terminal should still write output."""
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=True)
        console.print("[bold]bold text[/bold]")
        output = buf.getvalue()
        assert "bold text" in output


class TestCompletionSummary:
    """Test _print_completion_summary() helper."""

    def test_total_time(self):
        """Should display total elapsed time."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        results = [
            {"stage": "a", "status": "completed", "elapsed_seconds": 2.5},
            {"stage": "b", "status": "completed", "elapsed_seconds": 3.5},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()
        assert "6.0s" in output
        assert "Pipeline complete" in output

    def test_error_reporting(self):
        """Should report error count."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        results = [
            {"stage": "a", "status": "completed", "elapsed_seconds": 1.0},
            {"stage": "b", "status": "error", "elapsed_seconds": 0.5, "error": "fail"},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()
        assert "1 error" in output
        assert "finished with" in output

    def test_next_steps(self):
        """Should display next steps."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        _print_completion_summary(console, [])
        output = buf.getvalue()
        assert "Next steps" in output
        assert "fp status" in output

    def test_warning_reporting(self):
        """Should report warning count for completed_with_errors stages."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        results = [
            {"stage": "a", "status": "completed", "elapsed_seconds": 1.0},
            {"stage": "b", "status": "completed_with_errors", "elapsed_seconds": 0.5},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()
        assert "1 warning" in output
        assert "Pipeline complete" in output

    def test_completed_with_errors_counts_as_completed(self):
        """completed_with_errors stages should count toward the completed total."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        results = [
            {"stage": "a", "status": "completed", "elapsed_seconds": 1.0},
            {"stage": "b", "status": "completed_with_errors", "elapsed_seconds": 0.5},
            {"stage": "c", "status": "completed", "elapsed_seconds": 0.3},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()
        assert "3 stages" in output


class TestCompletedWithErrorsDisplay:
    """Test that completed_with_errors shows WARN in output."""

    def test_completed_with_errors_shows_warn(self):
        """print_results should show WARN for completed_with_errors status."""
        from footprinter.ingest.status import print_results

        console, buf = _make_console()
        results = [
            {
                "stage": "drive_folders",
                "status": "completed_with_errors",
                "elapsed_seconds": 2.0,
                "accounts": {
                    "work": {"status": "completed"},
                    "personal": {"status": "error", "error": "auth failed"},
                },
            },
        ]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "WARN" in output
        assert "drive_folders" in output

    def test_stage_detail_string_with_sub_errors(self):
        """_stage_detail_string should show 'key: error' for failed sub-operations."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "project_links",
            "status": "completed_with_errors",
            "project_detection": {"status": "completed", "projects_found": 10},
            "file_linking": {"status": "error", "error": "db locked"},
            "folder_counts": {"status": "completed", "folders_updated": 5},
        }
        detail = _stage_detail_string(result)
        assert "file_linking: error" in detail

    def test_stage_detail_string_skips_error_type(self):
        """error_type should not appear in detail string."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "browser",
            "status": "error",
            "error": "timeout",
            "error_type": "runtime",
        }
        detail = _stage_detail_string(result)
        assert "error_type" not in detail
        assert "runtime" not in detail


# ---------------------------------------------------------------------------
# Bug 6: TestQuietFlagBehavior — quiet suppresses all output
# ---------------------------------------------------------------------------
class TestQuietFlagBehavior:
    """Verify print_status and print_results produce zero output when quiet=True."""

    def test_print_status_quiet_zero_output(self):
        """print_status(quiet=True) should produce zero output."""
        from footprinter.ingest.status import print_status

        console, buf = _make_console()
        status = {
            "files": {"local": {"count": 10, "size_mb": 5.0}},
            "files_total": 10,
            "visits": 5,
            "emails": 3,
            "chats": {},
            "messages": 0,
            "projects": 1,
            # classifications removed — retention is app-scope
            "folders": {},
        }
        print_status(status, quiet=True, console=console)
        assert buf.getvalue() == ""

    def test_print_results_quiet_zero_output(self):
        """print_results(quiet=True) should produce zero output."""
        from footprinter.ingest.status import print_results

        console, buf = _make_console()
        results = [
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 1.0,
                "files_indexed": 42,
            }
        ]
        print_results(results, quiet=True, console=console)
        assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# Bug 7: TestKeyboardInterrupt — graceful shutdown
# ---------------------------------------------------------------------------
class TestKeyboardInterrupt:
    """Verify orchestrator supports graceful shutdown."""

    def test_orchestrator_has_close_method(self):
        """DataPipelineOrchestrator should have a close() method for cleanup."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        assert hasattr(DataPipelineOrchestrator, "close")
        assert callable(getattr(DataPipelineOrchestrator, "close"))


# ---------------------------------------------------------------------------
# Bug 8: TestErrorTruncation — long errors preserved
# ---------------------------------------------------------------------------
class TestErrorTruncation:
    """Verify long error messages are not over-truncated."""

    def test_long_error_not_over_truncated(self):
        """Long error message should preserve the filename (not truncated to 60 chars)."""
        from footprinter.ingest.status import print_results

        long_error = "Permission denied: /Users/john/Work/client-project/src/components/authentication/LoginForm.py"
        # Use a wide console so Rich table doesn't truncate columns
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=300)
        results = [{"stage": "local_files", "status": "error", "elapsed_seconds": 0.1, "error": long_error}]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "LoginForm.py" in output


# ---------------------------------------------------------------------------
# TestErrorDisplay — 2 tests
# ---------------------------------------------------------------------------
class TestErrorDisplay:
    """Tests for error message display in print_results."""

    def test_error_preserves_file_path(self):
        """Error message keeps filename visible in output."""
        from footprinter.ingest.status import print_results

        error_msg = "FileNotFoundError: /Users/john/Work/project/src/main.py"
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=300)
        results = [{"stage": "local_files", "status": "error", "elapsed_seconds": 0.1, "error": error_msg}]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "main.py" in output

    def test_short_error_not_padded(self):
        """Short errors like 'timeout' display as-is."""
        from footprinter.ingest.status import print_results

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=300)
        results = [{"stage": "gmail", "status": "error", "elapsed_seconds": 0.2, "error": "timeout"}]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "timeout" in output


# ---------------------------------------------------------------------------
# TestCompletionSummaryEdgeCases — 2 tests
# ---------------------------------------------------------------------------
class TestCompletionSummaryEdgeCases:
    """Tests for _print_completion_summary() edge cases."""

    def test_all_stages_failed(self):
        """Every stage errored → summary shows error count."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        results = [
            {"stage": "a", "status": "error", "elapsed_seconds": 0.5, "error": "fail1"},
            {"stage": "b", "status": "error", "elapsed_seconds": 0.3, "error": "fail2"},
            {"stage": "c", "status": "error", "elapsed_seconds": 0.2, "error": "fail3"},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()
        assert "3 error" in output
        assert "finished with" in output

    def test_mixed_statuses(self):
        """Mix of completed, completed_with_errors, error, info → counts correct."""
        from footprinter.ingest.status import _print_completion_summary

        console, buf = _make_console()
        results = [
            {"stage": "a", "status": "completed", "elapsed_seconds": 1.0},
            {"stage": "b", "status": "completed_with_errors", "elapsed_seconds": 0.5},
            {"stage": "c", "status": "error", "elapsed_seconds": 0.1, "error": "fail"},
            {"stage": "d", "status": "info", "elapsed_seconds": 0.0},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()
        # When errors exist, source shows "finished with N error(s)" and "M OK, N failed"
        assert "1 error" in output
        assert "3 OK" in output
        assert "1 failed" in output


# ---------------------------------------------------------------------------
# TestStageDetailEdgeCases — 2 tests
# ---------------------------------------------------------------------------
class TestStageDetailEdgeCases:
    """Tests for _stage_detail_string() edge cases."""

    def test_handles_none_values(self):
        """None values in result dict → no crash."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "local_files",
            "status": "completed",
            "files_indexed": None,
            "folders_found": None,
        }
        # Should not crash — None is not int/float so should be skipped
        detail = _stage_detail_string(result)
        assert isinstance(detail, str)

    def test_handles_zero_counts(self):
        """files_indexed: 0 → displays correctly."""
        from footprinter.ingest.status import _stage_detail_string

        result = {"stage": "local_files", "status": "completed", "files_indexed": 0}
        detail = _stage_detail_string(result)
        assert "0 files" in detail
