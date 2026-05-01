"""Tests for file_scanner.py exception handling."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from footprinter.ingest.file_scanner import FileScanner


def _minimal_config():
    """Config with no exclusions and all extensions supported."""
    return {"directories": [], "exclusions": {}, "indexing": {}}


def _make_scanner(since: datetime | None = None) -> FileScanner:
    return FileScanner(_minimal_config(), since_datetime=since)


# In Python 3.11, is_symlink() and resolve() both call Path.stat(),
# so the mtime check at line 303 is the 4th stat call on our file.
_MTIME_STAT_INDEX = 3


# --- mtime check (line 302-308) ---


class TestMtimeCheck:
    def test_mtime_check_failure_logs_debug_and_continues(self, tmp_path, caplog):
        """OSError on mtime stat should log debug and still yield the file."""
        f = tmp_path / "test.txt"
        f.write_text("hello")

        scanner = _make_scanner(since=datetime.now() - timedelta(days=1))
        fake_meta = {"file_path": str(f), "file_name": "test.txt"}

        original_stat = Path.stat
        call_count = 0

        def stat_side_effect(self_path, *args, **kwargs):
            nonlocal call_count
            if self_path.name == "test.txt":
                call_count += 1
                if call_count == _MTIME_STAT_INDEX + 1:
                    raise OSError("disk error")
            return original_stat(self_path, *args, **kwargs)

        with (
            patch.object(Path, "stat", stat_side_effect),
            patch.object(scanner, "get_file_metadata", return_value=fake_meta),
            caplog.at_level(logging.DEBUG, logger="footprinter.ingest.file_scanner"),
        ):
            results = list(scanner.scan_directory(str(tmp_path)))

        assert len(results) == 1
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("mtime" in r.message.lower() for r in debug_msgs), (
            f"Expected debug log about mtime, got: {[r.message for r in caplog.records]}"
        )

    def test_mtime_check_narrow_propagates_non_os_error(self, tmp_path):
        """RuntimeError during mtime check should propagate, not be caught."""
        f = tmp_path / "test.txt"
        f.write_text("hello")

        scanner = _make_scanner(since=datetime.now() - timedelta(days=1))

        original_stat = Path.stat
        call_count = 0

        def stat_side_effect(self_path, *args, **kwargs):
            nonlocal call_count
            if self_path.name == "test.txt":
                call_count += 1
                if call_count == _MTIME_STAT_INDEX + 1:
                    raise RuntimeError("unexpected bug")
            return original_stat(self_path, *args, **kwargs)

        with patch.object(Path, "stat", stat_side_effect):
            with pytest.raises(RuntimeError, match="unexpected bug"):
                list(scanner.scan_directory(str(tmp_path)))


# --- get_file_metadata (line 217) ---


class TestGetFileMetadata:
    def test_get_file_metadata_returns_none_on_os_error(self, caplog):
        """PermissionError (subclass of OSError) on stat returns None + logs."""
        scanner = _make_scanner()
        mock_path = MagicMock(spec=Path)
        mock_path.stat.side_effect = PermissionError("denied")

        with caplog.at_level(logging.ERROR, logger="footprinter.ingest.file_scanner"):
            result = scanner.get_file_metadata(mock_path)

        assert result is None
        assert any("Error reading metadata" in r.message for r in caplog.records)

    def test_get_file_metadata_propagates_non_os_error(self):
        """ValueError during metadata extraction should propagate."""
        scanner = _make_scanner()
        mock_path = MagicMock(spec=Path)
        mock_path.stat.side_effect = ValueError("bad value")

        with pytest.raises(ValueError, match="bad value"):
            scanner.get_file_metadata(mock_path)


# --- scan_directory outer try (line 318) ---


class TestScanDirectoryOuter:
    def test_scan_directory_catches_os_error(self, tmp_path, caplog):
        """OSError in os.walk should be caught and logged."""
        scanner = _make_scanner()

        with (
            patch("os.walk", side_effect=OSError("walk failed")),
            caplog.at_level(logging.ERROR, logger="footprinter.ingest.file_scanner"),
        ):
            results = list(scanner.scan_directory(str(tmp_path)))

        assert results == []
        assert any("Error scanning directory" in r.message for r in caplog.records)

    def test_scan_directory_propagates_non_os_error(self, tmp_path):
        """RuntimeError in os.walk should propagate."""
        scanner = _make_scanner()

        with patch("os.walk", side_effect=RuntimeError("internal error")):
            with pytest.raises(RuntimeError, match="internal error"):
                list(scanner.scan_directory(str(tmp_path)))
