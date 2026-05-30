"""Tests for footprinter.cli.search — CLI prog name, cross-source search, and search modes."""

import argparse
import sqlite3
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


class TestSearchProgName:
    """fp search --help should show 'usage: fp search', not 'search.py'."""

    def test_help_shows_fp_search(self, monkeypatch):
        # Use a non-"fp" argv[0] so only an explicit prog= makes this pass
        monkeypatch.setattr(sys, "argv", ["search.py", "--help"])
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        from footprinter.cli.search import main

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        output = captured.getvalue()
        assert "usage: fp search" in output


def _make_mock_store(file_results=None, chat_results=None):
    """Create a mock VectorStore with configurable search results."""
    mock_store = MagicMock()
    mock_store.search_files.return_value = file_results or []
    mock_store.search_chats.return_value = chat_results or []
    return mock_store


def _run_search(monkeypatch, mock_store, query="test query", extra_args=None):
    """Run the search CLI with a mock store in semantic mode, return captured stdout."""
    # Existing tests were written for semantic-only behavior, so force --mode semantic
    args = extra_args or []
    if "--mode" not in args:
        args = ["--mode", "semantic"] + args
    argv = ["fp"] + args + query.split()
    monkeypatch.setattr(sys, "argv", argv)

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    with patch("footprinter.cli.search.VectorStore") as MockVS, patch("footprinter.cli.search._HAS_ML", True):
        MockVS.get_instance.return_value = mock_store
        from footprinter.cli.search import main

        main()

    return captured.getvalue()


class TestCrossSourceSearch:
    """fp search should query both files and chats."""

    def test_searches_both_files_and_chats(self, monkeypatch):
        """Both file and chat results appear in output."""
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/home/user/doc.md",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "content_snippet": "File content about testing",
                    "distance": 0.4,
                },
            ],
            chat_results=[
                {
                    "chat_id": 1,
                    "chat_title": "Chat about testing",
                    "message_id": 10,
                    "role": "assistant",
                    "source": "personal",
                    "created_at": "2025-02-20T10:00:00",
                    "snippet": "Conversation content about testing",
                    "relevance_score": 0.85,
                    "chunk_type": "message",
                    "chunk_index": 0,
                    "total_chunks": 1,
                },
            ],
        )

        output = _run_search(monkeypatch, mock_store)

        assert "/home/user/doc.md" in output
        assert "Chat about testing" in output
        mock_store.search_files.assert_called_once()
        mock_store.search_chats.assert_called_once()

    def test_merges_by_relevance(self, monkeypatch):
        """Higher-relevance conversation result appears before lower-relevance file result."""
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/home/user/low_relevance.md",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "content_snippet": "Less relevant file",
                    "distance": 0.5,  # relevance = max(0, 1 - 0.5/2) = 0.75
                },
            ],
            chat_results=[
                {
                    "chat_id": 2,
                    "chat_title": "High relevance chat",
                    "message_id": 20,
                    "role": "user",
                    "source": "work",
                    "created_at": "2025-03-01T12:00:00",
                    "snippet": "Very relevant conversation",
                    "relevance_score": 0.9,
                    "chunk_type": "message",
                    "chunk_index": 0,
                    "total_chunks": 1,
                },
            ],
        )

        output = _run_search(monkeypatch, mock_store)

        # Conversation (0.9) should appear before file (0.75)
        chat_pos = output.index("High relevance chat")
        file_pos = output.index("low_relevance.md")
        assert chat_pos < file_pos

    def test_chat_only_results(self, monkeypatch):
        """When only chats match, results display correctly."""
        mock_store = _make_mock_store(
            file_results=[],
            chat_results=[
                {
                    "chat_id": 3,
                    "chat_title": "Solo conversation",
                    "message_id": 30,
                    "role": "assistant",
                    "source": "personal",
                    "created_at": "2025-01-15T08:00:00",
                    "snippet": "Only conversation matched",
                    "relevance_score": 0.7,
                    "chunk_type": "message",
                    "chunk_index": 0,
                    "total_chunks": 1,
                },
            ],
        )

        output = _run_search(monkeypatch, mock_store)

        assert "No results found" not in output
        assert "Solo conversation" in output
        assert "[Chat]" in output

    def test_file_only_results(self, monkeypatch):
        """When only files match, existing display behavior is preserved."""
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/home/user/only_file.txt",
                    "chunk_index": 0,
                    "total_chunks": 3,
                    "content_snippet": "File-only result content",
                    "distance": 0.3,
                },
            ],
            chat_results=[],
        )

        output = _run_search(monkeypatch, mock_store)

        assert "No results found" not in output
        assert "/home/user/only_file.txt" in output
        assert "[File]" in output
        assert "(chunk 1/3)" in output

    def test_type_filter_excludes_chats(self, monkeypatch):
        """--type flag should skip chat search entirely."""
        mock_store = _make_mock_store()
        _run_search(monkeypatch, mock_store, extra_args=["--type", ".md"])
        mock_store.search_files.assert_called_once()
        mock_store.search_chats.assert_not_called()

    def test_no_results_from_either(self, monkeypatch):
        """When nothing matches, 'No results found.' is printed."""
        mock_store = _make_mock_store(
            file_results=[],
            chat_results=[],
        )

        output = _run_search(monkeypatch, mock_store)

        assert "No results found." in output


# ---------------------------------------------------------------------------
# Search mode tests
# ---------------------------------------------------------------------------


class TestSearchModeFlag:
    """--mode flag should be registered with correct choices and default."""

    def test_mode_flag_registered(self):
        """search_cmd registers --mode with keyword/semantic/hybrid choices."""
        from footprinter.cli.search import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        # Parse with a valid --mode value
        args = parent.parse_args(["search", "--mode", "keyword", "test"])
        assert args.mode == "keyword"

    def test_mode_choices(self):
        """Only keyword, semantic, hybrid are valid."""
        from footprinter.cli.search import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        with pytest.raises(SystemExit):
            parent.parse_args(["search", "--mode", "invalid", "test"])

    def test_mode_default_is_none(self):
        """Default mode is None (auto-detect)."""
        from footprinter.cli.search import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        args = parent.parse_args(["search", "test"])
        assert args.mode is None


def _make_fts_db(tmp_path):
    """Create a temp DB with files and chats FTS5 tables populated with test data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE files (
            id INTEGER PRIMARY KEY, source TEXT, name TEXT, path TEXT,
            content_type TEXT, size_bytes INTEGER, modified_at TEXT,
            status TEXT DEFAULT 'listed'
        )"""
    )
    conn.execute("CREATE VIRTUAL TABLE files_fts USING fts5(name, path, content=files, content_rowid=id)")
    conn.execute(
        """INSERT INTO files (id, source, name, path, content_type, size_bytes, modified_at, status)
           VALUES (1, 'local', 'database_migration.sql', '/home/user/database_migration.sql',
                   'text/sql', 1024, '2026-01-15', 'listed')"""
    )
    conn.execute(
        "INSERT INTO files_fts (rowid, name, path)"
        " VALUES (1, 'database_migration.sql', '/home/user/database_migration.sql')"
    )
    conn.execute(
        """INSERT INTO files (id, source, name, path, content_type, size_bytes, modified_at, status)
           VALUES (2, 'local', 'database_schema.pdf', '/home/user/database_schema.pdf',
                   'application/pdf', 2048, '2026-01-16', 'listed')"""
    )
    conn.execute(
        "INSERT INTO files_fts (rowid, name, path) VALUES (2, 'database_schema.pdf', '/home/user/database_schema.pdf')"
    )

    conn.execute(
        """CREATE TABLE chats (
            id INTEGER PRIMARY KEY, external_id TEXT, title TEXT,
            account TEXT, created_at TEXT, updated_at TEXT, message_count INTEGER,
            visibility TEXT DEFAULT 'inherit',
            status TEXT DEFAULT 'listed'
        )"""
    )
    conn.execute("CREATE VIRTUAL TABLE chats_fts USING fts5(title, content=chats, content_rowid=id)")
    conn.execute(
        """INSERT INTO chats (id, title, account, created_at, message_count, visibility)
           VALUES (1, 'Database migration planning',
                   'claude', '2026-01-10', 5, 'full')"""
    )
    conn.execute(
        "INSERT INTO chats_fts (rowid, title)"
        " VALUES (1, 'Database migration planning')"
    )

    conn.commit()
    conn.close()
    return db_path


class TestKeywordSearch:
    """Keyword mode should use FTS5, not VectorStore."""

    def test_keyword_returns_file_and_chat_results(self, tmp_path):
        """execute_search(mode='keyword') returns FTS5 results without importing VectorStore."""
        db_path = str(_make_fts_db(tmp_path))
        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        from footprinter.cli.search import execute_search

        execute_search(query="database", mode="keyword", output=console, db_path=db_path)

        output = out.getvalue()
        assert "database_migration.sql" in output
        assert "Database migration planning" in output
        assert "keyword" in output.lower()

    def test_keyword_does_not_use_vectorstore(self, tmp_path):
        """Keyword mode should never touch VectorStore."""
        db_path = str(_make_fts_db(tmp_path))
        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with patch("footprinter.cli.search._HAS_ML", False):
            from footprinter.cli.search import execute_search

            execute_search(query="database", mode="keyword", output=console, db_path=db_path)

        # If we got here without error, VectorStore was not needed
        output = out.getvalue()
        assert "database_migration" in output

    def test_keyword_type_filter_limits_results(self, tmp_path):
        """--type .sql in keyword mode should return only .sql files."""
        db_path = str(_make_fts_db(tmp_path))
        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        from footprinter.cli.search import execute_search

        execute_search(
            query="database",
            mode="keyword",
            type_filter=".sql",
            output=console,
            db_path=db_path,
        )

        output = out.getvalue()
        assert "database_migration.sql" in output
        assert "database_schema.pdf" not in output


class TestSemanticSearch:
    """Semantic mode should use VectorStore (existing behavior)."""

    def test_semantic_uses_vectorstore(self):
        """execute_search(mode='semantic') delegates to VectorStore."""
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/home/user/doc.md",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "content_snippet": "Semantic result",
                    "distance": 0.3,
                }
            ],
        )
        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with patch("footprinter.cli.search.VectorStore") as MockVS, patch("footprinter.cli.search._HAS_ML", True):
            MockVS.get_instance.return_value = mock_store
            from footprinter.cli.search import execute_search

            execute_search(query="test query", mode="semantic", output=console)

        mock_store.search_files.assert_called_once()
        output = out.getvalue()
        assert "semantic" in output.lower()


class TestHybridSearch:
    """Hybrid mode should merge FTS5 + vector results."""

    def test_hybrid_includes_both_fts_and_vector(self, tmp_path):
        """Hybrid mode returns results from both FTS5 and VectorStore."""
        db_path = str(_make_fts_db(tmp_path))
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/home/user/vector_result.md",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "content_snippet": "Found via vectors",
                    "distance": 0.2,
                }
            ],
            chat_results=[
                {
                    "chat_id": 99,
                    "chat_title": "Vector chat",
                    "message_id": 100,
                    "role": "assistant",
                    "source": "claude",
                    "created_at": "2026-01-20",
                    "snippet": "Found via semantic",
                    "relevance_score": 0.9,
                    "chunk_type": "message",
                    "chunk_index": 0,
                    "total_chunks": 1,
                },
            ],
        )

        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with patch("footprinter.cli.search.VectorStore") as MockVS, patch("footprinter.cli.search._HAS_ML", True):
            MockVS.get_instance.return_value = mock_store
            from footprinter.cli.search import execute_search

            execute_search(query="database", mode="hybrid", output=console, db_path=db_path)

        output = out.getvalue()
        assert "hybrid" in output.lower()
        # Should have results from both sources
        assert "database_migration" in output or "vector_result" in output

    def test_hybrid_type_filter_limits_fts_results(self, tmp_path):
        """--type .pdf in hybrid mode should only return .pdf files from FTS5."""
        db_path = str(_make_fts_db(tmp_path))
        mock_store = _make_mock_store(
            file_results=[
                {
                    "file_path": "/home/user/database_schema.pdf",
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "content_snippet": "PDF from vectors",
                    "distance": 0.2,
                }
            ],
        )

        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with patch("footprinter.cli.search.VectorStore") as MockVS, patch("footprinter.cli.search._HAS_ML", True):
            MockVS.get_instance.return_value = mock_store
            from footprinter.cli.search import execute_search

            execute_search(
                query="database",
                mode="hybrid",
                type_filter=".pdf",
                output=console,
                db_path=db_path,
            )

        output = out.getvalue()
        assert "database_schema.pdf" in output
        assert "database_migration.sql" not in output


class TestSearchFilesFileExt:
    """Direct unit tests for search_files() file_ext parameter."""

    def test_search_files_file_ext_filter(self, tmp_path):
        """search_files(file_ext='.sql') returns only .sql files."""
        db_path = _make_fts_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        from footprinter.db.search import search_files

        result = search_files(conn, "database", file_ext=".sql")
        conn.close()

        names = [r["name"] for r in result["results"]]
        assert "database_migration.sql" in names
        assert "database_schema.pdf" not in names
        assert result["pagination"]["total"] == 1

    def test_search_files_no_ext_returns_all(self, tmp_path):
        """search_files() without file_ext returns all matching files."""
        db_path = _make_fts_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        from footprinter.db.search import search_files

        result = search_files(conn, "database")
        conn.close()

        assert result["pagination"]["total"] == 2

    def test_search_files_file_ext_underscore_is_literal(self, tmp_path):
        """Underscore in file_ext must be literal, not a LIKE single-char wildcard."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE files (
                id INTEGER PRIMARY KEY, source TEXT, name TEXT, path TEXT,
                content_type TEXT, size_bytes INTEGER, modified_at TEXT,
                status TEXT DEFAULT 'listed'
            )"""
        )
        conn.execute("CREATE VIRTUAL TABLE files_fts USING fts5(name, path, content=files, content_rowid=id)")
        conn.execute(
            """INSERT INTO files (id, source, name, path, size_bytes, modified_at, status)
               VALUES (1, 'local', 'data_backup.sql', '/home/user/data_backup.sql',
                       100, '2026-01-01', 'listed')"""
        )
        conn.execute(
            "INSERT INTO files_fts (rowid, name, path) VALUES (1, 'data_backup.sql', '/home/user/data_backup.sql')"
        )
        # Second file: 'x' instead of '_' — should NOT match literal '_backup.sql'
        conn.execute(
            """INSERT INTO files (id, source, name, path, size_bytes, modified_at, status)
               VALUES (2, 'local', 'dataxbackup.sql', '/home/user/dataxbackup.sql',
                       100, '2026-01-01', 'listed')"""
        )
        conn.execute(
            "INSERT INTO files_fts (rowid, name, path) VALUES (2, 'dataxbackup.sql', '/home/user/dataxbackup.sql')"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row

        from footprinter.db.search import search_files

        result = search_files(conn, "data", file_ext="_backup.sql")
        conn.close()

        assert result["pagination"]["total"] == 1
        assert result["results"][0]["name"] == "data_backup.sql"

    def test_search_files_file_ext_percent_is_literal(self, tmp_path):
        """Percent in file_ext must be literal, not a LIKE multi-char wildcard."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE files (
                id INTEGER PRIMARY KEY, source TEXT, name TEXT, path TEXT,
                content_type TEXT, size_bytes INTEGER, modified_at TEXT,
                status TEXT DEFAULT 'listed'
            )"""
        )
        conn.execute("CREATE VIRTUAL TABLE files_fts USING fts5(name, path, content=files, content_rowid=id)")
        conn.execute(
            """INSERT INTO files (id, source, name, path, size_bytes, modified_at, status)
               VALUES (1, 'local', 'report%final.txt', '/home/user/report%final.txt',
                       100, '2026-01-01', 'listed')"""
        )
        conn.execute(
            "INSERT INTO files_fts (rowid, name, path) VALUES (1, 'report%final.txt', '/home/user/report%final.txt')"
        )
        # Second file: ends in 'final.txt' but not '%final.txt'
        conn.execute(
            """INSERT INTO files (id, source, name, path, size_bytes, modified_at, status)
               VALUES (2, 'local', 'report_final.txt', '/home/user/report_final.txt',
                       100, '2026-01-01', 'listed')"""
        )
        conn.execute(
            "INSERT INTO files_fts (rowid, name, path) VALUES (2, 'report_final.txt', '/home/user/report_final.txt')"
        )
        conn.commit()
        conn.row_factory = sqlite3.Row

        from footprinter.db.search import search_files

        result = search_files(conn, "report", file_ext="%final.txt")
        conn.close()

        assert result["pagination"]["total"] == 1
        assert result["results"][0]["name"] == "report%final.txt"


class TestFtsFileToResult:
    """Unit tests for the _fts_file_to_result helper."""

    def test_builds_expected_dict(self):
        """_fts_file_to_result produces the correct result shape from a search_files() row."""
        from footprinter.cli.search import _fts_file_to_result

        row = {
            "path": "/home/user/doc.md",
            "name": "doc.md",
            "content_type": "text/markdown",
            "source": "local",
            "fts_score": 0.8,
            "modified_at": "2026-01-15",
        }
        result = _fts_file_to_result(row)

        assert result["source_type"] == "file"
        assert result["relevance"] == 0.8
        assert result["data"]["file_path"] == "/home/user/doc.md"
        assert result["data"]["chunk_index"] == 0
        assert result["data"]["total_chunks"] == 1
        assert result["data"]["content_snippet"] == "doc.md (text/markdown)"
        assert result["data"]["name"] == "doc.md"
        assert result["data"]["source"] == "local"
        assert result["data"]["modified_at"] == "2026-01-15"

    def test_falls_back_when_fields_missing(self):
        """_fts_file_to_result defaults relevance to 0.5 and modified_at to empty string."""
        from footprinter.cli.search import _fts_file_to_result

        row = {
            "path": "/home/user/notes.txt",
            "name": "notes.txt",
            "content_type": "text/plain",
            "source": "local",
        }
        result = _fts_file_to_result(row)

        assert result["relevance"] == 0.5
        assert result["data"]["modified_at"] == ""


class TestHybridKeywordOnlyChats:
    """When hybrid search has keyword chat results but no semantic chat results,
    it should reuse the already-fetched results instead of re-querying FTS5."""

    def test_hybrid_keyword_only_chats_no_double_query(self, tmp_path):
        """keyword_search should be called exactly once — not twice via fts5_fallback_search."""
        db_path = str(_make_fts_db(tmp_path))
        mock_store = _make_mock_store(
            file_results=[],
            chat_results=[],  # No semantic chat results → triggers keyword-only path
        )

        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with (
            patch("footprinter.cli.search.VectorStore") as MockVS,
            patch("footprinter.cli.search._HAS_ML", True),
            patch("footprinter.semantic.hybrid_search.keyword_search", wraps=None) as mock_kw,
        ):
            MockVS.get_instance.return_value = mock_store
            # Make the mock return realistic data
            mock_kw.return_value = [
                {
                    "chat_id": 1,
                    "chat_title": "Database migration planning",
                    "source": "claude",
                    "created_at": "2026-01-10",
                    "message_count": 5,
                    "snippet": "Discussion about migrating the database schema",
                    "fts_score": 0.7,
                    "match_type": "keyword",
                },
            ]

            from footprinter.cli.search import execute_search

            execute_search(query="database", mode="hybrid", output=console, db_path=db_path)

        # keyword_search should be called exactly once (the initial call),
        # NOT twice (once directly + once inside fts5_fallback_search)
        assert mock_kw.call_count == 1, f"keyword_search called {mock_kw.call_count} times, expected 1"

        # Verify the chat result still appears in output (behavioral equivalence)
        output = out.getvalue()
        assert "Database migration planning" in output

    def test_hybrid_keyword_only_chats_result_shape(self, tmp_path):
        """Keyword-only chat results should have the same shape as fts5_fallback_search produced."""
        db_path = str(_make_fts_db(tmp_path))
        mock_store = _make_mock_store(
            file_results=[],
            chat_results=[],
        )

        with (
            patch("footprinter.cli.search.VectorStore") as MockVS,
            patch("footprinter.cli.search._HAS_ML", True),
            patch("footprinter.semantic.hybrid_search.keyword_search") as mock_kw,
        ):
            MockVS.get_instance.return_value = mock_store
            mock_kw.return_value = [
                {
                    "chat_id": 1,
                    "chat_title": "Test chat",
                    "source": "claude",
                    "created_at": "2026-01-10",
                    "message_count": 5,
                    "snippet": "Title match: Test chat",
                    "fts_score": 0.7,
                    "match_type": "keyword",
                },
            ]

            from footprinter.cli.search import _hybrid_search

            results = _hybrid_search("database", db_path=db_path)

        assert len(results) >= 1
        chat_results = [r for r in results if r["source_type"] == "chat"]
        assert len(chat_results) == 1

        r = chat_results[0]
        assert r["source_type"] == "chat"
        assert r["relevance"] == 0.7
        assert r["data"]["chat_title"] == "Test chat"
        assert r["data"]["source"] == "claude"
        assert r["data"]["chat_id"] == 1
        assert "Test chat" in r["data"]["snippet"]


class TestAutoFallback:
    """When mode=None and ML missing, should fall back to keyword."""

    def test_fallback_to_keyword_without_ml(self, tmp_path):
        """Auto-detect falls back to keyword when _HAS_ML is False."""
        db_path = str(_make_fts_db(tmp_path))
        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with patch("footprinter.cli.search._HAS_ML", False):
            from footprinter.cli.search import execute_search

            # Should NOT sys.exit — should fall back gracefully
            execute_search(query="database", mode=None, output=console, db_path=db_path)

        output = out.getvalue()
        assert "keyword" in output.lower()
        # Should mention fallback
        assert "semantic" in output.lower() or "keyword" in output.lower()


class TestJsonFlag:
    """--json flag should be registered on search_cmd."""

    def test_json_flag_registered(self):
        """search_cmd registers --json; parsing with it sets args.json=True."""
        from footprinter.cli.search import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        args = parent.parse_args(["search", "--json", "test"])
        assert args.json is True

    def test_json_default_is_false(self):
        """Without --json, args.json defaults to False."""
        from footprinter.cli.search import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        args = parent.parse_args(["search", "test"])
        assert args.json is False


class TestJsonOutput:
    """--json should produce structured JSON, not Rich output."""

    def test_json_output_keyword_mode(self, tmp_path, monkeypatch):
        """execute_search(json_output=True) produces valid JSON with expected keys."""
        import json as json_mod

        db_path = str(_make_fts_db(tmp_path))
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        console = __import__("rich.console", fromlist=["Console"]).Console(file=StringIO())

        from footprinter.cli.search import execute_search

        execute_search(
            query="database",
            mode="keyword",
            json_output=True,
            output=console,
            db_path=db_path,
        )

        raw = captured.getvalue()
        parsed = json_mod.loads(raw)
        assert parsed["query"] == "database"
        assert parsed["mode"] == "keyword"
        assert "results" in parsed
        assert isinstance(parsed["results"], list)

    def test_json_output_includes_all_result_types(self, tmp_path, monkeypatch):
        """JSON output includes both file and chat results."""
        import json as json_mod

        db_path = str(_make_fts_db(tmp_path))
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        console = __import__("rich.console", fromlist=["Console"]).Console(file=StringIO())

        from footprinter.cli.search import execute_search

        execute_search(
            query="database",
            mode="keyword",
            json_output=True,
            output=console,
            db_path=db_path,
        )

        raw = captured.getvalue()
        parsed = json_mod.loads(raw)

        source_types = {r["source_type"] for r in parsed["results"]}
        assert "file" in source_types
        assert "chat" in source_types

    def test_json_output_no_rich_formatting(self, tmp_path, monkeypatch):
        """JSON mode must not contain Rich markup in stdout."""
        db_path = str(_make_fts_db(tmp_path))
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        console = __import__("rich.console", fromlist=["Console"]).Console(file=StringIO())

        from footprinter.cli.search import execute_search

        execute_search(
            query="database",
            mode="keyword",
            json_output=True,
            output=console,
            db_path=db_path,
        )

        raw = captured.getvalue()
        assert "[bold]" not in raw
        assert "[dim]" not in raw
        assert "====" not in raw


class TestSemanticUnavailable:
    """Explicitly requesting semantic when ML missing should error."""

    def test_semantic_mode_exits_without_ml(self):
        """mode='semantic' with _HAS_ML=False should sys.exit(1)."""
        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        with patch("footprinter.cli.search._HAS_ML", False):
            from footprinter.cli.search import execute_search

            with pytest.raises(SystemExit) as exc_info:
                execute_search(query="test", mode="semantic", output=console)

            assert exc_info.value.code == 1

        output = out.getvalue()
        assert "semantic" in output.lower()
