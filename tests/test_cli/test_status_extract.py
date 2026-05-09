"""
Tests for footprinter.ingest.status — extracted status reporting module.

Covers: import verification, _stage_detail_string bug fix (folders_indexed key),
retention code removal.
"""

import io


class TestStatusModuleImports:
    """Verify the extracted module exports all 4 functions."""

    def test_import_from_status_module(self):
        """All 4 extracted functions should be importable from status.py."""
        from footprinter.ingest.status import (
            _print_completion_summary,
            _stage_detail_string,
            print_results,
            print_status,
        )

        assert callable(print_status)
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


class TestChatsAndFoldersInTable:
    """Chats and folders should render as table rows, not loose dim text."""

    def test_chats_in_table_not_loose(self):
        """Conversations count appears as a table row, not loose 'Chats:' text."""
        from rich.console import Console

        from footprinter.ingest.status import print_status

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        status = {
            "files_total": 0,
            "files": {},
            "folders": {},
            "visits": 0,
            "emails": 0,
            "chats": {"claude": 5},
            "messages": 0,
            "projects": 0,
        }
        print_status(status, console=console)
        output = buf.getvalue()
        assert "Chats" in output
        assert "Chats:" not in output

    def test_folders_in_table_not_loose(self):
        """Indexed folders count appears as a table row, not loose dim text."""
        from rich.console import Console

        from footprinter.ingest.status import print_status

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        status = {
            "files_total": 0,
            "files": {},
            "folders": {"local": 10},
            "visits": 0,
            "emails": 0,
            "chats": {},
            "messages": 0,
            "projects": 0,
        }
        print_status(status, console=console)
        output = buf.getvalue()
        assert "Indexed folders" in output
        assert "Indexed folders:" not in output


class TestIngestStatusFolderFilter:
    """get_status() must exclude removed folders from counts."""

    def test_removed_folders_excluded(self, tmp_path):
        from footprinter.ingest.database import Database
        from footprinter.ingest.status import get_status

        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        db.conn.execute(
            "INSERT INTO folders (path, relative_path, name, source, status) "
            "VALUES ('/tmp/a', 'a', 'a', 'local', 'listed')"
        )
        db.conn.execute(
            "INSERT INTO folders (path, relative_path, name, source, status) "
            "VALUES ('/tmp/b', 'b', 'b', 'local', 'removed')"
        )
        db.conn.commit()
        db.conn.close()

        status = get_status(str(db_path))
        assert status["folders"]["local"] == 1


class TestNoRetentionCode:
    """Retention/classification code removed from indexer/status.py."""

    def test_get_status_no_classifications_key(self, tmp_path):
        """get_status() should not return a 'classifications' key."""
        from footprinter.ingest.database import Database
        from footprinter.ingest.status import get_status

        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        db.conn.close()

        status = get_status(str(db_path))
        assert "classifications" not in status

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

    def test_print_status_no_retention_table(self):
        """print_status() should not render a Retention Classifications table."""
        from rich.console import Console

        from footprinter.ingest.status import print_status

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        status = {
            "files_total": 0,
            "files": {},
            "folders": {},
            "visits": 0,
            "emails": 0,
            "chats": {},
            "messages": 0,
            "projects": 0,
        }
        print_status(status, console=console)
        output = buf.getvalue()
        assert "Retention" not in output
