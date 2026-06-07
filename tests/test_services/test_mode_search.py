"""Tests for footprinter.services.search_service — mode-based search (keyword/semantic/hybrid)."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


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


def _open_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


class TestMlAvailable:
    """ml_available() should report ML dependency status."""

    def test_returns_bool(self):
        from footprinter.services.search_service import ml_available

        result = ml_available()
        assert isinstance(result, bool)

    def test_false_when_import_fails(self):
        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": None}):
            from footprinter.services.search_service import ml_available

            result = ml_available()
            assert result is False


class TestFtsFileToResult:
    """_fts_file_to_result should live in the service layer."""

    def test_builds_expected_dict(self):
        from footprinter.services.search_service import _fts_file_to_result

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
        from footprinter.services.search_service import _fts_file_to_result

        row = {
            "path": "/home/user/notes.txt",
            "name": "notes.txt",
            "content_type": "text/plain",
            "source": "local",
        }
        result = _fts_file_to_result(row)

        assert result["relevance"] == 0.5
        assert result["data"]["modified_at"] == ""


class TestKeywordModeSearch:
    """mode_search(mode='keyword') should return FTS5 results."""

    def test_returns_file_and_chat_results(self, tmp_path):
        db_path = _make_fts_db(tmp_path)
        conn = _open_conn(db_path)

        from footprinter.services.search_service import mode_search

        results = mode_search("database", mode="keyword", conn=conn)
        conn.close()

        source_types = {r["source_type"] for r in results}
        assert "file" in source_types
        assert "chat" in source_types

    def test_type_filter_limits_to_files_only(self, tmp_path):
        db_path = _make_fts_db(tmp_path)
        conn = _open_conn(db_path)

        from footprinter.services.search_service import mode_search

        results = mode_search("database", mode="keyword", type_filter=".sql", conn=conn)
        conn.close()

        assert all(r["source_type"] == "file" for r in results)
        file_names = [r["data"]["name"] for r in results]
        assert "database_migration.sql" in file_names
        assert "database_schema.pdf" not in file_names

    def test_respects_limit(self, tmp_path):
        db_path = _make_fts_db(tmp_path)
        conn = _open_conn(db_path)

        from footprinter.services.search_service import mode_search

        results = mode_search("database", mode="keyword", limit=1, conn=conn)
        conn.close()

        assert len(results) <= 1


class TestSemanticModeSearch:
    """mode_search(mode='semantic') should delegate to VectorStore."""

    def test_delegates_to_vectorstore(self):
        mock_store = MagicMock()
        mock_store.search_files.return_value = [
            {
                "file_path": "/home/user/doc.md",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Semantic result",
                "distance": 0.3,
            }
        ]
        mock_store.search_chats.return_value = []

        with patch("footprinter.semantic.vector_store.VectorStore") as MockVS:
            MockVS.get_instance.return_value = mock_store
            from footprinter.services.search_service import mode_search

            results = mode_search("test query", mode="semantic")

        mock_store.search_files.assert_called_once()
        assert len(results) >= 1
        assert results[0]["source_type"] == "file"


class TestHybridModeSearch:
    """mode_search(mode='hybrid') should merge FTS5 + vector results."""

    def test_includes_both_sources(self, tmp_path):
        db_path = _make_fts_db(tmp_path)
        conn = _open_conn(db_path)

        mock_store = MagicMock()
        mock_store.search_files.return_value = [
            {
                "file_path": "/home/user/vector_result.md",
                "chunk_index": 0,
                "total_chunks": 1,
                "content_snippet": "Found via vectors",
                "distance": 0.2,
            }
        ]
        mock_store.search_chats.return_value = [
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
        ]

        with patch("footprinter.semantic.vector_store.VectorStore") as MockVS:
            MockVS.get_instance.return_value = mock_store
            from footprinter.services.search_service import mode_search

            results = mode_search("database", mode="hybrid", conn=conn)

        conn.close()

        source_types = {r["source_type"] for r in results}
        assert "file" in source_types


class TestModeSearchResultShape:
    """All mode_search results should have consistent shape."""

    def test_keyword_result_shape(self, tmp_path):
        db_path = _make_fts_db(tmp_path)
        conn = _open_conn(db_path)

        from footprinter.services.search_service import mode_search

        results = mode_search("database", mode="keyword", conn=conn)
        conn.close()

        for r in results:
            assert "source_type" in r
            assert "relevance" in r
            assert "data" in r
            assert r["source_type"] in ("file", "chat")
            assert isinstance(r["relevance"], (int, float))
            assert isinstance(r["data"], dict)
