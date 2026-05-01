"""Tests for footprinter/semantic/chunking.py"""

import sys
import types
from unittest.mock import MagicMock

# Stub chromadb so the semantic package __init__ can import
_chromadb_mod = types.ModuleType("chromadb")
_chromadb_mod.PersistentClient = MagicMock
sys.modules.setdefault("chromadb", _chromadb_mod)

_chromadb_utils = types.ModuleType("chromadb.utils")
sys.modules.setdefault("chromadb.utils", _chromadb_utils)

_ef_mod = types.ModuleType("chromadb.utils.embedding_functions")
_ef_mod.ONNXMiniLM_L6_V2 = MagicMock
sys.modules.setdefault("chromadb.utils.embedding_functions", _ef_mod)

_onnx_mod = types.ModuleType("onnxruntime")
sys.modules.setdefault("onnxruntime", _onnx_mod)

from footprinter.semantic.chunking import chunk_content


class TestChunkDefaults:
    """Test that chunk defaults are tuned for MiniLM-L6-v2."""

    def test_default_chunk_size_is_1000(self):
        from footprinter.semantic.chunking import DEFAULT_CHUNK_SIZE

        assert DEFAULT_CHUNK_SIZE == 1000

    def test_default_overlap_is_150(self):
        from footprinter.semantic.chunking import DEFAULT_CHUNK_OVERLAP

        assert DEFAULT_CHUNK_OVERLAP == 150

    def test_3000_chars_produces_multiple_chunks(self):
        """3000 chars at 1000-char chunk size should produce 3+ chunks."""
        content = "word " * 600  # 3000 chars
        result = chunk_content(content)
        assert len(result) >= 3


class TestChunkContent:
    def test_short_content_single_chunk(self):
        result = chunk_content("Short message under 8000 chars")
        assert len(result) == 1
        assert result == [("Short message under 8000 chars", 0, 1)]

    def test_long_content_multiple_chunks(self):
        content = "word " * 3000  # ~15000 chars
        result = chunk_content(content)
        assert len(result) >= 2

    def test_overlap_between_chunks(self):
        content = "word " * 3000  # ~15000 chars
        result = chunk_content(content)
        assert len(result) >= 2
        # With overlap, chunks should cover overlapping regions
        # The end of chunk 0 text should overlap with the start of chunk 1 text
        chunk0_text = result[0][0]
        chunk1_text = result[1][0]
        # Last 800 chars of chunk0 should overlap with start of chunk1
        tail = chunk0_text[-400:]
        assert tail in chunk1_text or chunk1_text[:400] in chunk0_text

    def test_word_boundary_splitting(self):
        words = ["hello"] * 2000  # 10000+ chars with spaces
        content = " ".join(words)
        result = chunk_content(content)
        assert len(result) >= 2
        # Chunks should not end mid-word
        for text, _idx, _total in result:
            stripped = text.strip()
            if stripped:
                assert not stripped[-1].isalpha() or stripped.endswith("hello")

    def test_empty_content(self):
        result = chunk_content("")
        assert len(result) == 1
        assert result[0][0] == ""

    def test_custom_chunk_size(self):
        content = "a " * 100  # 200 chars
        result = chunk_content(content, chunk_size=100, chunk_overlap=10)
        assert len(result) >= 2
        for text, _idx, _total in result:
            assert len(text) <= 110  # Allow margin for word boundary

    def test_total_chunks_backfilled(self):
        content = "word " * 3000
        result = chunk_content(content)
        total = len(result)
        assert total >= 2
        for text, idx, tc in result:
            assert tc == total
            assert idx < total
