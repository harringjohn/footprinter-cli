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


def _make_scanner(
    since: datetime | None = None,
    known_paths: set[str] | None = None,
) -> FileScanner:
    return FileScanner(_minimal_config(), since_datetime=since, known_paths=known_paths)


# --- mtime check (line 292-306) ---
#
# scan_directory calls is_supported_file (line 286) immediately before the
# mtime stat (line 292).  We use is_supported_file as a phase gate so the
# stat mock only fires on the mtime call, regardless of how many times
# pathlib internally calls Path.stat during is_symlink/resolve (varies
# across Python versions — 3.14 changed pathlib internals).


class TestMtimeCheck:
    def _make_phase_mocks(self, scanner, error_cls: type[Exception] = OSError):
        """Return (stat_side_effect, tracking_supported) that raise on the mtime stat."""
        original_stat = Path.stat
        original_is_supported = scanner.is_supported_file
        supported_checked = False

        def tracking_supported(fp):
            nonlocal supported_checked
            if Path(fp).name == "test.txt":
                supported_checked = True
            return original_is_supported(fp)

        def stat_side_effect(self_path, *args, **kwargs):
            if self_path.name == "test.txt" and supported_checked:
                raise error_cls("disk error" if error_cls is OSError else "unexpected bug")
            return original_stat(self_path, *args, **kwargs)

        return stat_side_effect, tracking_supported

    def test_mtime_check_failure_logs_debug_and_continues(self, tmp_path, caplog):
        """OSError on mtime stat should log debug and still yield the file."""
        f = tmp_path / "test.txt"
        f.write_text("hello")

        scanner = _make_scanner(since=datetime.now() - timedelta(days=1))
        fake_meta = {"file_path": str(f), "file_name": "test.txt"}
        stat_effect, supported_effect = self._make_phase_mocks(scanner)

        with (
            patch.object(Path, "stat", stat_effect),
            patch.object(scanner, "is_supported_file", side_effect=supported_effect),
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
        stat_effect, supported_effect = self._make_phase_mocks(
            scanner, error_cls=RuntimeError,
        )

        with (
            patch.object(Path, "stat", stat_effect),
            patch.object(scanner, "is_supported_file", side_effect=supported_effect),
        ):
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


# --- moved file detection ---


class TestMovedFileDetection:
    """Incremental ingest must detect files moved to new paths."""

    def _set_mtime(self, path: Path, dt: datetime):
        """Set file mtime to a specific datetime."""
        import os
        ts = dt.timestamp()
        os.utime(path, (ts, ts))

    def test_moved_file_yielded_when_not_in_known_paths(self, tmp_path):
        """File with old mtime at unknown path → yielded (moved file)."""
        f = tmp_path / "moved.txt"
        f.write_text("content")
        self._set_mtime(f, datetime(2024, 1, 1))

        scanner = _make_scanner(
            since=datetime(2025, 1, 1),
            known_paths=set(),
        )

        results = list(scanner.scan_directory(str(tmp_path)))
        paths = [r["file_path"] for r in results]
        assert str(f.absolute()) in paths

    def test_unchanged_file_skipped_when_in_known_paths(self, tmp_path):
        """File with old mtime at known path → skipped (truly unchanged)."""
        f = tmp_path / "known.txt"
        f.write_text("content")
        self._set_mtime(f, datetime(2024, 1, 1))

        scanner = _make_scanner(
            since=datetime(2025, 1, 1),
            known_paths={str(f.absolute())},
        )

        results = list(scanner.scan_directory(str(tmp_path)))
        paths = [r["file_path"] for r in results]
        assert str(f.absolute()) not in paths

    def test_known_paths_none_preserves_old_behavior(self, tmp_path):
        """known_paths=None (full scan default) → old mtime files still skipped."""
        f = tmp_path / "old.txt"
        f.write_text("content")
        self._set_mtime(f, datetime(2024, 1, 1))

        scanner = _make_scanner(
            since=datetime(2025, 1, 1),
            known_paths=None,
        )

        results = list(scanner.scan_directory(str(tmp_path)))
        paths = [r["file_path"] for r in results]
        assert str(f.absolute()) not in paths

    def test_modified_file_yielded_regardless_of_known_paths(self, tmp_path):
        """File with new mtime → yielded regardless of known_paths."""
        f = tmp_path / "new.txt"
        f.write_text("content")
        # mtime is now (after since), so mtime filter doesn't apply

        scanner = _make_scanner(
            since=datetime(2020, 1, 1),
            known_paths=set(),
        )

        results = list(scanner.scan_directory(str(tmp_path)))
        paths = [r["file_path"] for r in results]
        assert str(f.absolute()) in paths

    def test_moved_count_in_log(self, tmp_path, caplog):
        """Scan summary should include moved file count."""
        f = tmp_path / "moved_log.txt"
        f.write_text("content")
        self._set_mtime(f, datetime(2024, 1, 1))

        scanner = _make_scanner(
            since=datetime(2025, 1, 1),
            known_paths=set(),
        )

        with caplog.at_level(logging.INFO, logger="footprinter.ingest.file_scanner"):
            list(scanner.scan_directory(str(tmp_path)))

        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("moved" in m.lower() for m in info_msgs), (
            f"Expected 'moved' in scan summary log; got: {info_msgs}"
        )
