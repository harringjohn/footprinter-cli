"""Tests for footprinter.ingest.processing runners (FPR-1721).

Covers the ``run_vectorization`` post-ingest stage that replaces inline
per-row vectorization in ``FileIndexer._insert_batch``.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


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


def _make_db(tmp_path):
    """Create a Database with schema initialized."""
    from footprinter.ingest.database import Database

    return Database(db_path=str(tmp_path / "test.db"))


def _insert_file(db, *, file_path, status="listed", vectorized_at=None, metadata=None):
    """Insert a minimal files row; return its id."""
    from footprinter.db import files as files_db

    data = {
        "file_path": file_path,
        "file_name": file_path.rsplit("/", 1)[-1],
        "file_type": ".txt",
        "file_size": 100,
        "created_at": "2024-01-01",
        "modified_at": "2024-01-01",
    }
    if metadata is not None:
        data["metadata"] = metadata
    result = files_db.insert_file(db.conn, data)
    assert result is not None
    file_id = result[1]
    if status != "listed" or vectorized_at is not None:
        db.conn.execute(
            "UPDATE files SET status = ?, vectorized_at = ? WHERE id = ?",
            (status, vectorized_at, file_id),
        )
    db.conn.commit()
    return file_id


class TestRunVectorization:
    """run_vectorization queries the manifest and embeds via the vector store."""

    def test_processes_unvectorized_listed_files(self, tmp_path):
        """Only rows with vectorized_at IS NULL and status='listed' are embedded."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        unvec_id = _insert_file(db, file_path=str(tmp_path / "new.txt"))
        already_vec_id = _insert_file(
            db, file_path=str(tmp_path / "old.txt"), vectorized_at="2024-06-01T00:00:00"
        )
        removed_id = _insert_file(db, file_path=str(tmp_path / "gone.txt"), status="removed")

        # The unvectorized file must exist on disk so FullContentExtractor can read it.
        (tmp_path / "new.txt").write_text("hello world")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0  # disable size-cap check
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "hello world", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_store,
            ),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            result = run_vectorization(db)

        # Only the unvectorized listed file should be embedded.
        assert mock_store.upsert_file.call_count == 1
        called_file_id = mock_store.upsert_file.call_args[0][0]
        assert called_file_id == unvec_id

        # vectorized_at is now set for the embedded row.
        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (unvec_id,)
        ).fetchone()
        assert row["vectorized_at"] is not None
        assert row["vectorized_chunks"] == 1

        # Status of the result.
        assert result.status.value in ("completed", "completed_with_errors")
        assert result.data.get("vectorized_new", 0) == 1

        # The other two rows are untouched.
        already = db.conn.execute(
            "SELECT vectorized_at FROM files WHERE id = ?", (already_vec_id,)
        ).fetchone()
        assert already["vectorized_at"] == "2024-06-01T00:00:00"
        removed = db.conn.execute(
            "SELECT vectorized_at, status FROM files WHERE id = ?", (removed_id,)
        ).fetchone()
        assert removed["vectorized_at"] is None
        assert removed["status"] == "removed"

    def test_respects_disabled_flag(self, tmp_path):
        """When file_vectorization is disabled, no work is done — return skipped/info."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        _insert_file(db, file_path=str(tmp_path / "f.txt"))

        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=False),
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_store,
            ),
        ):
            result = run_vectorization(db)

        mock_store.upsert_file.assert_not_called()
        # Should be a skipped or info result — never errored.
        assert result.status.value in ("skipped", "info")

    def test_run_vectorization_records_skipped_large_files(self, tmp_path):
        """Files larger than the configured vectorize cap are skipped and
        listed in result.data with their path + size (FPR-1722)."""
        from footprinter.ingest.full_content_extractor import FullContentExtractor
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        big_path = tmp_path / "big.txt"
        big_path.write_bytes(b"z" * 8192)
        big_id = _insert_file(db, file_path=str(big_path))

        # Build a real extractor with a low cap so the on-disk big.txt is skipped.
        small_cap_extractor = FullContentExtractor(max_vectorize_size_bytes=1024)

        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_store,
            ),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=small_cap_extractor,
            ),
        ):
            result = run_vectorization(db)

        # The big file was not embedded.
        mock_store.upsert_file.assert_not_called()

        # Any prior vectors for this file_id are dropped (best-effort cleanup).
        mock_store.delete_file.assert_called_once_with(big_id)

        # vectorized_at is stamped (with chunks=0) so the row is not re-evaluated
        # every incremental run. Upstream ingest clears it if the file later
        # changes, giving us a chance to re-check whether it has shrunk.
        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (big_id,)
        ).fetchone()
        assert row["vectorized_at"] is not None
        assert row["vectorized_chunks"] == 0

        # Result data records the skip with path + size.
        assert result.data.get("vectorized_skipped_large") == 1
        skipped = result.data.get("skipped_large_files") or []
        assert len(skipped) == 1
        assert skipped[0]["path"] == str(big_path)
        assert skipped[0]["size_bytes"] == 8192

    def test_run_vectorization_normal_file_still_vectorized(self, tmp_path):
        """Regression: under-cap files are still vectorized (FPR-1722)."""
        from footprinter.ingest.full_content_extractor import FullContentExtractor
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        normal_path = tmp_path / "normal.txt"
        normal_path.write_text("hello")
        normal_id = _insert_file(db, file_path=str(normal_path))

        extractor = FullContentExtractor(max_vectorize_size_bytes=1024)

        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_store,
            ),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=extractor,
            ),
        ):
            result = run_vectorization(db)

        mock_store.upsert_file.assert_called_once()
        assert result.data.get("vectorized_new") == 1
        assert result.data.get("vectorized_skipped_large", 0) == 0
        row = db.conn.execute(
            "SELECT vectorized_at FROM files WHERE id = ?", (normal_id,)
        ).fetchone()
        assert row["vectorized_at"] is not None

    def test_skips_metadata_vectorize_zero(self, tmp_path):
        """Rows with metadata.vectorize == 0 are excluded even when enabled."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        opted_out_id = _insert_file(
            db,
            file_path=str(tmp_path / "noembed.txt"),
            metadata={"vectorize": 0},
        )
        (tmp_path / "noembed.txt").write_text("ignored")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "ignored", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_store,
            ),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db)

        mock_store.upsert_file.assert_not_called()
        row = db.conn.execute(
            "SELECT vectorized_at FROM files WHERE id = ?", (opted_out_id,)
        ).fetchone()
        assert row["vectorized_at"] is None
