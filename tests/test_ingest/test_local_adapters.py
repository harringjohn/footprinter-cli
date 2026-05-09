"""Tests for LocalFoldersAdapter and LocalFilesAdapter.

Validates protocol conformance, metadata, run() behavior, error handling,
and status() for the two local-source adapters.
"""

from unittest.mock import MagicMock, patch

import pytest

from footprinter.ingest.adapters import PipeAdapter, PipeResult, PipeStatus
from footprinter.ingest.adapters.protocol import ErrorType, PipeContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Minimal Database mock with db_path and conn."""
    db = MagicMock()
    db.db_path = "/tmp/test.db"
    cursor = MagicMock()
    db.conn.cursor.return_value = cursor
    # Prefix map builders return proper empty structures
    db.build_project_prefix_map.return_value = []
    db.build_folder_maps.return_value = ({}, {})
    return db


@pytest.fixture
def sample_config():
    """Config dict matching what the orchestrator passes."""
    return {
        "directories": ["~/Work", "~/Personal"],
        "folder_classifications": {},
    }


# ===========================================================================
# LocalFoldersAdapter
# ===========================================================================


class TestLocalFoldersProtocol:
    """LocalFoldersAdapter conforms to PipeAdapter."""

    def test_isinstance_check(self):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        adapter = LocalFoldersAdapter()
        assert isinstance(adapter, PipeAdapter)


class TestLocalFoldersMetadata:
    """Metadata properties return expected values."""

    def test_name(self):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        assert LocalFoldersAdapter().name == "local_folders"

    def test_pipe_name(self):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        assert LocalFoldersAdapter().pipe_name == "local_folders"

    def test_required_extras(self):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        assert LocalFoldersAdapter().required_extras == []


class TestLocalFoldersRun:
    """run() delegates to FolderIndexer and returns PipeResult."""

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_returns_stage_result(self, MockScanner, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = [
            {"path": "/a"},
            {"path": "/b"},
            {"path": "/c"},
        ]
        scanner_instance.save_folders.return_value = (2, 1, 0)

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFoldersAdapter()
        result = adapter.run(mock_db, ctx)

        assert isinstance(result, PipeResult)
        assert result.status == PipeStatus.COMPLETED
        assert result.stage == "local_folders"

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_data_keys(self, MockScanner, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = [{"path": "/a"}, {"path": "/b"}]
        scanner_instance.save_folders.return_value = (1, 1, 3)

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFoldersAdapter()
        result = adapter.run(mock_db, ctx)

        assert result.data["folders_found"] == 2
        assert result.data["inserted"] == 1
        assert result.data["updated"] == 1
        assert result.data["unchanged"] == 3

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_passes_db_to_scanner(self, MockScanner, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = []
        scanner_instance.save_folders.return_value = (0, 0, 0)

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFoldersAdapter()
        adapter.run(mock_db, ctx)

        MockScanner.assert_called_once_with(ctx.source_config, mock_db)


class TestLocalFoldersMarkRemoved:
    """run() invokes mark_removed_folders() with the scanned path set (FPR-1654)."""

    @patch("footprinter.ingest.adapters.local_folders.mark_removed_folders")
    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_calls_mark_removed_with_scanned_paths(
        self, MockScanner, mock_mark_removed, mock_db, sample_config
    ):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = [
            {"path": "/a"},
            {"path": "/b"},
            {"path": "/c"},
        ]
        scanner_instance.save_folders.return_value = (3, 0, 0)
        mock_mark_removed.return_value = [99, 100]

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFoldersAdapter()
        result = adapter.run(mock_db, ctx)

        mock_mark_removed.assert_called_once()
        passed_conn, passed_paths = mock_mark_removed.call_args[0]
        assert passed_conn is mock_db.conn
        assert passed_paths == {"/a", "/b", "/c"}
        assert result.data["removed"] == 2

    @patch("footprinter.ingest.adapters.local_folders.mark_removed_folders")
    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_skips_mark_removed_when_scan_empty(
        self, MockScanner, mock_mark_removed, mock_db, sample_config
    ):
        """Empty scan must not trigger any mark_removed call (avoids
        accidental mass-remove if the underlying guard is ever loosened)."""
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = []
        scanner_instance.save_folders.return_value = (0, 0, 0)

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFoldersAdapter()
        result = adapter.run(mock_db, ctx)

        mock_mark_removed.assert_not_called()
        assert result.data["removed"] == 0

    @patch("footprinter.ingest.adapters.local_folders.mark_removed_folders")
    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_skips_mark_removed_when_scan_roots_set(
        self, MockScanner, mock_mark_removed, mock_db, sample_config
    ):
        """Scoped scans (ctx.scan_roots is not None) must NOT trigger
        mark_removed_folders. Otherwise `fp setup folders add <path>` would
        scan only the new path and mass-mark every other folder as removed.
        Mirrors the FileIndexer's `if not self.incremental:` gate around
        mark_removed_files (FPR-1640)."""
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = [
            {"path": "/tmp/only-this/sub1"},
            {"path": "/tmp/only-this/sub2"},
        ]
        scanner_instance.save_folders.return_value = (2, 0, 0)

        ctx = PipeContext(
            source_config=sample_config,
            scan_roots=["/tmp/only-this"],
        )
        adapter = LocalFoldersAdapter()
        result = adapter.run(mock_db, ctx)

        mock_mark_removed.assert_not_called()
        assert result.data["removed"] == 0


class TestLocalFoldersScanRoots:
    """ctx.scan_roots scopes the scan to an explicit list, bypassing config[directories]."""

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_scan_roots_overrides_config_directories(self, MockScanner, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = []
        scanner_instance.save_folders.return_value = (0, 0, 0)

        ctx = PipeContext(source_config=sample_config, scan_roots=["/tmp/only-this"])
        LocalFoldersAdapter().run(mock_db, ctx)

        scanner_instance.scan_folders.assert_called_once_with(["/tmp/only-this"])

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_no_scan_roots_falls_back_to_config(self, MockScanner, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner_instance = MockScanner.return_value
        scanner_instance.scan_folders.return_value = []
        scanner_instance.save_folders.return_value = (0, 0, 0)

        ctx = PipeContext(source_config=sample_config)
        LocalFoldersAdapter().run(mock_db, ctx)

        scanner_instance.scan_folders.assert_called_once_with(["~/Work", "~/Personal"])


class TestLocalFoldersOnProgress:
    """_on_progress must not leak into FolderIndexer's source_config."""

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_on_progress_not_passed_to_indexer(self, MockIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        scanner = MockIndexer.return_value
        scanner.scan_folders.return_value = []
        scanner.save_folders.return_value = (0, 0, 0)

        ctx = PipeContext(source_config=sample_config, on_progress=lambda n: None)
        adapter = LocalFoldersAdapter()
        adapter.run(mock_db, ctx)

        passed_config = MockIndexer.call_args[0][0]
        assert "_on_progress" not in passed_config


class TestLocalFoldersErrorHandling:
    """run() returns PipeResult.make_error on exception."""

    @patch("footprinter.ingest.adapters.local_folders.FolderIndexer")
    def test_scanner_exception(self, MockScanner, mock_db, sample_config):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        MockScanner.side_effect = RuntimeError("disk not mounted")

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFoldersAdapter()
        result = adapter.run(mock_db, ctx)

        assert result.status == PipeStatus.ERROR
        assert result.stage == "local_folders"
        assert "disk not mounted" in result.error
        assert result.error_type == ErrorType.RUNTIME


class TestLocalFoldersStatus:
    """status() queries folders count."""

    def test_returns_dict_with_count(self, mock_db):
        from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter

        cursor = mock_db.conn.cursor.return_value
        cursor.fetchone.return_value = (42,)

        adapter = LocalFoldersAdapter()
        result = adapter.status(mock_db)

        assert isinstance(result, dict)
        assert result["folders"] == 42


# ===========================================================================
# LocalFilesAdapter
# ===========================================================================


class TestLocalFilesProtocol:
    """LocalFilesAdapter conforms to PipeAdapter."""

    def test_isinstance_check(self):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        adapter = LocalFilesAdapter()
        assert isinstance(adapter, PipeAdapter)


class TestLocalFilesMetadata:
    """Metadata properties return expected values."""

    def test_name(self):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        assert LocalFilesAdapter().name == "local_files"

    def test_pipe_name(self):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        assert LocalFilesAdapter().pipe_name == "local_files"

    def test_required_extras(self):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        assert LocalFilesAdapter().required_extras == []


class TestLocalFilesRun:
    """run() delegates to FileIndexer and returns PipeResult."""

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_returns_stage_result(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 100,
            "updated": 50,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFilesAdapter()
        result = adapter.run(mock_db, ctx)

        assert isinstance(result, PipeResult)
        assert result.status == PipeStatus.COMPLETED
        assert result.stage == "local_files"

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_data_keys_incremental(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 50,
            "updated": 25,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml", full_mode=False)
        adapter = LocalFilesAdapter()
        result = adapter.run(mock_db, ctx)

        assert result.data["inserted"] == 50
        assert result.data["updated"] == 25
        assert result.data["mode"] == "incremental"

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_data_keys_full_mode(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 150,
            "updated": 50,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml", full_mode=True)
        adapter = LocalFilesAdapter()
        result = adapter.run(mock_db, ctx)

        assert result.data["inserted"] == 150
        assert result.data["updated"] == 50
        assert result.data["mode"] == "full"

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_passes_shared_db_and_config(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml", full_mode=True)
        adapter = LocalFilesAdapter()
        adapter.run(mock_db, ctx)

        MockFileIndexer.assert_called_once_with(
            config_path="/tmp/config.yaml", last_run=None, db=mock_db, scan_roots=None
        )

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_does_not_close_db(self, MockFileIndexer, mock_db, sample_config):
        """Adapter must not close the shared db — orchestrator owns it."""
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFilesAdapter()
        adapter.run(mock_db, ctx)

        mock_db.close.assert_not_called()


class TestLocalFilesLastRun:
    """Adapter extracts last_run from ctx and passes to FileIndexer."""

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_adapter_passes_last_run_to_indexer(self, MockFileIndexer, mock_db, sample_config):
        """last_run in ctx is forwarded to FileIndexer constructor."""
        from datetime import datetime

        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        cutoff = datetime(2026, 4, 1, 12, 0, 0)
        ctx = PipeContext(
            source_config=sample_config,
            config_path="/tmp/config.yaml",
            full_mode=False,
            last_run=cutoff,
        )

        adapter = LocalFilesAdapter()
        adapter.run(mock_db, ctx)

        MockFileIndexer.assert_called_once_with(
            config_path="/tmp/config.yaml",
            last_run=cutoff,
            db=mock_db,
            scan_roots=None,
        )

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_adapter_full_mode_ignores_last_run(self, MockFileIndexer, mock_db, sample_config):
        """In full mode, last_run is forced to None regardless of ctx value."""
        from datetime import datetime

        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(
            source_config=sample_config,
            config_path="/tmp/config.yaml",
            full_mode=True,
            last_run=datetime(2026, 4, 1, 12, 0, 0),
        )

        adapter = LocalFilesAdapter()
        adapter.run(mock_db, ctx)

        MockFileIndexer.assert_called_once_with(
            config_path="/tmp/config.yaml",
            last_run=None,
            db=mock_db,
            scan_roots=None,
        )


class TestLocalFilesScanRoots:
    """ctx.scan_roots is forwarded to FileIndexer to scope the scan."""

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_scan_roots_passed_to_file_indexer(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(
            source_config=sample_config,
            config_path="/tmp/config.yaml",
            scan_roots=["/tmp/only-this"],
        )
        LocalFilesAdapter().run(mock_db, ctx)

        MockFileIndexer.assert_called_once_with(
            config_path="/tmp/config.yaml",
            last_run=None,
            db=mock_db,
            scan_roots=["/tmp/only-this"],
        )

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_no_scan_roots_passes_none(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.return_value = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "unchanged": 0,
            "errors": 0,
        }

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        LocalFilesAdapter().run(mock_db, ctx)

        MockFileIndexer.assert_called_once_with(
            config_path="/tmp/config.yaml",
            last_run=None,
            db=mock_db,
            scan_roots=None,
        )


class TestLocalFilesErrorHandling:
    """run() returns PipeResult.make_error on exception."""

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_indexer_exception(self, MockFileIndexer, mock_db, sample_config):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        MockFileIndexer.side_effect = RuntimeError("permission denied")

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFilesAdapter()
        result = adapter.run(mock_db, ctx)

        assert result.status == PipeStatus.ERROR
        assert result.stage == "local_files"
        assert "permission denied" in result.error
        assert result.error_type == ErrorType.RUNTIME

    @patch("footprinter.ingest.adapters.local_files.FileIndexer")
    def test_does_not_close_shared_db_on_error(self, MockFileIndexer, mock_db, sample_config):
        """Shared DB must not be closed on error — orchestrator owns it."""
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        indexer_instance = MockFileIndexer.return_value
        indexer_instance.index_files.side_effect = RuntimeError("corrupt index")

        ctx = PipeContext(source_config=sample_config, config_path="/tmp/config.yaml")
        adapter = LocalFilesAdapter()
        result = adapter.run(mock_db, ctx)

        mock_db.close.assert_not_called()
        assert result.status == PipeStatus.ERROR


class TestLocalFilesStatus:
    """status() queries file count where source='local'."""

    def test_returns_dict_with_count(self, mock_db):
        from footprinter.ingest.adapters.local_files import LocalFilesAdapter

        cursor = mock_db.conn.cursor.return_value
        cursor.fetchone.return_value = (1234,)

        adapter = LocalFilesAdapter()
        result = adapter.status(mock_db)

        assert isinstance(result, dict)
        assert result["local_files"] == 1234


# ===========================================================================
# ChatAdapter — _on_progress cleanup
# ===========================================================================


class TestChatAdapterOnProgress:
    """_on_progress is on PipeContext, not in the config dict."""

    @patch("footprinter.ingest.adapters.chat.ChatIndexer")
    def test_on_progress_not_in_config(self, MockIndexer, mock_db):
        from footprinter.ingest.adapters.chat import ChatAdapter

        mock_indexer = MockIndexer.return_value
        mock_indexer.get_stats.return_value = {"total_chats": 0, "total_messages": 0}

        ctx = PipeContext(source_config={})
        adapter = ChatAdapter()
        result = adapter.run(mock_db, ctx)

        # Adapter should complete without error; _on_progress lives on ctx, not in source_config
        assert result is not None
