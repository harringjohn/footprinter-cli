"""Tests for semantic_service — embedding search with FTS5 fallback."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from footprinter.services import Role, semantic_service


class TestSemanticServiceValidation:
    def test_short_query_rejected(self, service_db):
        result = semantic_service.semantic_search(
            service_db,
            "ab",
            role=Role.VIEWER,
        )
        assert result.get("status") == "invalid_query"

    def test_invalid_source_rejected(self, service_db):
        result = semantic_service.semantic_search(
            service_db,
            "test query",
            role=Role.VIEWER,
            source="bogus",
        )
        assert result.get("status") == "invalid_source"


class TestSemanticServiceChats:
    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.commit()

    def test_chat_fts5_fallback(self, service_db):
        """FTS5 fallback returns visible chats by title match."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "Visible Chat",
            role=Role.VIEWER,
            source="chats",
        )
        assert "chats" in result
        titles = [c.get("chat_title") for c in result["chats"]]
        assert any("Visible" in (t or "") for t in titles)

    def test_hidden_chat_excluded(self, service_db):
        """Hidden chats excluded from results."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "Chat",
            role=Role.VIEWER,
            source="chats",
        )
        chat_ids = [c.get("chat_id") or c.get("id") for c in result.get("chats", [])]
        assert 2 not in chat_ids  # hidden chat


class TestSemanticServiceFiles:
    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_file_fts5_fallback(self, service_db):
        """FTS5 fallback returns visible files by name match."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "readme",
            role=Role.VIEWER,
            source="files",
        )
        assert "files" in result
        file_names = [f.get("name") for f in result["files"]]
        assert "readme.md" in file_names

    def test_enrichment_failure_propagates_not_fallback(self, service_db):
        """DB enrichment failure must raise, not silently fall through to FTS5."""
        mock_store = MagicMock()
        mock_store.search_files.return_value = [
            {"file_id": 1, "distance": 0.5, "content_snippet": "test content"},
        ]
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.return_value = mock_store

        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}),
            patch.object(
                semantic_service,
                "enrich_file_metadata",
                side_effect=RuntimeError("DB connection lost"),
            ),
        ):
            with pytest.raises(RuntimeError, match="DB connection lost"):
                semantic_service.semantic_search(
                    service_db,
                    "test query",
                    role=Role.VIEWER,
                    source="files",
                )

    def test_vector_init_failure_still_triggers_fts5_fallback(self, service_db):
        """VectorStore.get_instance() failure falls back to FTS5 (regression guard)."""
        self._rebuild_fts(service_db)
        mock_vs_module = MagicMock()
        mock_vs_module.VectorStore.get_instance.side_effect = RuntimeError("vector store init failed")

        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_vs_module}):
            result = semantic_service.semantic_search(
                service_db,
                "readme",
                role=Role.VIEWER,
                source="files",
            )
        assert "files" in result
        file_names = [f.get("name") for f in result["files"]]
        assert "readme.md" in file_names

    def test_fts5_fallback_snippet_shows_content_when_allowed(self, service_db):
        """FTS5 fallback snippets show content_preview when mcp_read allows."""
        service_db.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, mcp_view, mcp_read, content_preview) "
            "VALUES (50, 'local', 'budget.xlsx', '/Users/u/docs/budget.xlsx', "
            "'listed', 'spreadsheet', 8000, '2026-01-15', 'visible', 'allow', "
            "'Q4 revenue figures for review')"
        )
        service_db.commit()
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "budget",
            role=Role.VIEWER,
            source="files",
        )
        assert "files" in result
        matches = [f for f in result["files"] if f.get("name") == "budget.xlsx"]
        assert len(matches) == 1
        snippet = matches[0].get("snippet", "")
        assert "revenue figures" in snippet

    def test_hidden_file_excluded(self, service_db):
        """Hidden files excluded from results."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "secret",
            role=Role.VIEWER,
            source="files",
        )
        file_names = [f.get("name") for f in result.get("files", [])]
        assert "secret.py" not in file_names


class TestSemanticServiceD2Access:
    """D2: semantic matches are content-derived — visible items need read access."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_visible_deny_excluded(self, service_db):
        """Visible item with mcp_read='deny' excluded from semantic results."""
        # Create a visible+deny chat
        service_db.execute(
            """INSERT INTO chats (id, external_id, account, title, message_count,
                                  mcp_view, mcp_read)
               VALUES (10, 'conv-deny', 'claude', 'Denied Chat', 1, 'visible', 'deny')"""
        )
        service_db.commit()
        self._rebuild_fts(service_db)

        result = semantic_service.semantic_search(
            service_db,
            "Denied Chat",
            role=Role.VIEWER,
            source="chats",
        )
        chat_ids = [c.get("chat_id") or c.get("id") for c in result.get("chats", [])]
        assert 10 not in chat_ids  # visible but denied → excluded


class TestSemanticFallbackNote:
    """Fallback note must surface even when FTS5 returns empty."""

    @pytest.fixture(autouse=True)
    def _force_vector_unavailable(self):
        """Ensure vector search raises ImportError regardless of chromadb."""
        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": None}):
            yield

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_note_present_when_fallback_returns_results(self, service_db):
        """note field present when vector search fails but FTS5 finds matches."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "Visible Chat",
            role=Role.VIEWER,
            source="chats",
        )
        assert len(result["chats"]) > 0
        assert "note" in result
        assert "keyword-based" in result["note"]

    def test_note_present_when_fallback_returns_empty(self, service_db):
        """note field present when vector search fails AND FTS5 returns nothing."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "zzz_nonexistent_query",
            role=Role.VIEWER,
            source="all",
        )
        assert result.get("chats") == []
        assert result.get("files") == []
        assert "note" in result
        assert "keyword-based" in result["note"]

    def test_summary_distinguishes_fallback_from_normal_empty(self, service_db):
        """Summary says 'semantic search unavailable' not generic 'no X found'."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "zzz_nonexistent_query",
            role=Role.VIEWER,
            source="all",
        )
        assert "Semantic search unavailable" in result["summary"]
        assert "Tips:" not in result["summary"]

    def test_summary_shows_fallback_annotation_with_results(self, service_db):
        """When fallback produces results, summary annotates them as keyword-based."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "Visible Chat",
            role=Role.VIEWER,
            source="chats",
        )
        assert len(result["chats"]) > 0
        assert "keyword match" in result["summary"]

    def test_fallback_logged_at_warning(self, service_db, caplog):
        """Vector search fallback emits WARNING, not INFO."""
        self._rebuild_fts(service_db)
        with caplog.at_level(logging.DEBUG, logger="footprinter.services.semantic_service"):
            semantic_service.semantic_search(
                service_db,
                "Visible Chat",
                role=Role.VIEWER,
                source="chats",
            )
        fallback_msgs = [r for r in caplog.records if "falling back to FTS5" in r.message]
        assert len(fallback_msgs) > 0
        assert all(r.levelno == logging.WARNING for r in fallback_msgs)

    def test_note_deduplicated_for_all_source(self, service_db):
        """When both chats and files fall back, the note isn't duplicated."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "zzz_nonexistent_query",
            role=Role.VIEWER,
            source="all",
        )
        note = result.get("note", "")
        assert note.count("keyword-based") == 1

    def test_double_failure_summary_says_failed(self, service_db):
        """When both vector and FTS5 crash, summary says 'failed' not 'Tips:'."""
        # Don't rebuild FTS — let FTS5 queries crash on missing table data
        with (
            patch.object(
                semantic_service,
                "chat_fts5_fallback",
                side_effect=RuntimeError("FTS5 broken"),
            ),
            patch.object(
                semantic_service,
                "file_fts5_fallback",
                side_effect=RuntimeError("FTS5 broken"),
            ),
        ):
            result = semantic_service.semantic_search(
                service_db,
                "anything",
                role=Role.VIEWER,
                source="all",
            )
        assert "search failed" in result["summary"].lower()
        assert "Tips:" not in result["summary"]
        assert "note" in result
        assert "search failed" in result["note"].lower()


class TestSemanticIncludeFlags:
    """include_unlisted/include_removed are ADMIN-only (FPR-1678).

    Threading reaches enrich_chat_visibility/enrich_file_metadata and the
    FTS5 fallbacks. VIEWER ignores the flags.
    """

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def _seed_unlisted_chat(self, conn) -> int:
        cur = conn.execute(
            """INSERT INTO chats (external_id, account, title, summary, message_count,
                                  status, mcp_view, mcp_read)
               VALUES ('conv-arch', 'claude', 'Archived Chat',
                       'A chat about archived stuff', 1,
                       'unlisted', 'visible', 'allow')"""
        )
        conn.commit()
        return cur.lastrowid

    def _seed_removed_file(self, conn) -> int:
        cur = conn.execute(
            """INSERT INTO files (name, path, source, status, status_reason,
                                  content_type, size_bytes, mcp_view, mcp_read,
                                  content_preview)
               VALUES ('archived.md', '/Users/u/Work/alpha/archived.md', 'local',
                       'removed', 'deleted_by_user', 'markdown', 50,
                       'visible', 'allow', 'archived content')"""
        )
        conn.commit()
        return cur.lastrowid

    def test_viewer_ignores_flags_for_chats(self, service_db):
        chat_id = self._seed_unlisted_chat(service_db)
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "Archived Chat",
            role=Role.VIEWER,
            source="chats",
            include_unlisted=True,
        )
        chat_ids = [c.get("chat_id") or c.get("id") for c in result.get("chats", [])]
        assert chat_id not in chat_ids

    def test_admin_include_unlisted_returns_unlisted_chat(self, service_db):
        chat_id = self._seed_unlisted_chat(service_db)
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "Archived Chat",
            role=Role.ADMIN,
            source="chats",
            include_unlisted=True,
        )
        chat_ids = [c.get("chat_id") or c.get("id") for c in result.get("chats", [])]
        assert chat_id in chat_ids

    def test_viewer_ignores_flags_for_files(self, service_db):
        file_id = self._seed_removed_file(service_db)
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "archived",
            role=Role.VIEWER,
            source="files",
            include_removed=True,
        )
        file_ids = [f.get("id") for f in result.get("files", [])]
        assert file_id not in file_ids

    def test_admin_include_removed_returns_removed_file(self, service_db):
        file_id = self._seed_removed_file(service_db)
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db,
            "archived",
            role=Role.ADMIN,
            source="files",
            include_removed=True,
        )
        file_ids = [f.get("id") for f in result.get("files", [])]
        assert file_id in file_ids
