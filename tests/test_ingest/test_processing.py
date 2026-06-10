"""Tests for footprinter.ingest.processing runners.

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

    def test_excludes_unlisted_files(self, tmp_path):
        """Files with status='unlisted' must not be vectorized."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "listed.txt").write_text("listed content")
        (tmp_path / "unlisted.txt").write_text("unlisted content")
        listed_id = _insert_file(db, file_path=str(tmp_path / "listed.txt"), status="listed")
        _insert_file(db, file_path=str(tmp_path / "unlisted.txt"), status="unlisted")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db)

        assert mock_store.upsert_file.call_count == 1
        called_file_id = mock_store.upsert_file.call_args[0][0]
        assert called_file_id == listed_id

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
        listed in result.data with their path + size."""
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
        """Regression: under-cap files are still vectorized."""
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

    def test_unlisted_included_when_config_allows(self, tmp_path):
        """When vectorize_statuses includes 'unlisted', those files are embedded."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "listed.txt").write_text("listed content")
        (tmp_path / "unlisted.txt").write_text("unlisted content")
        _insert_file(db, file_path=str(tmp_path / "listed.txt"), status="listed")
        _insert_file(db, file_path=str(tmp_path / "unlisted.txt"), status="unlisted")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
            patch(
                "footprinter.ingest.processing._get_vectorize_statuses",
                return_value=["listed", "unlisted"],
            ),
        ):
            run_vectorization(db)

        assert mock_store.upsert_file.call_count == 2


class TestScopedVectorization:
    """run_vectorization with file_ids scopes to specific files."""

    def test_scoped_vectorizes_only_given_ids(self, tmp_path):
        """When file_ids is provided, only those files are vectorized."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        (tmp_path / "c.txt").write_text("gamma")
        id_a = _insert_file(db, file_path=str(tmp_path / "a.txt"))
        id_b = _insert_file(db, file_path=str(tmp_path / "b.txt"))
        id_c = _insert_file(db, file_path=str(tmp_path / "c.txt"))

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            result = run_vectorization(db, file_ids=[id_a])

        assert mock_store.upsert_file.call_count == 1
        assert mock_store.upsert_file.call_args[0][0] == id_a
        assert result.data.get("vectorized_new") == 1

        row_b = db.conn.execute("SELECT vectorized_at FROM files WHERE id = ?", (id_b,)).fetchone()
        row_c = db.conn.execute("SELECT vectorized_at FROM files WHERE id = ?", (id_c,)).fetchone()
        assert row_b["vectorized_at"] is None
        assert row_c["vectorized_at"] is None

    def test_scoped_empty_ids_noop(self, tmp_path):
        """file_ids=[] means nothing to vectorize — early return."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        _insert_file(db, file_path=str(tmp_path / "x.txt"))

        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
        ):
            result = run_vectorization(db, file_ids=[])

        mock_store.upsert_file.assert_not_called()
        assert result.status.value == "completed"
        assert result.data.get("vectorized_new", 0) == 0

    def test_scoped_excludes_unlisted_files(self, tmp_path):
        """Unlisted file IDs in file_ids are excluded from vectorization."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "listed.txt").write_text("listed content")
        (tmp_path / "unlisted.txt").write_text("unlisted content")
        listed_id = _insert_file(db, file_path=str(tmp_path / "listed.txt"), status="listed")
        unlisted_id = _insert_file(db, file_path=str(tmp_path / "unlisted.txt"), status="unlisted")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db, file_ids=[listed_id, unlisted_id])

        assert mock_store.upsert_file.call_count == 1
        called_file_id = mock_store.upsert_file.call_args[0][0]
        assert called_file_id == listed_id

    def test_scoped_ignores_removed_status(self, tmp_path):
        """Even if a removed file's ID is in file_ids, it's still skipped."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "removed.txt").write_text("content")
        removed_id = _insert_file(db, file_path=str(tmp_path / "removed.txt"), status="removed")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db, file_ids=[removed_id])

        mock_store.upsert_file.assert_not_called()

    def test_scoped_skips_already_vectorized_unless_full_mode(self, tmp_path):
        """Scoped path respects full_mode: skips already-vectorized files when False."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "new.txt").write_text("new content")
        (tmp_path / "old.txt").write_text("old content")
        new_id = _insert_file(db, file_path=str(tmp_path / "new.txt"))
        old_id = _insert_file(
            db, file_path=str(tmp_path / "old.txt"), vectorized_at="2024-06-01T00:00:00"
        )

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db, file_ids=[new_id, old_id])

        assert mock_store.upsert_file.call_count == 1
        called_file_id = mock_store.upsert_file.call_args[0][0]
        assert called_file_id == new_id

        mock_store.reset_mock()
        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db, full_mode=True, file_ids=[new_id, old_id])

        assert mock_store.upsert_file.call_count == 2

    def test_none_file_ids_broad_query(self, tmp_path):
        """file_ids=None keeps the existing broad WHERE vectorized_at IS NULL behavior."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        _insert_file(db, file_path=str(tmp_path / "a.txt"))
        _insert_file(db, file_path=str(tmp_path / "b.txt"))

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            result = run_vectorization(db, file_ids=None)

        assert mock_store.upsert_file.call_count == 2
        assert result.data.get("vectorized_new") == 2

    def test_scoped_respects_config_statuses(self, tmp_path):
        """Scoped path includes unlisted files when config allows."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "listed.txt").write_text("listed content")
        (tmp_path / "unlisted.txt").write_text("unlisted content")
        listed_id = _insert_file(db, file_path=str(tmp_path / "listed.txt"), status="listed")
        unlisted_id = _insert_file(db, file_path=str(tmp_path / "unlisted.txt"), status="unlisted")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
            patch(
                "footprinter.ingest.processing._get_vectorize_statuses",
                return_value=["listed", "unlisted"],
            ),
        ):
            run_vectorization(db, file_ids=[listed_id, unlisted_id])

        assert mock_store.upsert_file.call_count == 2


class TestVectorizationPathParity:
    """The ingest follow-up (run_vectorization) and the doctor rebuild
    (_vectorize_files) must embed the same set of files under a widened
    semantic.vectorize_statuses — they now share one embed path."""

    def test_run_vectorization_and_doctor_agree_on_widened_statuses(self, tmp_path):
        """Both entry points embed listed + unlisted when config widens statuses.

        The doctor-rebuild half (selecting the identical set) is covered by
        TestVectorizeFilesHonorsStatuses in test_rebuild_vectors.py; this guard
        locks the ingest-follow-up half so the two paths cannot diverge.
        """
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "listed.txt").write_text("listed content")
        (tmp_path / "unlisted.txt").write_text("unlisted content")
        _insert_file(db, file_path=str(tmp_path / "listed.txt"), status="listed")
        _insert_file(db, file_path=str(tmp_path / "unlisted.txt"), status="unlisted")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
            patch(
                "footprinter.ingest.processing._get_vectorize_statuses",
                return_value=["listed", "unlisted"],
            ),
        ):
            run_vectorization(db)

        assert mock_store.upsert_file.call_count == 2, (
            "Ingest follow-up must embed both listed and unlisted under widened statuses"
        )


class TestChatVectorization:
    """run_vectorization delegates to vector_ops helpers for messages + chat_info."""

    def test_vectorizes_messages_when_chat_enabled(self, tmp_path):
        """When chat vectorization is enabled, run_vectorization calls _vectorize_messages."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.vector_ops._vectorize_messages",
                return_value={"done": 2, "interrupted": False},
            ) as mock_msgs,
            patch(
                "footprinter.ingest.vector_ops._vectorize_chat_info",
                return_value={"done": 0, "interrupted": False},
            ),
        ):
            result = run_vectorization(db)

        mock_msgs.assert_called_once()
        assert result.data["vectorized_messages_new"] == 2

    def test_vectorizes_chat_info_when_chat_enabled(self, tmp_path):
        """When chat vectorization is enabled, run_vectorization calls _vectorize_chat_info."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.vector_ops._vectorize_messages",
                return_value={"done": 0, "interrupted": False},
            ),
            patch(
                "footprinter.ingest.vector_ops._vectorize_chat_info",
                return_value={"done": 3, "interrupted": False},
            ) as mock_chat,
        ):
            result = run_vectorization(db)

        mock_chat.assert_called_once()
        assert result.data["vectorized_chat_info_new"] == 3

    def test_skips_chat_phases_when_disabled(self, tmp_path):
        """When chat vectorization is disabled, message/chat helpers are not called."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch("footprinter.ingest.vector_ops._vectorize_messages") as mock_msgs,
            patch("footprinter.ingest.vector_ops._vectorize_chat_info") as mock_chat,
        ):
            result = run_vectorization(db)

        mock_msgs.assert_not_called()
        mock_chat.assert_not_called()
        assert result.data.get("vectorized_messages_new", 0) == 0
        assert result.data.get("vectorized_chat_info_new", 0) == 0

    def test_chat_only_when_file_vectorization_disabled(self, tmp_path):
        """When only chat vectorization is enabled, files are skipped but messages/chats run."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        _insert_file(db, file_path=str(tmp_path / "f.txt"))

        mock_store = MagicMock()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.vector_ops._vectorize_messages",
                return_value={"done": 5, "interrupted": False},
            ) as mock_msgs,
            patch(
                "footprinter.ingest.vector_ops._vectorize_chat_info",
                return_value={"done": 1, "interrupted": False},
            ) as mock_chat,
        ):
            result = run_vectorization(db)

        # File vectorization did not run
        mock_store.upsert_file.assert_not_called()
        # Chat phases ran
        mock_msgs.assert_called_once()
        mock_chat.assert_called_once()
        # Result is completed, not skipped
        assert result.status.value == "completed"
        assert result.data["vectorized_messages_new"] == 5
        assert result.data["vectorized_chat_info_new"] == 1

    def test_shutdown_stops_chat_phases(self, tmp_path):
        """When _shutdown is set during file phase, chat phases are skipped."""
        import footprinter.ingest.processing as processing
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        (tmp_path / "f.txt").write_text("content")
        _insert_file(db, file_path=str(tmp_path / "f.txt"))

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]

        def trigger_shutdown(count):
            processing._shutdown = True

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
            patch("footprinter.ingest.vector_ops._vectorize_messages") as mock_msgs,
            patch("footprinter.ingest.vector_ops._vectorize_chat_info") as mock_chat,
        ):
            result = run_vectorization(db, on_progress=trigger_shutdown)

        processing._shutdown = False

        mock_msgs.assert_not_called()
        mock_chat.assert_not_called()
        assert result.data.get("interrupted") is True


class TestVectorizationInterruptSafety:
    """Periodic commits and graceful shutdown in run_vectorization."""

    def _setup_files(self, tmp_path, db, count):
        """Insert N file rows with corresponding disk files."""
        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.max_vectorize_size_bytes = 0
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "text", "chunk_index": 0, "total_chunks": 1}
        ]
        for i in range(count):
            p = tmp_path / f"file_{i:04d}.txt"
            p.write_text(f"content {i}")
            _insert_file(db, file_path=str(p))
        return mock_store, mock_extractor

    def test_periodic_commit_every_100_files(self, tmp_path):
        """commit() fires at least twice when processing >100 files."""
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        mock_store, mock_extractor = self._setup_files(tmp_path, db, 150)

        commit_count = {"n": 0}
        real_conn = db.conn

        class CommitCounter:
            """Proxy that counts commit() calls on the real connection."""

            def __getattr__(self, name):
                return getattr(real_conn, name)

            def commit(self):
                commit_count["n"] += 1
                real_conn.commit()

        db.conn = CommitCounter()

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            run_vectorization(db)

        db.conn = real_conn
        assert commit_count["n"] >= 2, f"Expected >=2 commits, got {commit_count['n']}"

    def test_shutdown_flag_commits_and_returns_early(self, tmp_path):
        """Setting _shutdown stops the loop and preserves committed progress."""
        import footprinter.ingest.processing as processing
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        mock_store, mock_extractor = self._setup_files(tmp_path, db, 200)

        def trigger_shutdown(count):
            if count >= 50:
                processing._shutdown = True

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            result = run_vectorization(db, on_progress=trigger_shutdown)

        processing._shutdown = False

        assert result.data.get("interrupted") is True
        done = db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE vectorized_at IS NOT NULL"
        ).fetchone()[0]
        remaining = db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE vectorized_at IS NULL"
        ).fetchone()[0]
        assert done <= 50
        assert remaining >= 150

    def test_interrupt_preserves_periodic_commit_progress(self, tmp_path):
        """Shutdown at 120 preserves the periodic commit at 100 plus the tail."""
        import footprinter.ingest.processing as processing
        from footprinter.ingest.processing import run_vectorization

        db = _make_db(tmp_path)
        mock_store, mock_extractor = self._setup_files(tmp_path, db, 250)

        def trigger_shutdown(count):
            if count >= 120:
                processing._shutdown = True

        with (
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore.get_instance", return_value=mock_store),
            patch(
                "footprinter.ingest.full_content_extractor.FullContentExtractor.from_config",
                return_value=mock_extractor,
            ),
        ):
            result = run_vectorization(db, on_progress=trigger_shutdown)

        processing._shutdown = False

        assert result.data.get("interrupted") is True
        done = db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE vectorized_at IS NOT NULL"
        ).fetchone()[0]
        assert done >= 100, f"Expected >=100 committed files, got {done}"


class TestEmbedOneFileContract:
    """Lock the per-outcome return contract of ``_embed_one_file``.

    The helper's second value used to be overloaded — a chunk count for
    ``"new"`` but a byte size for ``"skipped_large"`` — with a docstring that
    only described the chunk-count meaning. These tests assert the result
    exposes distinct, unambiguously-named fields so no single slot means two
    different things.
    """

    def test_skipped_missing_carries_no_count_or_size(self, tmp_path):
        """A None / non-existent path returns ``skipped_missing`` with chunks=0
        and no size — never a chunk count parked in a generic slot."""
        from footprinter.ingest.processing import _embed_one_file

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_conn = MagicMock()

        result = _embed_one_file(
            mock_store,
            mock_extractor,
            mock_conn,
            file_id=1,
            file_path=None,
            vectorize_cap=0,
            use_upsert=True,
        )

        assert result.outcome == "skipped_missing"
        assert result.chunks == 0
        assert result.size_bytes is None
        # Nothing was extracted or written for a missing path.
        mock_extractor.extract_with_chunking.assert_not_called()
        mock_store.upsert_file.assert_not_called()

    def test_skipped_large_reads_size_from_size_bytes_field(self, tmp_path):
        """An over-cap file returns ``skipped_large`` with the on-disk size in
        ``size_bytes`` (not a generic positional slot) and chunks=0."""
        from footprinter.ingest.processing import _embed_one_file

        big_path = tmp_path / "big.txt"
        big_path.write_bytes(b"z" * 4096)

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_conn = MagicMock()

        result = _embed_one_file(
            mock_store,
            mock_extractor,
            mock_conn,
            file_id=7,
            file_path=str(big_path),
            vectorize_cap=1024,
            use_upsert=True,
        )

        assert result.outcome == "skipped_large"
        # The byte size is read from the named size_bytes field.
        assert result.size_bytes == 4096
        # The oversize stamp writes vectorized_chunks = 0.
        assert result.chunks == 0
        # Over-cap files are not embedded.
        mock_extractor.extract_with_chunking.assert_not_called()
        mock_store.upsert_file.assert_not_called()

    def test_new_carries_chunk_count_in_chunks_field(self, tmp_path):
        """An under-cap file with non-empty chunks returns ``new`` with the
        chunk count in ``chunks`` and no size."""
        from footprinter.ingest.processing import _embed_one_file

        small_path = tmp_path / "small.txt"
        small_path.write_text("hello")

        mock_store = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.extract_with_chunking.return_value = ["c1", "c2", "c3"]
        mock_conn = MagicMock()

        result = _embed_one_file(
            mock_store,
            mock_extractor,
            mock_conn,
            file_id=11,
            file_path=str(small_path),
            vectorize_cap=0,
            use_upsert=True,
        )

        assert result.outcome == "new"
        # The chunk count is read from the named chunks field.
        assert result.chunks == 3
        assert result.size_bytes is None
        mock_store.upsert_file.assert_called_once()
