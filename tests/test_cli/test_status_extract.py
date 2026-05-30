"""
Tests for footprinter.ingest.status — extracted status reporting module.

Covers: import verification, _stage_detail_string bug fix (folders_indexed key),
retention code removal.
"""


class TestStatusModuleImports:
    """Verify the extracted module exports the active functions."""

    def test_import_from_status_module(self):
        """Active functions should be importable from status.py."""
        from footprinter.ingest.status import (
            _print_completion_summary,
            _stage_detail_string,
            print_results,
        )

        assert callable(print_results)
        assert callable(_stage_detail_string)
        assert callable(_print_completion_summary)


class TestFoldersIndexedKey:
    """Bug fix: drive_folders stage returns folders_indexed, not folders_found."""

    def test_folders_indexed_key(self):
        """_stage_detail_string should map folders_indexed to 'folders' label."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "drive_folders",
            "status": "completed",
            "folders_indexed": 25,
        }
        detail = _stage_detail_string(result)
        assert "25 folders" in detail


class TestSkipReason:
    """Skipped stages should show their reason in detail string."""

    def test_skipped_stage_shows_reason(self):
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "drive_folders",
            "status": "skipped",
            "reason": "No Drive accounts configured",
        }
        detail = _stage_detail_string(result)
        assert "No Drive accounts configured" in detail


class TestNoRetentionCode:
    """Retention/classification code removed from indexer/status.py."""

    def test_stage_detail_string_ignores_scored(self):
        """_stage_detail_string should not extract 'scored' sub-key."""
        from footprinter.ingest.status import _stage_detail_string

        result = {
            "stage": "analysis",
            "status": "completed",
            "scoring": {"status": "completed", "scored": 42},
        }
        detail = _stage_detail_string(result)
        assert "scored" not in detail
