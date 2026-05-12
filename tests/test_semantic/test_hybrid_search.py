"""Tests for footprinter/semantic/hybrid_search.py"""

import sqlite3
import sys
import types
from unittest.mock import MagicMock

import pytest

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

from footprinter.semantic.hybrid_search import (  # noqa: E402
    chat_snippet,
    extract_snippet,
    fts5_fallback_search,
    keyword_search,
    reciprocal_rank_fusion,
)

# ---------------------------------------------------------------------------
# TestExtractSnippet
# ---------------------------------------------------------------------------


class TestExtractSnippet:
    def test_short_content_returns_all(self):
        content = "Short content here"
        assert extract_snippet(content, "anything") == content

    def test_finds_query_term_in_context(self):
        content = "A" * 300 + "important_term" + "B" * 300
        snippet = extract_snippet(content, "important_term")
        assert "important_term" in snippet

    def test_no_match_returns_beginning(self):
        content = "X" * 1000
        snippet = extract_snippet(content, "nonexistent_zzzz")
        assert snippet.startswith("X")

    def test_adds_ellipsis_when_truncated(self):
        content = "A" * 300 + " target_word " + "B" * 300
        snippet = extract_snippet(content, "target_word")
        assert "..." in snippet


# ---------------------------------------------------------------------------
# TestChatSnippet
# ---------------------------------------------------------------------------


class TestChatSnippet:
    def test_summary_truncated_to_300_chars(self):
        row = {"summary": "A" * 500, "chat_title": "Test Chat"}
        result = chat_snippet(row)
        assert result == "A" * 300 + "..."

    def test_summary_short_returned_without_ellipsis(self):
        row = {"summary": "Short summary", "chat_title": "Test Chat"}
        result = chat_snippet(row)
        assert result == "Short summary"

    def test_summary_exactly_300_chars(self):
        row = {"summary": "A" * 300, "chat_title": "Test Chat"}
        result = chat_snippet(row)
        assert result == "A" * 300

    def test_no_summary_returns_title_match(self):
        row = {"summary": "", "chat_title": "My Chat"}
        result = chat_snippet(row)
        assert result == "Title match: My Chat"

    def test_none_summary_returns_title_match(self):
        row = {"summary": None, "chat_title": "My Chat"}
        result = chat_snippet(row)
        assert result == "Title match: My Chat"


# ---------------------------------------------------------------------------
# TestReciprocalRankFusion
# ---------------------------------------------------------------------------


def _make_semantic_result(chat_id, title="Test", source="claude"):
    return {
        "chat_id": chat_id,
        "chat_title": title,
        "source": source,
        "relevance_score": 0.9,
        "snippet": "snippet",
        "message_id": 1,
        "role": "user",
        "created_at": "2024-01-01",
        "chunk_type": "message",
        "chunk_index": 0,
        "total_chunks": 1,
    }


def _make_keyword_result(chat_id, title="Test", source="claude"):
    return {
        "chat_id": chat_id,
        "chat_title": title,
        "source": source,
        "created_at": "2024-01-01",
        "summary": "keyword summary",
        "message_count": 5,
    }


class TestReciprocalRankFusion:
    def test_merges_two_result_sets(self):
        semantic = [_make_semantic_result(1, "A")]
        keyword = [_make_keyword_result(2, "B")]
        combined = reciprocal_rank_fusion(semantic, keyword)
        chat_ids = [r["chat_id"] for r in combined]
        assert 1 in chat_ids
        assert 2 in chat_ids

    def test_both_lists_boost_score(self):
        shared_sem = _make_semantic_result(1, "Shared")
        shared_kw = _make_keyword_result(1, "Shared")
        only_sem = _make_semantic_result(2, "SemOnly")
        combined = reciprocal_rank_fusion([shared_sem, only_sem], [shared_kw])
        shared = next(r for r in combined if r["chat_id"] == 1)
        sem_only = next(r for r in combined if r["chat_id"] == 2)
        assert shared["rrf_score"] > sem_only["rrf_score"]

    def test_deduplicates_by_chat_id(self):
        sem = _make_semantic_result(1)
        kw = _make_keyword_result(1)
        combined = reciprocal_rank_fusion([sem], [kw])
        assert len(combined) == 1

    def test_empty_semantic_results(self):
        kw = _make_keyword_result(1)
        combined = reciprocal_rank_fusion([], [kw])
        assert len(combined) == 1
        assert combined[0]["chat_id"] == 1

    def test_empty_keyword_results(self):
        sem = _make_semantic_result(1)
        combined = reciprocal_rank_fusion([sem], [])
        assert len(combined) == 1

    def test_both_empty(self):
        assert reciprocal_rank_fusion([], []) == []

    def test_keyword_only_uses_account_key(self):
        """FIX: keyword results may have 'account' instead of 'source'."""
        kw = {
            "chat_id": 1,
            "chat_title": "Test",
            "account": "claude",
            "created_at": "2024-01-01",
            "summary": "",
            "message_count": 5,
        }
        # Should NOT raise KeyError
        combined = reciprocal_rank_fusion([], [kw])
        assert len(combined) == 1
        assert combined[0]["source"] == "claude"

    def test_keyword_only_prefers_source_over_account(self):
        """When both 'source' and 'account' are present, 'source' wins."""
        kw = {
            "chat_id": 1,
            "chat_title": "Test",
            "source": "chatgpt",
            "account": "claude",
            "created_at": "2024-01-01",
            "summary": "",
            "message_count": 5,
        }
        combined = reciprocal_rank_fusion([], [kw])
        assert combined[0]["source"] == "chatgpt"


# ---------------------------------------------------------------------------
# TestKeywordSearch — uses real FTS5 temp DB
# ---------------------------------------------------------------------------


@pytest.fixture
def fts5_db(tmp_path):
    """Create a temp DB with FTS5 table and test data."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            summary TEXT,
            account TEXT,
            created_at TEXT,
            message_count INTEGER DEFAULT 0,
            mcp_view TEXT DEFAULT 'inherit',
            status TEXT DEFAULT 'listed'
        )
    """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE chats_fts USING fts5(
            title, summary, content=chats, content_rowid=id
        )
    """
    )

    # Insert test data — visible chats
    conn.execute(
        """INSERT INTO chats (title, summary, account, created_at, message_count, mcp_view)
        VALUES ('Python debugging', 'Discussion about Python errors', 'claude', '2024-01-01', 10, 'visible')"""
    )
    conn.execute(
        """INSERT INTO chats (title, summary, account, created_at, message_count, mcp_view)
        VALUES ('JavaScript intro', 'Learning JS basics', 'chatgpt', '2024-01-02', 5, 'visible')"""
    )
    # Hidden chat — returned by keyword_search (downstream handles filtering)
    conn.execute(
        """INSERT INTO chats (title, summary, account, created_at, message_count, mcp_view)
        VALUES ('Python patterns', 'Advanced Python design patterns', 'claude', '2024-01-03', 8, 'hidden')"""
    )
    # Unresolved chat (inherit) — returned by keyword_search (downstream handles filtering)
    conn.execute(
        """INSERT INTO chats (title, summary, account, created_at, message_count, mcp_view)
        VALUES ('Python tips', 'Quick Python tips and tricks', 'claude', '2024-01-04', 3, 'inherit')"""
    )
    # Opaque chat — should be returned by keyword_search (downstream handles filtering)
    conn.execute(
        """INSERT INTO chats (title, summary, account, created_at, message_count, mcp_view)
        VALUES ('Python opaque', 'Opaque Python chat', 'claude', '2024-01-05', 4, 'opaque')"""
    )
    # Removed chat — should be excluded from keyword_search results
    conn.execute(
        """INSERT INTO chats (title, summary, account, created_at, message_count, mcp_view, status)
        VALUES ('Python removed', 'Removed Python chat', 'claude', '2024-01-06', 2, 'visible', 'removed')"""
    )
    # Populate FTS
    conn.execute(
        """INSERT INTO chats_fts(rowid, title, summary)
        SELECT id, title, summary FROM chats"""
    )
    conn.commit()
    conn.close()
    return db_path


class TestKeywordSearch:
    def test_returns_results_from_fts5(self, fts5_db):
        results = keyword_search("Python", db_path=fts5_db)
        assert len(results) >= 1
        titles = [r["chat_title"] for r in results]
        assert "Python debugging" in titles

    def test_account_filter(self, fts5_db):
        results = keyword_search("Python", db_path=fts5_db, account="chatgpt")
        assert len(results) == 0  # Python conv is 'claude', not 'chatgpt'

    def test_empty_query_returns_empty(self, fts5_db):
        results = keyword_search("", db_path=fts5_db)
        assert results == []

    def test_fts5_error_returns_empty(self, tmp_path):
        """Graceful on missing FTS5 table."""
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.close()
        results = keyword_search("test", db_path=db_path)
        assert results == []

    def test_result_shape(self, fts5_db):
        results = keyword_search("Python", db_path=fts5_db)
        assert len(results) >= 1
        r = results[0]
        required_keys = {
            "chat_id",
            "chat_title",
            "source",
            "created_at",
            "message_count",
            "fts_score",
            "match_type",
        }
        assert required_keys.issubset(r.keys())
        # Verify normalized key is 'source' (not 'account')
        assert "source" in r
        assert r["source"] == "claude"

    def test_includes_hidden_chats(self, fts5_db):
        """Hidden chats returned by keyword_search — filtering is downstream's job."""
        results = keyword_search("Python", db_path=fts5_db)
        titles = [r["chat_title"] for r in results]
        assert "Python patterns" in titles

    def test_includes_unresolved_chats(self, fts5_db):
        """Inherit/unresolved chats returned by keyword_search — filtering is downstream's job."""
        results = keyword_search("Python", db_path=fts5_db)
        titles = [r["chat_title"] for r in results]
        assert "Python tips" in titles

    def test_includes_opaque_chats(self, fts5_db):
        """Opaque chats returned by keyword_search — downstream minimizes fields."""
        results = keyword_search("Python", db_path=fts5_db)
        titles = [r["chat_title"] for r in results]
        assert "Python opaque" in titles

    def test_excludes_removed_chats(self, fts5_db):
        """Removed chats must not appear in keyword_search results."""
        results = keyword_search("Python", db_path=fts5_db)
        titles = [r["chat_title"] for r in results]
        assert "Python removed" not in titles


# ---------------------------------------------------------------------------
# TestFts5FallbackSearch
# ---------------------------------------------------------------------------


class TestFts5FallbackSearch:
    def test_returns_tuple_with_fallback_flag(self, fts5_db):
        results, is_fallback = fts5_fallback_search("Python", db_path=fts5_db)
        assert is_fallback is True

    def test_result_shape_matches_hybrid(self, fts5_db):
        results, _ = fts5_fallback_search("Python", db_path=fts5_db)
        assert len(results) >= 1
        r = results[0]
        required_keys = {
            "chat_id",
            "chat_title",
            "message_id",
            "role",
            "source",
            "created_at",
            "snippet",
            "relevance_score",
            "chunk_type",
            "chunk_index",
            "total_chunks",
        }
        assert required_keys.issubset(r.keys())

    def test_source_filter(self, fts5_db):
        results, _ = fts5_fallback_search("Python", source="chatgpt", db_path=fts5_db)
        assert len(results) == 0
