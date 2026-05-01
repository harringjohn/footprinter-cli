"""Tests for progress indicator callbacks in file indexing and pipeline threading."""

from unittest.mock import MagicMock, patch


class TestFileIndexerOnProgress:
    """FileIndexer.index_files() calls on_progress with cumulative counts."""

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    @patch("footprinter.ingest.file_indexer.ContentExtractor")
    @patch("footprinter.ingest.file_indexer.FileScanner")
    @patch("footprinter.ingest.file_indexer.get_config")
    def test_calls_on_progress_per_file(
        self, mock_get_config, mock_scanner_cls, mock_extractor_cls, mock_insert, mock_mark_removed
    ):
        """on_progress called with increasing count per file processed."""
        from footprinter.ingest.file_indexer import FileIndexer

        mock_get_config.return_value = {"directories": ["~/Work"]}

        # Mock scanner to yield 3 file metadata dicts
        mock_scanner = MagicMock()
        mock_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_size": 100},
                {"file_path": "/b.txt", "file_size": 200},
                {"file_path": "/c.txt", "file_size": 300},
            ]
        )
        mock_scanner_cls.return_value = mock_scanner

        # Mock content extractor
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = "preview"
        mock_extractor_cls.return_value = mock_extractor

        # Mock database
        mock_db = MagicMock()
        mock_db.conn = MagicMock()

        on_progress = MagicMock()

        indexer = FileIndexer(db=mock_db)
        indexer.index_files(on_progress=on_progress)

        # on_progress called 3 times with cumulative counts
        assert on_progress.call_count == 3
        counts = [c[0][0] for c in on_progress.call_args_list]
        assert counts[0] <= counts[1] <= counts[2]
        assert counts[-1] == 3  # total files processed

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    @patch("footprinter.ingest.file_indexer.ContentExtractor")
    @patch("footprinter.ingest.file_indexer.FileScanner")
    @patch("footprinter.ingest.file_indexer.get_config")
    def test_no_error_without_callback(
        self, mock_get_config, mock_scanner_cls, mock_extractor_cls, mock_insert, mock_mark_removed
    ):
        """on_progress=None (default) — no error, backward compatible."""
        from footprinter.ingest.file_indexer import FileIndexer

        mock_get_config.return_value = {"directories": ["~/Work"]}

        mock_scanner = MagicMock()
        mock_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_size": 100},
            ]
        )
        mock_scanner_cls.return_value = mock_scanner

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = "preview"
        mock_extractor_cls.return_value = mock_extractor

        mock_db = MagicMock()
        mock_db.conn = MagicMock()

        indexer = FileIndexer(db=mock_db)
        result = indexer.index_files()  # No on_progress arg

        assert result["inserted"] == 1

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    @patch("footprinter.ingest.file_indexer.ContentExtractor")
    @patch("footprinter.ingest.file_indexer.FileScanner")
    @patch("footprinter.ingest.file_indexer.get_config")
    def test_on_progress_called_on_error(
        self, mock_get_config, mock_scanner_cls, mock_extractor_cls, mock_insert, mock_mark_removed
    ):
        """on_progress fires on every item, including ones that raise during extraction."""
        from footprinter.ingest.file_indexer import FileIndexer

        mock_get_config.return_value = {"directories": ["~/Work"]}

        # 3 files — the 2nd will raise during content extraction
        mock_scanner = MagicMock()
        mock_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_size": 100},
                {"file_path": "/b.txt", "file_size": 200},
                {"file_path": "/c.txt", "file_size": 300},
            ]
        )
        mock_scanner_cls.return_value = mock_scanner

        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = [
            "preview",  # /a.txt succeeds
            RuntimeError("corrupt file"),  # /b.txt errors
            "preview",  # /c.txt succeeds
        ]
        mock_extractor_cls.return_value = mock_extractor

        mock_db = MagicMock()
        mock_db.conn = MagicMock()

        on_progress = MagicMock()

        indexer = FileIndexer(db=mock_db)
        indexer.index_files(on_progress=on_progress)

        # on_progress must fire for ALL 3 items, not just the 2 successes
        assert on_progress.call_count == 3
        counts = [c[0][0] for c in on_progress.call_args_list]
        assert counts[-1] == 3
