"""Tests for footprinter/semantic/vector_store.py and embeddings.py.

Embeddings tests merged from test_embeddings.py.
"""

import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub chromadb / onnxruntime / chromadb.config into sys.modules
# ---------------------------------------------------------------------------
_chromadb_mod = types.ModuleType("chromadb")
_chromadb_mod.PersistentClient = MagicMock
sys.modules.setdefault("chromadb", _chromadb_mod)

_chromadb_utils = types.ModuleType("chromadb.utils")
sys.modules.setdefault("chromadb.utils", _chromadb_utils)

_ef_mod = types.ModuleType("chromadb.utils.embedding_functions")
_ef_mod.ONNXMiniLM_L6_V2 = MagicMock
sys.modules.setdefault("chromadb.utils.embedding_functions", _ef_mod)

_chromadb_config = types.ModuleType("chromadb.config")
_chromadb_config.Settings = MagicMock
sys.modules.setdefault("chromadb.config", _chromadb_config)

_onnx_mod = types.ModuleType("onnxruntime")
sys.modules.setdefault("onnxruntime", _onnx_mod)

# Check for real chromadb (not stubs) — used to skip tests that need the actual package
_HAS_REAL_CHROMADB = False
try:
    from importlib.metadata import version as _pkg_version

    _pkg_version("chromadb")
    _HAS_REAL_CHROMADB = True
except Exception:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_chroma():
    """Mock ChromaDB client and both collections."""
    files_collection = MagicMock()
    files_collection.count.return_value = 0
    files_collection.name = "footprinter_files"
    files_collection.query.return_value = {
        "ids": [[]],
        "metadatas": [[]],
        "documents": [[]],
        "distances": [[]],
    }

    chats_collection = MagicMock()
    chats_collection.count.return_value = 0
    chats_collection.name = "footprinter_chats"
    chats_collection.query.return_value = {
        "ids": [[]],
        "metadatas": [[]],
        "documents": [[]],
        "distances": [[]],
    }

    mock_client = MagicMock()

    def get_or_create(name, **kwargs):
        if name == "footprinter_files":
            return files_collection
        return chats_collection

    mock_client.get_or_create_collection.side_effect = get_or_create

    with patch("chromadb.PersistentClient", return_value=mock_client):
        yield files_collection, chats_collection, mock_client


@pytest.fixture
def mock_model():
    """Mock the shared ONNX embedding function."""
    mock_ef = MagicMock()
    mock_ef.return_value = [[0.1] * 384]
    with patch("footprinter.semantic.embeddings.get_embedding_function", return_value=mock_ef):
        yield mock_ef


@pytest.fixture
def store(mock_chroma, mock_model, tmp_path):
    """Create a VectorStore with mocked dependencies."""
    from footprinter.semantic.vector_store import VectorStore

    VectorStore.reset_instance()
    s = VectorStore(chroma_path=str(tmp_path / "chroma"))
    yield s
    VectorStore.reset_instance()


# ---------------------------------------------------------------------------
# TestFileVectorizationEnabled
# ---------------------------------------------------------------------------


class TestFileVectorizationEnabled:
    def test_file_vectorization_logs_debug_on_failure(self, caplog):
        """When get_config raises, _file_vectorization_enabled should log at DEBUG."""
        import logging

        from footprinter.semantic.vector_store import _file_vectorization_enabled

        with (
            patch(
                "footprinter.semantic.vector_store.get_config",
                side_effect=RuntimeError("config missing"),
                create=True,
            ),
            caplog.at_level(logging.DEBUG, logger="footprinter.semantic.vector_store"),
        ):
            # Must re-import get_config inside the function; patch the source_registry
            with patch(
                "footprinter.source_registry.get_config",
                side_effect=RuntimeError("config missing"),
            ):
                result = _file_vectorization_enabled()

        assert result is False
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("config" in r.message.lower() for r in debug_msgs), (
            f"Expected DEBUG log about config failure, got: {[r.message for r in caplog.records]}"
        )

    def test_file_vectorization_returns_false_on_failure(self):
        """Regression guard: exception in get_config returns False."""
        from footprinter.semantic.vector_store import _file_vectorization_enabled

        with patch(
            "footprinter.source_registry.get_config",
            side_effect=RuntimeError("broken"),
        ):
            assert _file_vectorization_enabled() is False


# ---------------------------------------------------------------------------
# TestVectorStoreInit
# ---------------------------------------------------------------------------


class TestVectorStoreInit:
    def test_creates_persistent_client(self, store, mock_chroma):
        _, _, client = mock_chroma
        # PersistentClient was called during __init__
        import chromadb

        chromadb.PersistentClient.assert_called()

    def test_disables_telemetry(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        mock_settings = MagicMock()
        with patch("chromadb.config.Settings", mock_settings):
            VectorStore(chroma_path=str(tmp_path / "chroma"))
        mock_settings.assert_called_with(anonymized_telemetry=False)
        VectorStore.reset_instance()

    def test_creates_files_collection(self, store, mock_chroma):
        _, _, client = mock_chroma
        calls = client.get_or_create_collection.call_args_list
        names = [c[1].get("name", c[0][0] if c[0] else None) for c in calls]
        assert "footprinter_files" in names

    def test_creates_chats_collection(self, store, mock_chroma):
        _, _, client = mock_chroma
        calls = client.get_or_create_collection.call_args_list
        names = [c[1].get("name", c[0][0] if c[0] else None) for c in calls]
        assert "footprinter_chats" in names

    def test_loads_embedding_function(self, store, mock_model):
        assert store.ef is mock_model

    def test_custom_chroma_path(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        custom = str(tmp_path / "custom_chroma")
        s = VectorStore(chroma_path=custom)
        assert str(s.chroma_path) == custom
        VectorStore.reset_instance()

    def test_raises_when_ml_unavailable(self, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        with patch("footprinter.semantic.vector_store._semantic_available", return_value=False):
            with pytest.raises(ImportError, match="Semantic search libraries"):
                VectorStore(chroma_path=str(tmp_path / "chroma"))

    def test_default_path_resolved_at_init_not_import(self, mock_chroma, mock_model, tmp_path):
        """Chroma path must be resolved at init time, not from a module-level constant."""
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        expected = tmp_path / "dynamic_chroma"
        with patch("footprinter.semantic.vector_store.get_chroma_path", return_value=expected):
            s = VectorStore()
        assert s.chroma_path == expected
        VectorStore.reset_instance()

    @pytest.mark.parametrize(
        "enabled_flag",
        [
            "_file_vectorization_enabled",
            "_chat_vectorization_enabled",
        ],
    )
    def test_logs_warning_when_collections_empty(self, mock_chroma, mock_model, tmp_path, caplog, enabled_flag):
        """VectorStore should warn when both collections have 0 documents."""
        import logging
        from unittest.mock import patch

        from footprinter.semantic.vector_store import VectorStore

        files_col, chats_col, _ = mock_chroma
        files_col.count.return_value = 0
        chats_col.count.return_value = 0
        VectorStore.reset_instance()
        with (
            caplog.at_level(logging.WARNING, logger="footprinter.semantic.vector_store"),
            patch(f"footprinter.semantic.vector_store.{enabled_flag}", return_value=True),
        ):
            VectorStore(chroma_path=str(tmp_path / "chroma"))
        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("0 documents" in r.message for r in warning_msgs), (
            f"Expected WARNING about empty collections, got: {[r.message for r in caplog.records]}"
        )
        VectorStore.reset_instance()

    def test_no_warning_when_collections_have_data(self, mock_chroma, mock_model, tmp_path, caplog):
        """No warning should be emitted when at least one collection has data."""
        import logging

        from footprinter.semantic.vector_store import VectorStore

        files_col, chats_col, _ = mock_chroma
        files_col.count.return_value = 100
        chats_col.count.return_value = 0
        VectorStore.reset_instance()
        with caplog.at_level(logging.WARNING, logger="footprinter.semantic.vector_store"):
            VectorStore(chroma_path=str(tmp_path / "chroma"))
        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("0 documents" in r.message for r in warning_msgs), (
            f"Unexpected WARNING about empty collections: {[r.message for r in warning_msgs]}"
        )
        VectorStore.reset_instance()


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_instance_returns_same_object(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        a = VectorStore.get_instance(chroma_path=str(tmp_path / "chroma"))
        b = VectorStore.get_instance()
        assert a is b
        VectorStore.reset_instance()

    def test_first_call_creates_instance(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        assert VectorStore._instance is None
        inst = VectorStore.get_instance(chroma_path=str(tmp_path / "chroma"))
        assert VectorStore._instance is inst
        VectorStore.reset_instance()

    def test_reset_clears_singleton(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        VectorStore.get_instance(chroma_path=str(tmp_path / "chroma"))
        VectorStore.reset_instance()
        assert VectorStore._instance is None

    def test_get_instance_resets_when_chroma_path_deleted(self, mock_chroma, mock_model, tmp_path):
        """Singleton auto-resets when chroma directory is deleted (e.g., by rebuild)."""
        import shutil

        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert VectorStore._instance is first

        # Simulate rebuild_vectors deleting chroma in another process
        shutil.rmtree(chroma_dir)
        assert not chroma_dir.exists()

        second = VectorStore.get_instance()
        assert second is not first
        assert second.chroma_path.exists()
        VectorStore.reset_instance()

    def test_get_instance_keeps_instance_when_chroma_path_exists(self, mock_chroma, mock_model, tmp_path):
        """Singleton is reused when chroma directory still exists (no regression)."""
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))
        second = VectorStore.get_instance()
        assert second is first
        VectorStore.reset_instance()

    def test_stale_reset_forwards_chroma_path(self, mock_chroma, mock_model, tmp_path):
        """When chroma_path is passed during a stale reset, the new instance uses it."""
        import shutil

        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        original_dir = tmp_path / "chroma_old"
        VectorStore.get_instance(chroma_path=str(original_dir))

        shutil.rmtree(original_dir)

        new_dir = tmp_path / "chroma_new"
        refreshed = VectorStore.get_instance(chroma_path=str(new_dir))
        assert refreshed.chroma_path == new_dir
        VectorStore.reset_instance()

    def test_stale_reset_logs_warning(self, mock_chroma, mock_model, tmp_path, caplog):
        """A WARNING is logged when the singleton is reset due to missing chroma path."""
        import logging
        import shutil

        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        VectorStore.get_instance(chroma_path=str(chroma_dir))

        shutil.rmtree(chroma_dir)

        with caplog.at_level(logging.WARNING, logger="footprinter.semantic.vector_store"):
            VectorStore.get_instance()

        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("chroma" in r.message.lower() for r in warning_msgs), (
            f"Expected WARNING about missing chroma path, got: {[r.message for r in caplog.records]}"
        )
        VectorStore.reset_instance()

    def test_thread_safety(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        instances = []

        def get():
            inst = VectorStore.get_instance(chroma_path=str(tmp_path / "chroma"))
            instances.append(inst)

        t1 = threading.Thread(target=get)
        t2 = threading.Thread(target=get)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert len(instances) == 2
        assert instances[0] is instances[1]
        VectorStore.reset_instance()


# ---------------------------------------------------------------------------
# TestIndexFile
# ---------------------------------------------------------------------------


class TestIndexFile:
    def test_indexes_chunks_to_files_collection(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        chunks = [
            {"content": "Chunk one", "chunk_index": 0, "total_chunks": 2},
            {"content": "Chunk two", "chunk_index": 1, "total_chunks": 2},
        ]
        result = store.index_file(file_id=42, file_path="/test/doc.txt", chunks=chunks)
        assert result == 2
        assert files_col.add.call_count == 1

    def test_doc_id_format(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        chunks = [{"content": "Hello", "chunk_index": 0, "total_chunks": 1}]
        store.index_file(file_id=42, file_path="/test.txt", chunks=chunks)
        call_kwargs = files_col.add.call_args[1]
        assert call_kwargs["ids"] == ["file_42_chunk_0"]

    def test_metadata_includes_path(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        chunks = [{"content": "Hi", "chunk_index": 0, "total_chunks": 1}]
        store.index_file(file_id=1, file_path="/my/file.py", chunks=chunks)
        meta = files_col.add.call_args[1]["metadatas"][0]
        assert meta["file_path"] == "/my/file.py"

    def test_empty_chunks_returns_zero(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        result = store.index_file(file_id=1, file_path="/x.txt", chunks=[])
        assert result == 0
        files_col.add.assert_not_called()

    def test_returns_chunk_count(self, store, mock_chroma):
        chunks = [
            {"content": "A", "chunk_index": 0, "total_chunks": 3},
            {"content": "B", "chunk_index": 1, "total_chunks": 3},
            {"content": "C", "chunk_index": 2, "total_chunks": 3},
        ]
        assert store.index_file(file_id=1, file_path="/x", chunks=chunks) == 3


# ---------------------------------------------------------------------------
# TestSearchFiles
# ---------------------------------------------------------------------------


class TestSearchFiles:
    def test_returns_results(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        files_col.query.return_value = {
            "ids": [["file_1_chunk_0"]],
            "metadatas": [
                [
                    {
                        "file_path": "/a.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    }
                ]
            ],
            "documents": [["Content A"]],
            "distances": [[0.1]],
        }
        results = store.search_files("test")
        assert len(results) == 1

    def test_result_shape(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        files_col.query.return_value = {
            "ids": [["file_1_chunk_0"]],
            "metadatas": [
                [
                    {
                        "file_path": "/doc.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    }
                ]
            ],
            "documents": [["Doc content"]],
            "distances": [[0.3]],
        }
        results = store.search_files("query")
        r = results[0]
        assert "file_path" in r
        assert "chunk_index" in r
        assert "total_chunks" in r
        assert "content_snippet" in r
        # Full chunk length carried for excerpt provenance at the service layer.
        assert r["content_length"] == len("Doc content")
        assert "distance" in r

    def test_full_chunk_not_sliced(self, store, mock_chroma):
        """search_files carries the full chunk text — no 500-char pre-slice.

        The service layer owns the one-and-only excerpt cap (via build_excerpt),
        so the store must hand it the whole chunk.
        """
        files_col, _, _ = mock_chroma
        long_chunk = "y" * 1200
        files_col.query.return_value = {
            "ids": [["file_1_chunk_0"]],
            "metadatas": [
                [
                    {
                        "file_id": 1,
                        "file_path": "/big.txt",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    }
                ]
            ],
            "documents": [[long_chunk]],
            "distances": [[0.1]],
        }
        results = store.search_files("query")
        r = results[0]
        assert r["content_snippet"] == long_chunk
        assert r["content_length"] == 1200

    def test_filter_metadata_forwarded(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        store.search_files("test", filter_metadata={"file_type": ".pdf"})
        call_kwargs = files_col.query.call_args[1]
        assert call_kwargs["where"] == {"file_type": ".pdf"}

    def test_empty_results(self, store, mock_chroma):
        results = store.search_files("anything")
        assert results == []


# ---------------------------------------------------------------------------
# TestGetFileChunks — fetch + reassemble a file's chunks by chunk id
# ---------------------------------------------------------------------------


class TestGetFileChunks:
    def test_reassembles_in_chunk_index_order(self, store, mock_chroma):
        """get_file_chunks returns a file's chunks sorted by chunk_index even
        when ChromaDB hands them back out of order."""
        files_col, _, _ = mock_chroma
        files_col.get.return_value = {
            "ids": ["file_1_chunk_2", "file_1_chunk_0", "file_1_chunk_1"],
            "documents": ["third", "first", "second"],
            "metadatas": [
                {"file_id": 1, "chunk_index": 2, "total_chunks": 3},
                {"file_id": 1, "chunk_index": 0, "total_chunks": 3},
                {"file_id": 1, "chunk_index": 1, "total_chunks": 3},
            ],
        }
        chunks = store.get_file_chunks(1)
        assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
        assert [c["content"] for c in chunks] == ["first", "second", "third"]
        assert all(c["total_chunks"] == 3 for c in chunks)

    def test_get_chunks_by_ids_reassembles_in_order(self, store, mock_chroma):
        """get_chunks_by_ids fetches the exact ids and returns them sorted by
        chunk_index, carrying content + index/totals."""
        files_col, _, _ = mock_chroma
        files_col.get.return_value = {
            "ids": ["file_1_chunk_6", "file_1_chunk_4", "file_1_chunk_5"],
            "documents": ["c6", "c4", "c5"],
            "metadatas": [
                {"file_id": 1, "chunk_index": 6, "total_chunks": 9},
                {"file_id": 1, "chunk_index": 4, "total_chunks": 9},
                {"file_id": 1, "chunk_index": 5, "total_chunks": 9},
            ],
        }
        chunks = store.get_chunks_by_ids(
            ["file_1_chunk_4", "file_1_chunk_5", "file_1_chunk_6"]
        )
        assert [c["chunk_index"] for c in chunks] == [4, 5, 6]
        assert [c["content"] for c in chunks] == ["c4", "c5", "c6"]
        files_col.get.assert_called_once()
        assert files_col.get.call_args[1]["ids"] == [
            "file_1_chunk_4",
            "file_1_chunk_5",
            "file_1_chunk_6",
        ]

    def test_get_chunks_by_ids_empty(self, store, mock_chroma):
        """An empty id list short-circuits without hitting ChromaDB."""
        files_col, _, _ = mock_chroma
        assert store.get_chunks_by_ids([]) == []
        files_col.get.assert_not_called()

    def test_get_file_chunks_enumerates_by_file_id(self, store, mock_chroma):
        """get_file_chunks enumerates a file's chunks via where={'file_id'}."""
        files_col, _, _ = mock_chroma
        files_col.get.return_value = {
            "ids": ["file_7_chunk_0"],
            "documents": ["only"],
            "metadatas": [{"file_id": 7, "chunk_index": 0, "total_chunks": 1}],
        }
        store.get_file_chunks(7)
        assert files_col.get.call_args[1]["where"] == {"file_id": 7}

    def test_get_file_chunks_empty_result(self, store, mock_chroma):
        """No chunks for the file → empty list, not an error."""
        files_col, _, _ = mock_chroma
        files_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        assert store.get_file_chunks(999) == []


# ---------------------------------------------------------------------------
# TestDeleteFile
# ---------------------------------------------------------------------------


class TestDeleteFile:
    def test_deletes_by_file_id(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        store.delete_file(42)
        files_col.delete.assert_called_once_with(where={"file_id": 42})


# ---------------------------------------------------------------------------
# TestFileStats
# ---------------------------------------------------------------------------


class TestFileStats:
    def test_returns_count_and_collection_name(self, store, mock_chroma):
        files_col, _, _ = mock_chroma
        files_col.count.return_value = 100
        stats = store.get_file_stats()
        assert stats["total_chunks"] == 100
        assert stats["collection_name"] == "footprinter_files"


# ---------------------------------------------------------------------------
# TestIndexChatMessage
# ---------------------------------------------------------------------------


class TestIndexChatMessage:
    def test_short_content_single_chunk(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        result = store.index_chat_message(
            message_id=42,
            chat_id=1,
            content="Short",
            metadata={"source": "claude", "role": "user"},
        )
        assert result == 1
        conv_col.add.assert_called_once()

    def test_auto_chunks_long_content(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        content = "word " * 3000  # ~15000 chars
        result = store.index_chat_message(
            message_id=1,
            chat_id=1,
            content=content,
            metadata={"source": "claude"},
        )
        assert result >= 2

    def test_doc_id_format(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        store.index_chat_message(
            message_id=42,
            chat_id=1,
            content="Hi",
            metadata={},
        )
        call_kwargs = conv_col.add.call_args[1]
        assert call_kwargs["ids"] == ["msg_42_chunk_0"]

    def test_metadata_passed_through(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        store.index_chat_message(
            message_id=1,
            chat_id=5,
            content="Hello",
            metadata={"source": "claude", "role": "user"},
        )
        meta = conv_col.add.call_args[1]["metadatas"][0]
        assert meta["source"] == "claude"
        assert meta["chat_id"] == 5

    def test_returns_chunk_count(self, store, mock_chroma):
        result = store.index_chat_message(
            message_id=1,
            chat_id=1,
            content="Hello",
            metadata={},
        )
        assert isinstance(result, int)
        assert result == 1

    def test_empty_content_returns_zero(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        result = store.index_chat_message(
            message_id=1,
            chat_id=1,
            content="",
            metadata={},
        )
        assert result == 0
        conv_col.add.assert_not_called()


# ---------------------------------------------------------------------------
# TestUpsertChatMessage
# ---------------------------------------------------------------------------


class TestUpsertChatMessage:
    def test_upsert_chat_delete_failure_logs_warning(self, store, mock_chroma, caplog):
        _, chats_col, _ = mock_chroma
        chats_col.delete.side_effect = Exception("chromadb error")

        import logging

        with caplog.at_level(logging.WARNING, logger="footprinter.semantic.vector_store"):
            result = store.upsert_chat_message(
                message_id=1,
                chat_id=1,
                content="Hello",
                metadata={},
            )

        assert result == 1  # upsert continues after failed delete
        assert any("message_id=1" in r.message for r in caplog.records)
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_upsert_chat_delete_failure_still_indexes(self, store, mock_chroma):
        _, chats_col, _ = mock_chroma
        chats_col.delete.side_effect = Exception("chromadb error")

        store.upsert_chat_message(
            message_id=1,
            chat_id=1,
            content="Hello",
            metadata={},
        )

        chats_col.add.assert_called_once()


# ---------------------------------------------------------------------------
# TestIndexChatInfo
# ---------------------------------------------------------------------------


class TestIndexChatInfo:
    def test_builds_searchable_text(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        store.index_chat_info(
            chat_id=1,
            title="My Chat",
            source="claude",
            created_at="2024-01-01",
            message_count=10,
        )
        call_kwargs = conv_col.upsert.call_args[1]
        doc = call_kwargs["documents"][0]
        assert "Chat: My Chat" in doc

    def test_uses_upsert(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        store.index_chat_info(
            chat_id=1,
            title="Test",
            source="claude",
            created_at="",
            message_count=1,
        )
        conv_col.upsert.assert_called_once()

    def test_doc_id_format(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        store.index_chat_info(
            chat_id=99,
            title="Test",
            source="claude",
            created_at="",
            message_count=1,
        )
        call_kwargs = conv_col.upsert.call_args[1]
        assert call_kwargs["ids"] == ["chat_info_99"]


# ---------------------------------------------------------------------------
# TestSearchChats
# ---------------------------------------------------------------------------


class TestSearchChats:
    def test_short_query_rejected(self, store):
        assert store.search_chats("ab") == []

    def test_hybrid_search_calls_both(self, store, mock_chroma, mock_model):
        _, conv_col, _ = mock_chroma
        conv_col.query.return_value = {
            "ids": [["msg_1_chunk_0"]],
            "metadatas": [
                [
                    {
                        "chat_id": 1,
                        "chat_title": "Test",
                        "message_id": 1,
                        "role": "user",
                        "source": "claude",
                        "created_at": "",
                        "chunk_type": "message",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    }
                ]
            ],
            "documents": [["Test content about search"]],
            "distances": [[0.3]],
        }
        with (
            patch("footprinter.semantic.hybrid_search.keyword_search", return_value=[]) as kw_mock,
            patch("footprinter.ingest.database.get_db_path", return_value="/tmp/test.db"),
        ):
            store.search_chats("test query", min_score=0.0)
        conv_col.query.assert_called_once()
        kw_mock.assert_called_once()

    def test_source_filter_forwarded(self, store, mock_chroma, mock_model):
        _, conv_col, _ = mock_chroma
        with (
            patch("footprinter.semantic.hybrid_search.keyword_search", return_value=[]),
            patch("footprinter.ingest.database.get_db_path", return_value="/tmp/t.db"),
        ):
            store.search_chats("test query", source="claude", min_score=0.0)
        call_kwargs = conv_col.query.call_args[1]
        assert call_kwargs["where"] == {"source": "claude"}

    def test_result_shape(self, store, mock_chroma, mock_model):
        _, conv_col, _ = mock_chroma
        conv_col.query.return_value = {
            "ids": [["msg_1_chunk_0"]],
            "metadatas": [
                [
                    {
                        "chat_id": 1,
                        "chat_title": "Test",
                        "message_id": 1,
                        "role": "user",
                        "source": "claude",
                        "created_at": "",
                        "chunk_type": "message",
                        "chunk_index": 0,
                        "total_chunks": 1,
                    }
                ]
            ],
            "documents": [["Search test content"]],
            "distances": [[0.3]],
        }
        with (
            patch("footprinter.semantic.hybrid_search.keyword_search", return_value=[]),
            patch("footprinter.ingest.database.get_db_path", return_value="/tmp/t.db"),
        ):
            results = store.search_chats("test query", min_score=0.0)
        assert len(results) >= 1
        r = results[0]
        required = {"chat_id", "chat_title", "relevance_score", "snippet"}
        assert required.issubset(r.keys())


# ---------------------------------------------------------------------------
# TestDeleteChat
# ---------------------------------------------------------------------------


class TestDeleteChat:
    def test_deletes_messages_and_info(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        store.delete_chat(42)
        conv_col.delete.assert_called_once_with(where={"chat_id": 42})


# ---------------------------------------------------------------------------
# TestChatStats
# ---------------------------------------------------------------------------


class TestChatStats:
    def test_returns_count_and_name(self, store, mock_chroma):
        _, conv_col, _ = mock_chroma
        conv_col.count.return_value = 50
        stats = store.get_chat_stats()
        assert stats["total_documents"] == 50
        assert stats["collection_name"] == "footprinter_chats"


# ---------------------------------------------------------------------------
# Embeddings tests (merged from test_embeddings.py)
# ---------------------------------------------------------------------------


class TestEmbeddingDim:
    def test_embedding_dim_is_384(self):
        from footprinter.semantic.embeddings import EMBEDDING_DIM

        assert EMBEDDING_DIM == 384


class TestRebuildStamp:
    def test_rebuild_stamp_constant_exists(self):
        from footprinter.semantic.vector_store import VectorStore

        assert VectorStore._REBUILD_STAMP == ".rebuild_stamp"

    def test_init_reads_stamp_when_present(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("abc123")
        s = VectorStore(chroma_path=str(chroma_dir))
        assert s._rebuild_id == "abc123"
        VectorStore.reset_instance()

    def test_init_sets_none_when_stamp_missing(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        s = VectorStore(chroma_path=str(tmp_path / "chroma"))
        assert s._rebuild_id is None
        VectorStore.reset_instance()

    def test_init_strips_whitespace_from_stamp(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("  abc123\n")
        s = VectorStore(chroma_path=str(chroma_dir))
        assert s._rebuild_id == "abc123"
        VectorStore.reset_instance()


class TestRebuildStampStaleness:
    def test_get_instance_resets_when_stamp_changes(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("v1")
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert first._rebuild_id == "v1"

        (chroma_dir / ".rebuild_stamp").write_text("v2")
        second = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert second is not first
        assert second._rebuild_id == "v2"
        VectorStore.reset_instance()

    def test_get_instance_keeps_instance_when_stamp_unchanged(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("same")
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))
        second = VectorStore.get_instance()
        assert second is first
        VectorStore.reset_instance()

    def test_get_instance_resets_when_stamp_appears(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert first._rebuild_id is None

        (chroma_dir / ".rebuild_stamp").write_text("new_stamp")
        second = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert second is not first
        assert second._rebuild_id == "new_stamp"
        VectorStore.reset_instance()

    def test_get_instance_resets_when_stamp_disappears(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("will_be_removed")
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert first._rebuild_id == "will_be_removed"

        (chroma_dir / ".rebuild_stamp").unlink()
        second = VectorStore.get_instance(chroma_path=str(chroma_dir))
        assert second is not first
        assert second._rebuild_id is None
        VectorStore.reset_instance()

    def test_stamp_staleness_logs_warning(self, mock_chroma, mock_model, tmp_path, caplog):
        import logging

        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("v1")
        VectorStore.get_instance(chroma_path=str(chroma_dir))

        (chroma_dir / ".rebuild_stamp").write_text("v2")
        with caplog.at_level(logging.WARNING, logger="footprinter.semantic.vector_store"):
            VectorStore.get_instance()

        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stamp" in r.message.lower() for r in warning_msgs), (
            f"Expected WARNING about stamp change, got: {[r.message for r in caplog.records]}"
        )
        VectorStore.reset_instance()

    def test_stamp_check_before_directory_check(self, mock_chroma, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("old")
        first = VectorStore.get_instance(chroma_path=str(chroma_dir))

        assert chroma_dir.exists()
        (chroma_dir / ".rebuild_stamp").write_text("new")
        second = VectorStore.get_instance()
        assert second is not first
        VectorStore.reset_instance()

    def test_stamp_mismatch_creates_fresh_client(self, mock_model, tmp_path):
        from footprinter.semantic.vector_store import VectorStore

        def _make_client(*args, **kwargs):
            c = MagicMock()
            col = MagicMock()
            col.count.return_value = 0
            c.get_or_create_collection.return_value = col
            return c

        VectorStore.reset_instance()
        chroma_dir = tmp_path / "chroma"
        chroma_dir.mkdir()
        (chroma_dir / ".rebuild_stamp").write_text("v1")

        with patch("chromadb.PersistentClient", side_effect=_make_client):
            first = VectorStore.get_instance(chroma_path=str(chroma_dir))
            first_client = first.client

            (chroma_dir / ".rebuild_stamp").write_text("v2")
            second = VectorStore.get_instance(chroma_path=str(chroma_dir))
            assert second.client is not first_client
        VectorStore.reset_instance()


@pytest.mark.skipif(not _HAS_REAL_CHROMADB, reason="requires [semantic] extra")
class TestGetEmbeddingFunction:
    def test_returns_onnx_instance(self):
        from footprinter.semantic.embeddings import get_embedding_function

        ef = get_embedding_function()
        # ONNXMiniLM_L6_V2 is stubbed as MagicMock, so calling it returns a MagicMock instance
        assert ef is not None

    def test_each_call_returns_new_instance(self):
        from footprinter.semantic.embeddings import get_embedding_function

        ef1 = get_embedding_function()
        ef2 = get_embedding_function()
        assert ef1 is not ef2
