"""
Tests for FileIndexer.

Vectorization moved out of file ingest — it now runs as a
follow-up stage (footprinter.ingest.processing.run_vectorization). These
tests pin the contracts the indexer still owns: config delegation,
incremental cutoff, vector-store init for stale-file cleanup, the
"no inline vectorization" guarantee for _insert_batch, insert/update
result semantics, count aggregation, content-extraction gating, and
per-file logging.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from footprinter.db import files as files_db


@pytest.fixture(autouse=True)
def _mock_chromadb():
    """Make footprinter.semantic importable without chromadb installed."""
    mods = {}
    for name in ("chromadb", "chromadb.utils", "chromadb.utils.embedding_functions", "onnxruntime"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            if name == "chromadb.utils.embedding_functions":
                mod.ONNXMiniLM_L6_V2 = lambda: None
            sys.modules[name] = mod
            mods[name] = mod
    yield
    for name in mods:
        sys.modules.pop(name, None)


def test_file_indexer_importable():
    from footprinter.ingest.file_indexer import FileIndexer

    assert FileIndexer is not None


class TestFileIndexerDefaultConfig:
    """Test that FileIndexer delegates config loading to source_registry.get_config()."""

    def test_default_config_uses_get_config(self):
        """FileIndexer() without config_path should delegate to source_registry.get_config()."""
        from footprinter.ingest.file_indexer import FileIndexer

        with (
            patch("footprinter.ingest.file_indexer.get_config") as mock_gc,
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner"),
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            mock_gc.return_value = {"scan_directories": []}

            FileIndexer()

            mock_gc.assert_called_once_with(None)

    def test_explicit_config_path_passed_to_get_config(self):
        """FileIndexer(config_path='custom.yaml') should forward the path to get_config()."""
        from footprinter.ingest.file_indexer import FileIndexer

        with (
            patch("footprinter.ingest.file_indexer.get_config") as mock_gc,
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner"),
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            mock_gc.return_value = {"scan_directories": []}

            FileIndexer(config_path="custom.yaml")

            mock_gc.assert_called_once_with("custom.yaml")


class TestIncrementalLastRun:
    """Test that FileIndexer uses last_run param for incremental cutoff."""

    def test_incremental_uses_last_run_from_param(self):
        """FileIndexer(last_run=datetime) passes that datetime to FileScanner as since_datetime."""
        from datetime import datetime

        from footprinter.ingest.file_indexer import FileIndexer

        cutoff = datetime(2026, 4, 1, 12, 0, 0)

        with (
            patch("footprinter.ingest.file_indexer.get_config") as mock_gc,
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner") as MockScanner,
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            mock_gc.return_value = {"scan_directories": []}

            FileIndexer(last_run=cutoff)

            MockScanner.assert_called_once()
            _, kwargs = MockScanner.call_args
            assert kwargs["since_datetime"] == cutoff

    def test_incremental_no_last_run_falls_back_to_full(self):
        """FileIndexer(last_run=None) passes since_datetime=None — full scan."""
        from footprinter.ingest.file_indexer import FileIndexer

        with (
            patch("footprinter.ingest.file_indexer.get_config") as mock_gc,
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner") as MockScanner,
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            mock_gc.return_value = {"scan_directories": []}

            FileIndexer(last_run=None)

            MockScanner.assert_called_once()
            _, kwargs = MockScanner.call_args
            assert kwargs["since_datetime"] is None


class TestVectorStoreInitWarning:
    """Test that _get_vector_store() logs a warning on initialization failure."""

    def _make_indexer(self):
        from footprinter.ingest.file_indexer import FileIndexer

        indexer = FileIndexer.__new__(FileIndexer)
        indexer.db = MagicMock()
        indexer._vector_store = None
        return indexer

    def test_vector_store_import_error_logs_warning(self):
        """ImportError during vector store init should log a warning and return None."""
        indexer = self._make_indexer()

        with (
            patch("footprinter.ingest.file_indexer.logger") as mock_logger,
            patch.dict(sys.modules, {"footprinter.semantic.vector_store": None}),
        ):
            # Force ImportError by removing the module
            indexer._vector_store = None
            with patch(
                "footprinter.ingest.file_indexer.VectorStore",
                side_effect=ImportError("No module named 'chromadb'"),
                create=True,
            ):
                # Reimport will raise ImportError from the try block
                result = indexer._get_vector_store()

        assert result is None
        mock_logger.warning.assert_called_once()
        assert "vector_store" in str(mock_logger.warning.call_args)

    def test_vector_store_runtime_error_logs_warning(self):
        """RuntimeError during vector store init should log a warning and return None."""
        indexer = self._make_indexer()

        with (
            patch("footprinter.ingest.file_indexer.logger") as mock_logger,
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                side_effect=RuntimeError("ChromaDB failed"),
            ),
        ):
            result = indexer._get_vector_store()

        assert result is None
        mock_logger.warning.assert_called_once()
        assert "ChromaDB failed" in str(mock_logger.warning.call_args)

    def test_vector_store_success_no_warning(self):
        """Successful vector store init should not log any warning."""
        indexer = self._make_indexer()
        mock_store = MagicMock()

        with (
            patch("footprinter.ingest.file_indexer.logger") as mock_logger,
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_store,
            ),
        ):
            result = indexer._get_vector_store()

        assert result is mock_store
        mock_logger.warning.assert_not_called()


class TestInsertBatchVectorizationSkip:
    """_insert_batch() never vectorizes inline; commits exactly once."""

    def _make_indexer(self):
        """Create a FileIndexer instance without requiring config/db."""
        from footprinter.ingest.file_indexer import FileIndexer

        indexer = FileIndexer.__new__(FileIndexer)
        indexer.db = MagicMock()
        indexer._vector_store = None
        indexer.incremental = False
        return indexer

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 42))
    def test_insert_batch_does_not_call_vector_store(self, mock_insert):
        """_insert_batch() must not touch the vector store.

        Vectorization runs as a separate follow-up stage via
        footprinter.ingest.processing.run_vectorization. The fast ingest pass
        should only insert rows and commit — never embed. We pin this by
        assigning a sentinel mock to both `_vector_store` (the direct
        attribute) and patching `_get_vector_store` (the lazy getter), then
        asserting neither was touched. Catches regressions that reach the
        vector store by either path.
        """
        indexer = self._make_indexer()
        sentinel_store = MagicMock()
        indexer._vector_store = sentinel_store

        batch = [{"file_path": "/some/file.txt", "path": "/some/file.txt"}]

        with patch.object(indexer, "_get_vector_store") as mock_getter:
            indexer._insert_batch(batch)

        mock_insert.assert_called_once()
        mock_getter.assert_not_called()
        assert sentinel_store.mock_calls == [], (
            f"_insert_batch must not touch the vector store; got calls: "
            f"{sentinel_store.mock_calls}"
        )

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    def test_insert_batch_commits_once(self, mock_insert):
        """_insert_batch() should call db.conn.commit() exactly once at the end."""
        indexer = self._make_indexer()

        batch = [
            {"file_path": "/a.txt", "path": "/a.txt"},
            {"file_path": "/b.txt", "path": "/b.txt"},
            {"file_path": "/c.txt", "path": "/c.txt"},
        ]

        indexer._insert_batch(batch)

        indexer.db.conn.commit.assert_called_once()


class TestInsertFileDoesNotCommit:
    """insert_file() must not call conn.commit() — callers own the commit boundary."""

    def test_insert_file_does_not_commit(self, tmp_path):
        """insert_file() should not auto-commit; data should be uncommitted until caller commits."""
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        # Ensure WAL mode is off so uncommitted data is truly invisible to other connections
        db.conn.execute("PRAGMA journal_mode=DELETE")
        db.conn.commit()

        file_data = {
            "file_path": "/tmp/test/file.txt",
            "file_name": "file.txt",
            "file_type": ".txt",
            "file_size": 100,
            "created_at": "2024-01-01",
            "modified_at": "2024-01-01",
        }

        files_db.insert_file(db.conn, file_data)

        # Open a second connection — if insert_file() committed, the row is visible
        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn2.close()

        assert count == 0, (
            f"insert_file() auto-committed — row visible from second connection "
            f"({count} row(s)). It should leave commit to the caller."
        )


class TestInsertFileResultType:
    """Test that insert_file() returns ('inserted', id) / ('updated', id) / None."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create an in-memory Database with schema initialized."""
        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        return db

    def _make_file_data(self, path="/tmp/test/file.txt", name="file.txt"):
        return {
            "file_path": path,
            "file_name": name,
            "file_type": ".txt",
            "file_size": 100,
            "created_at": "2024-01-01",
            "modified_at": "2024-01-01",
        }

    def test_insert_file_returns_inserted_tuple(self, db):
        """New file → ('inserted', <id>)."""
        result = files_db.insert_file(db.conn, self._make_file_data())
        assert isinstance(result, tuple)
        assert result[0] == "inserted"
        assert isinstance(result[1], int)

    def test_insert_file_returns_updated_tuple(self, db):
        """Duplicate file → ('updated', <id>)."""
        data = self._make_file_data()
        first = files_db.insert_file(db.conn, data)
        second = files_db.insert_file(db.conn, data)
        assert isinstance(second, tuple)
        assert second[0] == "updated"
        assert second[1] == first[1]  # same file_id

    def test_insert_file_clears_vectorized_at_on_update(self, db):
        """UPDATE clears vectorized_at when existing sha256 is NULL (hash newly available).

        The CASE expression in insert_file's UPDATE path clears vectorized_at
        when the existing row has no sha256_hash (cannot prove content unchanged).
        """
        data = self._make_file_data()
        first = files_db.insert_file(db.conn, data)
        file_id = first[1]

        # Simulate a prior successful vectorization pass.
        db.conn.execute(
            "UPDATE files SET vectorized_at = '2024-06-01T00:00:00', vectorized_chunks = 4 WHERE id = ?",
            (file_id,),
        )
        db.conn.commit()

        # Re-insert with changed content (size + modified_at) → "updated".
        updated_data = self._make_file_data()
        updated_data["modified_at"] = "2025-09-12"
        updated_data["file_size"] = 555
        updated_data["sha256_hash"] = "deadbeef"
        result = files_db.insert_file(db.conn, updated_data)
        assert result == ("updated", file_id)

        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        assert row["vectorized_at"] is None, (
            "UPDATE must clear vectorized_at so the row is picked up by "
            "run_vectorization's `vectorized_at IS NULL` predicate."
        )
        assert row["vectorized_chunks"] == 0

    def test_insert_file_reactivates_removed_path(self, db):
        """Re-inserting at a removed path should reactivate the record, not skip it."""
        data = self._make_file_data()
        result = files_db.insert_file(db.conn, data)
        original_id = result[1]

        # Mark as removed (simulates file deletion)
        db.conn.execute(
            "UPDATE files SET status = 'removed', status_reason = 'test_removal' WHERE id = ?",
            (original_id,),
        )
        db.conn.commit()

        # Re-insert with updated metadata
        updated_data = self._make_file_data()
        updated_data["modified_at"] = "2025-06-15"
        updated_data["file_size"] = 999
        result2 = files_db.insert_file(db.conn, updated_data)

        # Should reactivate, not skip
        assert result2 is not None, "insert_file() returned None — removed path was skipped"
        assert result2[0] == "inserted", f"Expected 'inserted' (reactivated), got '{result2[0]}'"
        assert result2[1] == original_id, "Should reuse the same row, not create a new one"

        # Verify DB state
        row = db.conn.execute(
            "SELECT status, status_reason, modified_at, size_bytes FROM files WHERE id = ?",
            (original_id,),
        ).fetchone()
        assert row["status"] == "listed", f"Status should be 'listed', got '{row['status']}'"
        assert row["modified_at"] == "2025-06-15"


class TestInsertBatchCounts:
    """Test that _insert_batch() returns (inserted, updated, skipped) counts."""

    def _make_indexer(self):
        from footprinter.ingest.file_indexer import FileIndexer

        indexer = FileIndexer.__new__(FileIndexer)
        indexer.db = MagicMock()
        indexer._vector_store = None
        indexer.incremental = False
        return indexer

    @patch("footprinter.ingest.file_indexer.files_db.insert_file")
    def test_insert_batch_counts_inserts_and_updates(self, mock_insert):
        """_insert_batch returns (inserted, updated, skipped, unchanged, touched_ids) tuple."""
        indexer = self._make_indexer()
        mock_insert.side_effect = [
            ("inserted", 1),
            ("updated", 2),
            None,  # skipped
        ]

        batch = [
            {"file_path": "/a.txt"},
            {"file_path": "/b.txt"},
            {"file_path": "/c.txt"},
        ]

        result = indexer._insert_batch(batch)

        assert result == (1, 1, 1, 0, [1, 2])  # 1 inserted, 1 updated, 1 skipped, 0 unchanged, touched=[1,2]

    @patch("footprinter.ingest.file_indexer.files_db.insert_file")
    def test_insert_batch_counts_reactivated_as_inserted(self, mock_insert):
        """Reactivated files (removed→active) should count as 'inserted', not 'skipped'."""
        indexer = self._make_indexer()
        mock_insert.side_effect = [
            ("inserted", 10),  # reactivated (was removed, now re-indexed)
            ("updated", 20),  # normal update
        ]

        batch = [
            {"file_path": "/reactivated.txt"},
            {"file_path": "/existing.txt"},
        ]

        result = indexer._insert_batch(batch)

        assert result == (1, 1, 0, 0, [10, 20])

    @patch("footprinter.ingest.file_indexer.files_db.insert_file")
    def test_insert_batch_counts_unchanged(self, mock_insert):
        """'unchanged' results increment the counter but are excluded from touched_ids.

        Vectorization moved to a follow-up stage. Backfilling of
        unchanged-but-not-yet-vectorized files now happens in
        ``run_vectorization`` via the ``vectorized_at IS NULL`` query.
        """
        indexer = self._make_indexer()
        sentinel_store = MagicMock()
        indexer._vector_store = sentinel_store
        mock_insert.side_effect = [
            ("inserted", 1),
            ("unchanged", 2),
            ("unchanged", 3),
        ]

        batch = [
            {"file_path": "/new.txt"},
            {"file_path": "/same1.txt"},
            {"file_path": "/same2.txt"},
        ]

        with patch.object(indexer, "_get_vector_store") as mock_getter:
            result = indexer._insert_batch(batch)

        assert result == (1, 0, 0, 2, [1])  # only inserted file in touched_ids
        mock_getter.assert_not_called()
        assert sentinel_store.mock_calls == []


class TestInsertBatchTouchedIds:
    """_insert_batch() returns touched file IDs for scoped vectorization."""

    def _make_indexer(self):
        from footprinter.ingest.file_indexer import FileIndexer

        indexer = FileIndexer.__new__(FileIndexer)
        indexer.db = MagicMock()
        indexer._vector_store = None
        indexer.incremental = False
        return indexer

    @patch("footprinter.ingest.file_indexer.files_db.insert_file")
    def test_insert_batch_returns_touched_ids(self, mock_insert):
        """touched_ids includes IDs for inserted and updated, not unchanged or skipped."""
        indexer = self._make_indexer()
        mock_insert.side_effect = [
            ("inserted", 10),
            ("updated", 20),
            ("unchanged", 30),
            None,  # skipped
            ("inserted", 50),
        ]

        batch = [{"file_path": f"/{i}.txt"} for i in range(5)]
        _, _, _, _, touched_ids = indexer._insert_batch(batch)

        assert touched_ids == [10, 20, 50]

    @patch("footprinter.ingest.file_indexer.files_db.insert_file")
    def test_insert_batch_empty_batch_returns_empty_touched(self, mock_insert):
        """Empty batch returns empty touched_ids list."""
        indexer = self._make_indexer()
        result = indexer._insert_batch([])
        assert result == (0, 0, 0, 0, [])


class TestIndexFilesCountDict:
    """Test that index_files() returns a dict with inserted/updated/skipped keys."""

    def _make_indexer(self):
        from footprinter.ingest.file_indexer import FileIndexer

        with (
            patch(
                "footprinter.ingest.file_indexer.get_config",
                return_value={
                    "scan_directories": [],
                },
            ),
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner"),
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            indexer = FileIndexer()
        return indexer

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    def test_index_files_returns_count_dict(self, mock_mark_removed):
        """index_files() returns dict with inserted/updated/skipped/errors/touched_file_ids keys."""
        indexer = self._make_indexer()
        indexer.file_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_name": "a.txt"},
                {"file_path": "/b.txt", "file_name": "b.txt"},
                {"file_path": "/c.txt", "file_name": "c.txt"},
            ]
        )

        with (
            patch.object(indexer, "_insert_batch", return_value=(2, 1, 0, 5, [10, 20])),
            patch.object(indexer, "content_extractor") as mock_ce,
        ):
            mock_ce.extract.return_value = "preview"
            result = indexer.index_files()

        assert isinstance(result, dict)
        assert "inserted" in result
        assert "updated" in result
        assert "skipped" in result
        assert "errors" in result
        assert "unchanged" in result
        assert result["unchanged"] == 5
        assert "touched_file_ids" in result
        assert result["touched_file_ids"] == [10, 20]

class TestVectorCleanupOnRemoval:
    """Test that index_files() deletes vectors for files marked as removed."""

    def _make_indexer(self):
        from footprinter.ingest.file_indexer import FileIndexer

        with (
            patch(
                "footprinter.ingest.file_indexer.get_config",
                return_value={
                    "scan_directories": [],
                },
            ),
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner"),
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            indexer = FileIndexer()
        return indexer

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=[10, 20])
    def test_index_files_deletes_vectors_for_removed(self, mock_mark_removed):
        """index_files() calls store.delete_file for each ID returned by mark_removed_files."""
        indexer = self._make_indexer()
        indexer.file_scanner.scan_all_directories.return_value = iter([])

        mock_store = MagicMock()

        with patch.object(indexer, "_get_vector_store", return_value=mock_store):
            indexer.index_files()

        assert mock_store.delete_file.call_count == 2
        mock_store.delete_file.assert_any_call(10)
        mock_store.delete_file.assert_any_call(20)

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=[10, 20])
    def test_index_files_vector_cleanup_skipped_when_no_store(self, mock_mark_removed):
        """Vector cleanup is skipped gracefully when no vector store is available."""
        indexer = self._make_indexer()
        indexer.file_scanner.scan_all_directories.return_value = iter([])

        with patch.object(indexer, "_get_vector_store", return_value=None):
            result = indexer.index_files()

        # Should complete without error
        assert isinstance(result, dict)


class TestContentExtractionGating:
    """Content extraction should only run when indexing.content_snippets is enabled."""

    def _make_indexer(self, content_snippets=False):
        from footprinter.ingest.file_indexer import FileIndexer

        config = {
            "scan_directories": [],
            "indexing": {"content_snippets": content_snippets},
        }
        with (
            patch("footprinter.ingest.file_indexer.get_config", return_value=config),
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner"),
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            indexer = FileIndexer()
        return indexer

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    def test_extraction_skipped_when_disabled(self, mock_mark_removed):
        """content_extractor.extract() is NOT called when content_snippets is false."""
        indexer = self._make_indexer(content_snippets=False)
        indexer.file_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_name": "a.txt"},
            ]
        )

        with patch.object(indexer, "_insert_batch", return_value=(1, 0, 0, 0, [])):
            indexer.index_files()

        indexer.content_extractor.extract.assert_not_called()

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    def test_extraction_runs_when_enabled(self, mock_mark_removed):
        """content_extractor.extract() IS called when content_snippets is true."""
        indexer = self._make_indexer(content_snippets=True)
        indexer.file_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_name": "a.txt"},
            ]
        )

        with patch.object(indexer, "_insert_batch", return_value=(1, 0, 0, 0, [])):
            indexer.index_files()

        indexer.content_extractor.extract.assert_called_once()

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    def test_content_preview_none_when_disabled(self, mock_mark_removed):
        """content_preview is None in file metadata when snippets disabled."""
        indexer = self._make_indexer(content_snippets=False)
        indexer.file_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_name": "a.txt"},
            ]
        )

        batches = []

        def capture_batch(batch, *args, **kwargs):
            batches.extend(batch)
            return (1, 0, 0, 0, [])

        with patch.object(indexer, "_insert_batch", side_effect=capture_batch):
            indexer.index_files()

        assert len(batches) == 1
        assert batches[0]["content_preview"] is None

    @patch("footprinter.ingest.file_indexer.files_db.mark_removed_files", return_value=0)
    def test_default_config_skips_extraction(self, mock_mark_removed):
        """Missing indexing.content_snippets key defaults to disabled."""
        from footprinter.ingest.file_indexer import FileIndexer

        config = {"scan_directories": []}  # No indexing section at all
        with (
            patch("footprinter.ingest.file_indexer.get_config", return_value=config),
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner"),
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
        ):
            indexer = FileIndexer()

        indexer.file_scanner.scan_all_directories.return_value = iter(
            [
                {"file_path": "/a.txt", "file_name": "a.txt"},
            ]
        )

        with patch.object(indexer, "_insert_batch", return_value=(1, 0, 0, 0, [])):
            indexer.index_files()

        indexer.content_extractor.extract.assert_not_called()


class TestInsertBatchPerFileLogging:
    """_insert_batch() logs each inserted/updated file path.

    Incremental mode → INFO; full mode → DEBUG; unchanged/skipped → silent.
    Paths under $HOME are abbreviated to ~/...; others stay verbatim.
    """

    def _make_indexer(self, *, incremental: bool):
        from footprinter.ingest.file_indexer import FileIndexer

        indexer = FileIndexer.__new__(FileIndexer)
        indexer.db = MagicMock()
        indexer._vector_store = None
        indexer.incremental = incremental
        return indexer

    @staticmethod
    def _messages(calls):
        """Format mock log calls as ready-to-search strings."""
        rendered = []
        for call in calls:
            args, _kwargs = call
            if not args:
                continue
            fmt = args[0]
            try:
                rendered.append(fmt % args[1:] if len(args) > 1 else fmt)
            except TypeError:
                rendered.append(" ".join(str(a) for a in args))
        return rendered

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    def test_incremental_logs_inserted_path_at_info(self, _mock_insert):
        import os

        indexer = self._make_indexer(incremental=True)
        home = os.path.expanduser("~")
        batch = [{"file_path": f"{home}/Documents/foo.txt"}]

        with patch("footprinter.ingest.file_indexer.logger") as mock_logger:
            indexer._insert_batch(batch)

        messages = self._messages(mock_logger.info.call_args_list)
        assert any(
            "~/Documents/foo.txt" in m and "inserted" in m for m in messages
        ), f"expected per-file INFO log with abbreviated path; got {messages}"

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("updated", 2))
    def test_incremental_logs_updated_path_at_info(self, _mock_insert):
        import os

        indexer = self._make_indexer(incremental=True)
        home = os.path.expanduser("~")
        batch = [{"file_path": f"{home}/Documents/bar.txt"}]

        with patch("footprinter.ingest.file_indexer.logger") as mock_logger:
            indexer._insert_batch(batch)

        messages = self._messages(mock_logger.info.call_args_list)
        assert any(
            "~/Documents/bar.txt" in m and "updated" in m for m in messages
        ), f"expected per-file INFO log with abbreviated path; got {messages}"

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("unchanged", 3))
    def test_incremental_does_not_log_unchanged(self, _mock_insert):
        import os

        indexer = self._make_indexer(incremental=True)
        home = os.path.expanduser("~")
        path = f"{home}/Documents/same.txt"

        with patch("footprinter.ingest.file_indexer.logger") as mock_logger:
            indexer._insert_batch([{"file_path": path}])

        info_messages = self._messages(mock_logger.info.call_args_list)
        debug_messages = self._messages(mock_logger.debug.call_args_list)
        assert not any(path in m or "~/Documents/same.txt" in m for m in info_messages), (
            f"unchanged file should not log at INFO; got {info_messages}"
        )
        assert not any(path in m or "~/Documents/same.txt" in m for m in debug_messages), (
            f"unchanged file should not log at DEBUG; got {debug_messages}"
        )

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=None)
    def test_incremental_does_not_log_skipped(self, _mock_insert):
        import os

        indexer = self._make_indexer(incremental=True)
        home = os.path.expanduser("~")
        path = f"{home}/Documents/skipped.txt"

        with patch("footprinter.ingest.file_indexer.logger") as mock_logger:
            indexer._insert_batch([{"file_path": path}])

        info_messages = self._messages(mock_logger.info.call_args_list)
        debug_messages = self._messages(mock_logger.debug.call_args_list)
        assert not any(path in m or "~/Documents/skipped.txt" in m for m in info_messages)
        assert not any(path in m or "~/Documents/skipped.txt" in m for m in debug_messages)

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    def test_full_mode_logs_per_file_at_debug(self, _mock_insert):
        import os

        indexer = self._make_indexer(incremental=False)
        home = os.path.expanduser("~")
        batch = [{"file_path": f"{home}/Documents/full.txt"}]

        with patch("footprinter.ingest.file_indexer.logger") as mock_logger:
            indexer._insert_batch(batch)

        info_messages = self._messages(mock_logger.info.call_args_list)
        debug_messages = self._messages(mock_logger.debug.call_args_list)
        assert not any("~/Documents/full.txt" in m for m in info_messages), (
            f"full mode must not log per-file at INFO; got {info_messages}"
        )
        assert any(
            "~/Documents/full.txt" in m and "inserted" in m for m in debug_messages
        ), f"full mode should log per-file at DEBUG; got {debug_messages}"

    @patch("footprinter.ingest.file_indexer.files_db.insert_file", return_value=("inserted", 1))
    def test_path_outside_home_logged_verbatim(self, _mock_insert):
        indexer = self._make_indexer(incremental=True)
        batch = [{"file_path": "/etc/something.conf"}]

        with patch("footprinter.ingest.file_indexer.logger") as mock_logger:
            indexer._insert_batch(batch)

        messages = self._messages(mock_logger.info.call_args_list)
        assert any("/etc/something.conf" in m for m in messages), (
            f"path outside $HOME should be logged verbatim; got {messages}"
        )
        assert not any("~/etc/something.conf" in m for m in messages), (
            f"path outside $HOME should not gain a ~/ prefix; got {messages}"
        )


class TestMovedFileWiring:
    """FileIndexer loads known paths and passes them to FileScanner."""

    def test_incremental_passes_known_paths_to_scanner(self):
        """FileIndexer(last_run=datetime) loads known paths and passes them to FileScanner."""
        from datetime import datetime

        from footprinter.ingest.file_indexer import FileIndexer

        cutoff = datetime(2026, 4, 1, 12, 0, 0)
        fake_paths = {"/tmp/a.txt", "/tmp/b.txt"}

        with (
            patch("footprinter.ingest.file_indexer.get_config") as mock_gc,
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner") as MockScanner,
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
            patch("footprinter.ingest.file_indexer.files_db.get_known_local_paths", return_value=fake_paths),
        ):
            mock_gc.return_value = {"scan_directories": []}

            FileIndexer(last_run=cutoff)

            MockScanner.assert_called_once()
            _, kwargs = MockScanner.call_args
            assert kwargs["known_paths"] == fake_paths

    def test_full_scan_passes_none_known_paths(self):
        """FileIndexer(last_run=None) passes known_paths=None — no DB query needed."""
        from footprinter.ingest.file_indexer import FileIndexer

        with (
            patch("footprinter.ingest.file_indexer.get_config") as mock_gc,
            patch("footprinter.ingest.file_indexer.Database"),
            patch("footprinter.ingest.file_indexer.FileScanner") as MockScanner,
            patch("footprinter.ingest.file_indexer.ContentExtractor"),
            patch("footprinter.ingest.file_indexer.files_db.get_known_local_paths") as mock_known,
        ):
            mock_gc.return_value = {"scan_directories": []}

            FileIndexer(last_run=None)

            mock_known.assert_not_called()
            MockScanner.assert_called_once()
            _, kwargs = MockScanner.call_args
            assert kwargs.get("known_paths") is None
