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

from footprinter.semantic.chunking import chunk_content  # noqa: E402


class TestChunkDefaults:
    """Test that chunk defaults are tuned for MiniLM-L6-v2."""

    def test_default_chunk_size_is_1000(self):
        from footprinter.semantic.chunking import DEFAULT_CHUNK_SIZE

        assert DEFAULT_CHUNK_SIZE == 1000

    def test_default_overlap_is_fractional(self):
        from footprinter.semantic.chunking import DEFAULT_CHUNK_OVERLAP

        assert DEFAULT_CHUNK_OVERLAP == 0.15

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

    def test_fractional_overlap_computes_correct_chars(self):
        """chunk_overlap=0.1 with chunk_size=1000 -> 100-char overlap, step ~900."""
        content = "a " * 1500  # 3000 chars
        result = chunk_content(content, chunk_size=1000, chunk_overlap=0.1)
        assert len(result) >= 3

    def test_legacy_absolute_overlap_still_works_with_warning(self):
        """Passing chunk_overlap=150 (>=1.0) emits DeprecationWarning but works."""
        import warnings

        content = "word " * 600  # 3000 chars
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = chunk_content(content, chunk_size=1000, chunk_overlap=150)
            assert len(result) >= 3
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_msgs) == 1
            assert "fractional" in str(deprecation_msgs[0].message).lower()

    def test_zero_overlap_no_warning(self):
        """chunk_overlap=0.0 means no overlap, no warning."""
        import warnings

        content = "word " * 600
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = chunk_content(content, chunk_size=1000, chunk_overlap=0.0)
            assert len(result) >= 3
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_msgs) == 0

    def test_custom_chunk_size(self):
        content = "a " * 100  # 200 chars
        result = chunk_content(content, chunk_size=100, chunk_overlap=0.1)
        assert len(result) >= 2
        for text, _idx, _total in result:
            assert len(text) <= 110  # Allow margin for word boundary

    def test_negative_fractional_overlap_raises(self):
        """Negative fractional overlap would skip content; reject it."""
        import pytest

        with pytest.raises(ValueError, match="must be non-negative"):
            chunk_content("word " * 600, chunk_overlap=-0.1)

    def test_negative_absolute_overlap_raises(self):
        """Negative value outside fractional range is also rejected."""
        import pytest

        with pytest.raises(ValueError, match="must be non-negative"):
            chunk_content("word " * 600, chunk_overlap=-100)

    def test_overlap_ge_chunk_size_raises(self):
        """Overlap >= chunk_size would cause an infinite loop; reject it."""
        import pytest

        with pytest.raises(ValueError, match="must be less than chunk_size"):
            chunk_content("word " * 600, chunk_size=1000, chunk_overlap=1000)

    def test_overlap_1_0_uses_legacy_path(self):
        """chunk_overlap=1.0 hits the legacy path (1 char), not 100% fractional."""
        import warnings

        content = "word " * 600
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = chunk_content(content, chunk_size=1000, chunk_overlap=1.0)
            assert len(result) >= 3
            deprecation_msgs = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_msgs) == 1

    def test_total_chunks_backfilled(self):
        content = "word " * 3000
        result = chunk_content(content)
        total = len(result)
        assert total >= 2
        for text, idx, tc in result:
            assert tc == total
            assert idx < total
