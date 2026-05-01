"""Tests for the shared ingest_entries() helper.

Validates the helper's counting, error handling, logging, and PipeResult
construction — the loop logic shared by Browser, Email, DriveFiles, and
DriveFolders adapters.
"""

from unittest.mock import MagicMock, call, patch

from footprinter.ingest.adapters.protocol import PipeStatus


class TestIngestHappyPath:
    """All entries succeed — returns completed with correct counts."""

    def test_happy_path(self):
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock()

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED
        assert result.data["items_indexed"] == 3
        assert result.data["errors"] == 0
        assert insert_fn.call_count == 3


class TestIngestPartialErrors:
    """Some entries raise — returns completed_with_errors."""

    def test_partial_errors(self):
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock(side_effect=[None, Exception("bad"), None, Exception("bad"), None])

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.data["items_indexed"] == 3
        assert result.data["errors"] == 2
        assert result.error == "2 entries failed"


class TestIngestAllFail:
    """Every entry raises — returns completed_with_errors with 0 successes.

    Intentional: 100% failure still returns COMPLETED_WITH_ERRORS, not ERROR.
    ERROR is reserved for stage-level failures (database, config, runtime).
    COMPLETED_WITH_ERRORS means the loop completed — individual entries failed.
    Even at 100% failure the stage machinery worked; the data was bad.
    """

    def test_all_entries_fail(self):
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock(side_effect=Exception("always fails"))

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.status != PipeStatus.ERROR, (
            "100% failure returns COMPLETED_WITH_ERRORS, not ERROR — ERROR is for stage-level failures, not bad data"
        )
        assert result.data["items_indexed"] == 0
        assert result.data["errors"] == 3


class TestIngestMaxLoggedErrors:
    """Only max_logged_errors errors are logged, even if more occur."""

    @patch("footprinter.ingest.adapters.ingest.logger")
    def test_max_logged_errors(self, mock_logger):
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = list(range(10))
        insert_fn = MagicMock(side_effect=Exception("fail"))

        result = ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            max_logged_errors=5,
        )

        assert result.data["errors"] == 10
        # Only 5 logger.error calls despite 10 failures
        assert mock_logger.error.call_count == 5


class TestIngestProgressInterval:
    """Progress logging fires at the specified interval."""

    @patch("footprinter.ingest.adapters.ingest.logger")
    def test_progress_interval(self, mock_logger):
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = list(range(5))
        insert_fn = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            progress_interval=2,
        )

        # Should log at 2 and 4 (every 2 successes)
        info_calls = mock_logger.info.call_args_list
        progress_msgs = [c for c in info_calls if "2" in str(c) or "4" in str(c)]
        assert len(progress_msgs) >= 2


class TestIngestEmptyEntries:
    """Zero entries — returns completed with count=0."""

    def test_empty_entries(self):
        from footprinter.ingest.adapters.ingest import ingest_entries

        insert_fn = MagicMock()

        result = ingest_entries("test_stage", [], insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED
        assert result.data["items_indexed"] == 0
        assert result.data["errors"] == 0
        assert insert_fn.call_count == 0


class TestIngestSuppressedErrorSummary:
    """Post-loop warning when errors exceed max_logged_errors."""

    @patch("footprinter.ingest.adapters.ingest.logger")
    def test_suppressed_errors_logged(self, mock_logger):
        """10 failures with max_logged_errors=3 → warning about 7 suppressed."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = list(range(10))
        insert_fn = MagicMock(side_effect=Exception("fail"))

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            max_logged_errors=3,
        )

        assert mock_logger.error.call_count == 3
        assert mock_logger.warning.call_count == 1
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "7 more errors not shown" in warning_msg

    @patch("footprinter.ingest.adapters.ingest.logger")
    def test_no_summary_when_errors_at_cap(self, mock_logger):
        """3 failures with max_logged_errors=3 → no suppressed warning."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = list(range(3))
        insert_fn = MagicMock(side_effect=Exception("fail"))

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            max_logged_errors=3,
        )

        assert mock_logger.error.call_count == 3
        assert mock_logger.warning.call_count == 0


class TestIngestSkippedEntries:
    """insert_fn returning False signals a skip — not counted as success."""

    def test_false_return_counted_as_skip(self):
        """5 entries, 2 return False → 3 successes, 2 skipped."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock(side_effect=[None, False, None, False, None])

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED
        assert result.data["items_indexed"] == 3
        assert result.data["skipped"] == 2
        assert result.data["errors"] == 0

    def test_all_skipped(self):
        """3 entries, all return False → 0 successes, 3 skipped."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock(return_value=False)

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED
        assert result.data["items_indexed"] == 0
        assert result.data["skipped"] == 3
        assert result.data["errors"] == 0

    def test_none_return_still_counted_as_success(self):
        """3 entries returning None → 3 successes, 0 skipped (backward compat)."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock(return_value=None)

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED
        assert result.data["items_indexed"] == 3
        assert result.data["skipped"] == 0

    def test_mixed_skip_and_error(self):
        """4 entries: [None, False, Exception, None] → 2 success, 1 skip, 1 error."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4]
        insert_fn = MagicMock(side_effect=[None, False, Exception("bad"), None])

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.data["items_indexed"] == 2
        assert result.data["skipped"] == 1
        assert result.data["errors"] == 1


class TestIngestBatchCommit:
    """Batch-commit support — periodic conn.commit() during the ingest loop."""

    def test_commits_at_batch_boundary(self):
        """5 entries, batch_size=2 → commits at 2, 4, and remainder (5) = 3 commits."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock()
        mock_conn = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=2,
        )

        assert mock_conn.commit.call_count == 3

    def test_final_commit_for_remainder(self):
        """3 entries, batch_size=5 → only 1 commit (the remainder after loop)."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock()
        mock_conn = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=5,
        )

        assert mock_conn.commit.call_count == 1

    def test_no_commit_when_conn_is_none(self):
        """No conn param → no commit calls (backward-compatible)."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock()

        # No conn param — should not raise or attempt commits
        result = ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
        )

        assert result.data["items_indexed"] == 5

    def test_commit_on_error_preserves_batch(self):
        """[ok, ok, error, ok, ok] batch_size=3 → commit on error (2 pending), commit remainder (2) = 2."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock(side_effect=[None, None, Exception("bad"), None, None])
        mock_conn = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=3,
        )

        assert mock_conn.commit.call_count == 2

    def test_skipped_entries_dont_count_toward_batch(self):
        """[ok, skip, ok, skip, ok] batch_size=2 → commits at success 2, remainder at 3 = 2."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock(side_effect=[None, False, None, False, None])
        mock_conn = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=2,
        )

        assert mock_conn.commit.call_count == 2

    def test_batch_commit_failure_continues_processing(self):
        """Batch-boundary commit failure → all entries still processed, result reflects error."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock()
        mock_conn = MagicMock()
        # First commit (batch boundary at entry 2) fails, rest succeed
        mock_conn.commit.side_effect = [OSError("disk full"), None, None]

        result = ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=2,
        )

        assert insert_fn.call_count == 5
        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.data.get("commit_errors", 0) >= 1

    def test_error_recovery_commit_failure_continues(self):
        """Error-recovery commit failure → remaining entries still processed."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        # Entry 3 raises → triggers error-recovery commit
        insert_fn = MagicMock(side_effect=[None, None, ValueError("bad entry"), None, None])
        mock_conn = MagicMock()
        # Error-recovery commit fails, final-remainder commit succeeds
        mock_conn.commit.side_effect = [OSError("locked"), None]

        result = ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=10,
        )

        # All 5 entries attempted (entry 3 failed at insert level)
        assert insert_fn.call_count == 5
        assert result.data["items_indexed"] == 4
        assert result.data["errors"] == 1
        assert result.data.get("commit_errors", 0) >= 1

    def test_final_commit_failure_reflected_in_result(self):
        """Final-remainder commit failure → result is completed_with_errors."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock()
        mock_conn = MagicMock()
        # Only commit is the final remainder — and it fails
        mock_conn.commit.side_effect = OSError("disk full")

        result = ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=5,
        )

        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.data.get("commit_errors") == 1

    @patch("footprinter.ingest.adapters.ingest.logger")
    def test_commit_failure_logged_with_context(self, mock_logger):
        """Commit failure log message includes site label and pending count."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock()
        mock_conn = MagicMock()
        mock_conn.commit.side_effect = [OSError("disk full"), None]

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=2,
        )

        # Should have logged a warning with the stage name and pending count
        warning_calls = mock_logger.warning.call_args_list
        commit_warnings = [c for c in warning_calls if "commit" in str(c).lower()]
        assert len(commit_warnings) >= 1
        warning_msg = str(commit_warnings[0])
        assert "test_stage" in warning_msg

    def test_retry_commit_after_batch_failure(self):
        """Failed batch commit → retry commit after loop flushes uncommitted rows."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2]
        insert_fn = MagicMock()
        mock_conn = MagicMock()
        # Batch commit at entry 2 fails, retry commit after loop succeeds
        mock_conn.commit.side_effect = [OSError("locked"), None]

        result = ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            conn=mock_conn,
            batch_size=2,
        )

        # Two commits: failed batch + successful retry after loop
        assert mock_conn.commit.call_count == 2
        assert result.data.get("commit_errors") == 1


class TestIngestOnProgress:
    """on_progress callback fires with running processed count (every item)."""

    def test_all_succeed(self):
        """5 entries, all succeed → on_progress called 5 times with counts 1..5."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock()
        on_progress = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            on_progress=on_progress,
        )

        assert on_progress.call_args_list == [call(1), call(2), call(3), call(4), call(5)]

    def test_with_errors(self):
        """5 entries, 2 errors → on_progress called 5 times (every item)."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock(side_effect=[None, Exception("bad"), None, Exception("bad"), None])
        on_progress = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            on_progress=on_progress,
        )

        assert on_progress.call_args_list == [call(1), call(2), call(3), call(4), call(5)]

    def test_with_skips(self):
        """5 entries, 2 skips → on_progress called 5 times (every item)."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4, 5]
        insert_fn = MagicMock(side_effect=[None, False, None, False, None])
        on_progress = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            on_progress=on_progress,
        )

        assert on_progress.call_args_list == [call(1), call(2), call(3), call(4), call(5)]

    def test_mixed_errors_and_skips(self):
        """4 entries: [ok, skip, error, ok] → on_progress called 4 times."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3, 4]
        insert_fn = MagicMock(side_effect=[None, False, Exception("bad"), None])
        on_progress = MagicMock()

        ingest_entries(
            "test_stage",
            entries,
            insert_fn,
            count_label="items_indexed",
            on_progress=on_progress,
        )

        assert on_progress.call_args_list == [call(1), call(2), call(3), call(4)]

    def test_none_default_no_error(self):
        """on_progress=None (default) → no error, backward compatible."""
        from footprinter.ingest.adapters.ingest import ingest_entries

        entries = [1, 2, 3]
        insert_fn = MagicMock()

        result = ingest_entries("test_stage", entries, insert_fn, count_label="items_indexed")

        assert result.data["items_indexed"] == 3
