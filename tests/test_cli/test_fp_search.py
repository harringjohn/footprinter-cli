"""Tests for ``fp search`` routed subcommand.

Validates:
  1. fp search --help exits 0
  2. fp search (no query) exits non-zero
  3. fp search --mode semantic <query> with mocked VectorStore runs and calls both sources
  4. --limit / -n flag passes correct limit
  5. --type flag passes filter metadata
  6. Missing ML dependencies fall back to keyword search
"""

import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

from conftest import run_fp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_store(file_results=None, chat_results=None):
    """Create a mock VectorStore with configurable search results."""
    mock_store = MagicMock()
    mock_store.search_files.return_value = file_results or []
    mock_store.search_chats.return_value = chat_results or []
    return mock_store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSearchHelp:
    """fp search --help behaviour."""

    def test_search_help_exits_zero(self):
        stdout, stderr, code = run_fp("search", "--help")
        assert code == 0
        output = stdout + stderr
        assert "query" in output.lower()


class TestSearchRequiresQuery:
    """fp search (no args) should show help with examples."""

    def test_search_bare_shows_help(self):
        stdout, stderr, code = run_fp("search")
        assert code == 0
        output = stdout + stderr
        assert "search" in output.lower()
        assert "the following arguments are required" not in output


class TestSearchExecution:
    """fp search --mode semantic <query> with mocked VectorStore."""

    @patch("footprinter.cli.search._HAS_ML", True)
    @patch("footprinter.cli.search.VectorStore")
    def test_search_runs_with_mock_store(self, MockVS):
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/tmp/doc.md",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "content_snippet": "Test content",
                    "distance": 0.3,
                },
            ],
            chat_results=[
                {
                    "chat_title": "Test chat",
                    "source": "personal",
                    "snippet": "Chat content",
                    "relevance_score": 0.8,
                },
            ],
        )
        MockVS.get_instance.return_value = mock_store

        stdout, stderr, code = run_fp("search", "--mode", "semantic", "test", "query")
        assert code == 0
        mock_store.search_files.assert_called_once()
        mock_store.search_chats.assert_called_once()

    @patch("footprinter.cli.search._HAS_ML", True)
    @patch("footprinter.cli.search.VectorStore")
    def test_search_limit_flag(self, MockVS):
        mock_store = _make_mock_store()
        MockVS.get_instance.return_value = mock_store

        stdout, stderr, code = run_fp("search", "--mode", "semantic", "-n", "5", "test")
        assert code == 0
        # Verify n_results=5 was passed
        call_kwargs = mock_store.search_files.call_args
        assert (
            call_kwargs[1].get("n_results") == 5
            or call_kwargs[0][1] == 5
            or (len(call_kwargs[1]) > 0 and call_kwargs[1].get("n_results") == 5)
        )

    @patch("footprinter.cli.search._HAS_ML", True)
    @patch("footprinter.cli.search.VectorStore")
    def test_search_type_filter(self, MockVS):
        mock_store = _make_mock_store()
        MockVS.get_instance.return_value = mock_store

        stdout, stderr, code = run_fp("search", "--mode", "semantic", "--type", ".py", "test")
        assert code == 0
        call_kwargs = mock_store.search_files.call_args
        # filter_metadata should include file_type
        assert call_kwargs[1].get("filter_metadata") == {"file_type": ".py"}

    @patch("footprinter.cli.search._HAS_ML", True)
    @patch("footprinter.cli.search.VectorStore")
    def test_type_filter_excludes_chats(self, MockVS):
        """--type flag should skip chat search entirely."""
        mock_store = _make_mock_store()
        MockVS.get_instance.return_value = mock_store

        stdout, stderr, code = run_fp("search", "--mode", "semantic", "--type", ".md", "test")
        assert code == 0
        mock_store.search_files.assert_called_once()
        assert mock_store.search_files.call_args[1].get("filter_metadata") == {"file_type": ".md"}
        mock_store.search_chats.assert_not_called()


class TestSearchNoML:
    """Missing ML dependencies fall back to keyword search gracefully."""

    def test_search_no_ml_falls_back_to_keyword(self):
        # Create a minimal temp DB with FTS5 tables so keyword fallback works
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE files (id INTEGER PRIMARY KEY, source TEXT,"
                " name TEXT, path TEXT, content_type TEXT,"
                " size_bytes INTEGER, modified_at TEXT,"
                " status TEXT DEFAULT 'active')"
            )
            conn.execute("CREATE VIRTUAL TABLE files_fts USING fts5(name, path, content=files, content_rowid=id)")
            conn.execute(
                "CREATE TABLE chats (id INTEGER PRIMARY KEY,"
                " external_id TEXT, title TEXT, summary TEXT,"
                " account TEXT, created_at TEXT, modified_at TEXT,"
                " message_count INTEGER)"
            )
            conn.execute("CREATE VIRTUAL TABLE chats_fts USING fts5(title, summary, content=chats, content_rowid=id)")
            conn.commit()
            conn.close()

            with (
                patch("footprinter.cli.search._HAS_ML", False),
                patch("footprinter.paths.get_db_path", return_value=db_path),
            ):
                stdout, stderr, code = run_fp("search", "test")

            assert code == 0
            output = stdout + stderr
            assert "keyword" in output.lower()
        finally:
            os.unlink(db_path)
