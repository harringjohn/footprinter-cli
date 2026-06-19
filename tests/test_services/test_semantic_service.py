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

    def test_fts5_fallback_excerpt_shows_content_when_allowed(self, service_db):
        """FTS5 fallback excerpt is sourced from content_preview when access allows."""
        service_db.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (50, 'local', 'budget.xlsx', '/Users/u/docs/budget.xlsx', "
            "'listed', 'spreadsheet', 8000, '2026-01-15', 'full', 'allow', "
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
        match = matches[0]
        assert "revenue figures" in match["excerpt"]
        assert match["excerpt_source"] == "content_preview"
        assert match["chars_returned"] == len(match["excerpt"])
        assert "snippet" not in match

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


class TestSemanticExcerptContract:
    """Every content-bearing semantic result carries the uniform excerpt contract."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.commit()

    def _mock_vs_module(self, *, files=None, chats=None):
        mock_store = MagicMock()
        mock_store.search_files.return_value = files or []
        mock_store.search_chats.return_value = chats or []
        mock_module = MagicMock()
        mock_module.VectorStore.get_instance.return_value = mock_store
        return mock_module

    def test_vector_file_excerpt_is_chunk_with_provenance(self, service_db):
        """Vector file hits carry excerpt_source='chunk' + chunk index/totals."""
        long_chunk = "x" * 500  # vector store slices the chunk to the budget
        mock_module = self._mock_vs_module(
            files=[
                {
                    "file_id": 1,
                    "distance": 0.2,
                    "content_snippet": long_chunk,
                    "content_length": 1800,
                    "chunk_index": 2,
                    "total_chunks": 9,
                }
            ]
        )
        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        match = [f for f in result["files"] if f.get("id") == 1][0]
        assert match["excerpt"] == long_chunk
        assert match["excerpt_source"] == "chunk"
        assert match["chars_returned"] == 500
        assert match["chars_available"] == 1800
        assert match["has_more"] is True
        assert match["chunk_index"] == 2
        assert match["total_chunks"] == 9
        assert "snippet" not in match
        assert "content_snippet" not in match

    def test_vector_chat_excerpt_is_chunk_with_provenance(self, service_db):
        """Vector chat hits carry excerpt_source='chunk' + chunk index/totals."""
        window = "...the matched conversation window..."
        mock_module = self._mock_vs_module(
            chats=[
                {
                    "chat_id": 1,
                    "chat_title": "Visible Chat",
                    "snippet": window,
                    "content_length": 1200,
                    "chunk_index": 0,
                    "total_chunks": 3,
                    "relevance_score": 0.9,
                    "source": "claude",
                    "created_at": "",
                    "message_id": 11,
                }
            ]
        )
        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}):
            result = semantic_service.semantic_search(
                service_db, "conversation", role=Role.VIEWER, source="chats"
            )
        match = [c for c in result["chats"] if c.get("chat_id") == 1][0]
        assert match["excerpt"] == window
        assert match["excerpt_source"] == "chunk"
        assert match["chars_available"] == 1200
        assert match["has_more"] is True
        assert match["chunk_index"] == 0
        assert match["total_chunks"] == 3
        assert "snippet" not in match

    def test_file_fallback_excerpt_falls_back_to_name_path(self, service_db):
        """A file with no content_preview falls back to a name/path excerpt."""
        service_db.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access, content_preview) "
            "VALUES (60, 'local', 'plan.md', '/Users/u/docs/plan.md', "
            "'listed', 'markdown', 400, '2026-01-15', 'full', 'allow', NULL)"
        )
        service_db.commit()
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db, "plan", role=Role.VIEWER, source="files"
        )
        match = [f for f in result["files"] if f.get("name") == "plan.md"][0]
        assert match["excerpt_source"] == "title"
        assert "plan.md" in match["excerpt"]
        assert "/Users/u/docs/plan.md" in match["excerpt"]
        assert "snippet" not in match

    def test_chat_fallback_excerpt_is_title(self, service_db):
        """Degraded (FTS5) chat results carry a title excerpt."""
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db, "Visible Chat", role=Role.VIEWER, source="chats"
        )
        matches = [c for c in result["chats"] if "Visible" in (c.get("chat_title") or "")]
        assert len(matches) >= 1
        match = matches[0]
        assert match["excerpt_source"] == "title"
        assert "Visible Chat" in match["excerpt"]
        assert "snippet" not in match

    def test_vector_file_excerpt_full_chunk_up_to_cap(self, service_db):
        """Full chunk (≤ cap) surfaces in the excerpt — no 500-char truncation."""
        chunk = "z" * 1000
        mock_module = self._mock_vs_module(
            files=[
                {
                    "file_id": 1,
                    "distance": 0.2,
                    "content_snippet": chunk,
                    "content_length": 1000,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            ]
        )
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunk_chars": 1000}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        match = [f for f in result["files"] if f.get("id") == 1][0]
        assert match["excerpt"] == chunk
        assert match["chars_returned"] == 1000
        assert match["chars_available"] == 1000
        assert match["has_more"] is False

    def test_vector_file_excerpt_capped_when_chunk_exceeds(self, service_db):
        """A chunk longer than the cap is capped; has_more reports the remainder."""
        chunk = "z" * 1500
        mock_module = self._mock_vs_module(
            files=[
                {
                    "file_id": 1,
                    "distance": 0.2,
                    "content_snippet": chunk,
                    "content_length": 1500,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            ]
        )
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunk_chars": 1000}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        match = [f for f in result["files"] if f.get("id") == 1][0]
        assert match["chars_returned"] == 1000
        assert match["chars_available"] == 1500
        assert match["has_more"] is True

    def test_vector_file_excerpt_default_cap_when_config_unavailable(self, service_db):
        """When get_config raises, the module-default cap (1000) is used."""
        chunk = "z" * 1500
        mock_module = self._mock_vs_module(
            files=[
                {
                    "file_id": 1,
                    "distance": 0.2,
                    "content_snippet": chunk,
                    "content_length": 1500,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            ]
        )
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                side_effect=RuntimeError("config missing"),
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        match = [f for f in result["files"] if f.get("id") == 1][0]
        assert match["chars_returned"] == semantic_service._DEFAULT_MAX_CHUNK_CHARS
        assert match["chars_returned"] == 1000

    def test_vector_file_excerpt_zero_cap_means_no_cap(self, service_db):
        """max_chunk_chars: 0 returns the whole chunk regardless of length."""
        chunk = "z" * 1800
        mock_module = self._mock_vs_module(
            files=[
                {
                    "file_id": 1,
                    "distance": 0.2,
                    "content_snippet": chunk,
                    "content_length": 1800,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            ]
        )
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunk_chars": 0}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        match = [f for f in result["files"] if f.get("id") == 1][0]
        assert match["excerpt"] == chunk
        assert match["chars_returned"] == 1800
        assert match["has_more"] is False


class TestSemanticMultiChunk:
    """Vector file hits return the top-N chunks per file, relevance-ordered."""

    def _mock_vs_module(self, *, files=None):
        mock_store = MagicMock()
        mock_store.search_files.return_value = files or []
        mock_module = MagicMock()
        mock_module.VectorStore.get_instance.return_value = mock_store
        return mock_module

    @staticmethod
    def _chunk(file_id, chunk_index, total_chunks, distance, text):
        return {
            "file_id": file_id,
            "distance": distance,
            "content_snippet": text,
            "content_length": len(text),
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
        }

    def test_top_n_chunks_per_file_relevance_ordered(self, service_db):
        """Three chunks for one file collapse to a single row carrying a
        relevance-ordered ``chunks`` list; the best chunk is the top-level
        excerpt and equals chunks[0]."""
        # distances 0.2 / 0.6 / 1.0 → relevance 0.9 / 0.7 / 0.5
        files = [
            self._chunk(1, 5, 9, 0.6, "mid relevance chunk"),
            self._chunk(1, 2, 9, 0.2, "best relevance chunk"),
            self._chunk(1, 7, 9, 1.0, "low relevance chunk"),
        ]
        mock_module = self._mock_vs_module(files=files)
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunks_per_file": 3}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        rows = [f for f in result["files"] if f.get("id") == 1]
        assert len(rows) == 1  # one row per file id
        row = rows[0]
        assert "chunks" in row
        assert len(row["chunks"]) == 3
        scores = [c["relevance_score"] for c in row["chunks"]]
        assert scores == sorted(scores, reverse=True)
        # Top-level excerpt is the highest-relevance chunk.
        assert row["excerpt"] == "best relevance chunk"
        assert row["chunk_index"] == 2
        # chunks[0] mirrors the top-level excerpt.
        assert row["chunks"][0]["excerpt"] == row["excerpt"]
        assert row["chunks"][0]["chunk_index"] == row["chunk_index"]

    def test_one_row_per_file(self, service_db):
        """Distinct file ids each get exactly one representative row."""
        service_db.execute(
            "INSERT INTO files (id, source, name, path, status, content_type, "
            "size_bytes, modified_at, visibility, access) "
            "VALUES (4, 'local', 'second.md', '/Users/u/Work/alpha/second.md', "
            "'listed', 'markdown', 700, '2026-01-15', 'full', 'allow')"
        )
        service_db.commit()
        files = [
            self._chunk(1, 0, 2, 0.2, "file one chunk a"),
            self._chunk(1, 1, 2, 0.6, "file one chunk b"),
            self._chunk(4, 0, 1, 0.4, "file four chunk"),
        ]
        mock_module = self._mock_vs_module(files=files)
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunks_per_file": 3}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        ids = sorted(f["id"] for f in result["files"])
        assert ids == [1, 4]
        assert len([f for f in result["files"] if f["id"] == 1]) == 1

    def test_n_caps_the_chunk_list(self, service_db):
        """With max_chunks_per_file: 2, only the two best chunks are kept."""
        files = [
            self._chunk(1, i, 5, distance, f"chunk {i}")
            for i, distance in enumerate([1.0, 0.2, 0.8, 0.4, 0.6])
        ]
        mock_module = self._mock_vs_module(files=files)
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunks_per_file": 2}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        row = [f for f in result["files"] if f.get("id") == 1][0]
        assert len(row["chunks"]) == 2
        # The two highest-relevance chunks: distances 0.2 and 0.4.
        kept_indices = {c["chunk_index"] for c in row["chunks"]}
        assert kept_indices == {1, 3}

    def test_default_n_when_config_unavailable(self, service_db):
        """When get_config raises, the module-default N (3) caps the chunk list."""
        files = [
            self._chunk(1, i, 5, distance, f"chunk {i}")
            for i, distance in enumerate([1.0, 0.2, 0.8, 0.4, 0.6])
        ]
        mock_module = self._mock_vs_module(files=files)
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                side_effect=RuntimeError("config missing"),
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        row = [f for f in result["files"] if f.get("id") == 1][0]
        assert len(row["chunks"]) == semantic_service._DEFAULT_MAX_CHUNKS_PER_FILE
        assert len(row["chunks"]) == 3

    def test_chunks_survive_viewer_trim_no_governance(self, service_db):
        """``chunks`` survives the VIEWER keep-allowlist trim and each chunk dict
        carries only excerpt-contract + chunk-index keys (no governance)."""
        from footprinter.services.access_service import GOVERNANCE_FIELDS

        files = [
            self._chunk(1, 0, 2, 0.2, "chunk a"),
            self._chunk(1, 1, 2, 0.6, "chunk b"),
        ]
        mock_module = self._mock_vs_module(files=files)
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunks_per_file": 3}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        row = [f for f in result["files"] if f.get("id") == 1][0]
        assert "chunks" in row  # not stripped by _trim_file_result
        allowed_keys = {
            "excerpt",
            "excerpt_source",
            "chars_returned",
            "chars_available",
            "has_more",
            "chunk_index",
            "total_chunks",
            "relevance_score",
        }
        for chunk in row["chunks"]:
            assert set(chunk).issubset(allowed_keys), (
                f"chunk dict carries unexpected keys: {set(chunk) - allowed_keys}"
            )
            for field in GOVERNANCE_FIELDS:
                assert field not in chunk

    def test_single_chunk_file_regression(self, service_db):
        """One chunk for one file → a chunks list of length 1 whose sole entry
        equals the top-level excerpt; flat fields unchanged from today."""
        files = [self._chunk(1, 3, 9, 0.2, "the only matched chunk")]
        mock_module = self._mock_vs_module(files=files)
        with (
            patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}),
            patch(
                "footprinter.source_registry.get_config",
                return_value={"semantic": {"max_chunks_per_file": 3}},
            ),
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        row = [f for f in result["files"] if f.get("id") == 1][0]
        assert row["excerpt"] == "the only matched chunk"
        assert row["excerpt_source"] == "chunk"
        assert row["chunk_index"] == 3
        assert row["total_chunks"] == 9
        assert len(row["chunks"]) == 1
        assert row["chunks"][0]["excerpt"] == row["excerpt"]
        assert row["chunks"][0]["chunk_index"] == row["chunk_index"]


class TestSemanticGovernanceStripped:
    """Full-visibility semantic VIEWER results carry no governance fields.

    Semantic already trims full-visibility rows through keep-allowlists
    (``_CHAT_FIELDS`` / ``_FILE_FIELDS``) that exclude the governance set, so no
    source change is needed there. This regression guard ties that guarantee to
    the shared ``GOVERNANCE_FIELDS`` constant so the keyword and semantic paths
    stay in lockstep.
    """

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.commit()

    def _mock_vs_module(self, *, files=None, chats=None):
        mock_store = MagicMock()
        mock_store.search_files.return_value = files or []
        mock_store.search_chats.return_value = chats or []
        mock_module = MagicMock()
        mock_module.VectorStore.get_instance.return_value = mock_store
        return mock_module

    def test_vector_file_result_excludes_governance(self, service_db):
        from footprinter.services.access_service import GOVERNANCE_FIELDS

        mock_module = self._mock_vs_module(
            files=[
                {
                    "file_id": 1,
                    "distance": 0.2,
                    "content_snippet": "matched chunk",
                    "content_length": 100,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            ]
        )
        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="files"
            )
        match = [f for f in result["files"] if f.get("id") == 1][0]
        for field in GOVERNANCE_FIELDS:
            assert field not in match

    def test_vector_chat_result_excludes_governance(self, service_db):
        from footprinter.services.access_service import GOVERNANCE_FIELDS

        mock_module = self._mock_vs_module(
            chats=[
                {
                    "chat_id": 1,
                    "chat_title": "Visible Chat",
                    "snippet": "matched window",
                    "content_length": 100,
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "relevance_score": 0.9,
                    "source": "claude",
                    "created_at": "",
                    "message_id": 11,
                }
            ]
        )
        with patch.dict("sys.modules", {"footprinter.semantic.vector_store": mock_module}):
            result = semantic_service.semantic_search(
                service_db, "conversation", role=Role.VIEWER, source="chats"
            )
        match = [c for c in result["chats"] if c.get("chat_id") == 1][0]
        for field in GOVERNANCE_FIELDS:
            assert field not in match

    def test_fts5_fallback_file_result_excludes_governance(self, service_db):
        from footprinter.services.access_service import GOVERNANCE_FIELDS

        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db, "readme", role=Role.VIEWER, source="files"
        )
        matches = [f for f in result.get("files", []) if f.get("name") == "readme.md"]
        assert matches
        for field in GOVERNANCE_FIELDS:
            assert field not in matches[0]


class TestSemanticServiceD2Access:
    """D2: semantic matches are content-derived — visible items need read access."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def test_visible_deny_excluded(self, service_db):
        """Visible item with access='deny' excluded from semantic results."""
        # Create a visible+deny chat
        service_db.execute(
            """INSERT INTO chats (id, external_id, account, title, message_count,
                                  visibility, access)
               VALUES (10, 'conv-deny', 'claude', 'Denied Chat', 1, 'full', 'deny')"""
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
    """include_unlisted/include_removed are ADMIN-only.

    Threading reaches enrich_chat_visibility/enrich_file_metadata and the
    FTS5 fallbacks. VIEWER ignores the flags.
    """

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.commit()

    def _seed_unlisted_chat(self, conn) -> int:
        cur = conn.execute(
            """INSERT INTO chats (external_id, account, title, message_count,
                                  status, visibility, access)
               VALUES ('conv-arch', 'claude', 'Archived Chat',
                       1,
                       'unlisted', 'full', 'allow')"""
        )
        conn.commit()
        return cur.lastrowid

    def _seed_removed_file(self, conn) -> int:
        cur = conn.execute(
            """INSERT INTO files (name, path, source, status, status_reason,
                                  content_type, size_bytes, visibility, access,
                                  content_preview)
               VALUES ('archived.md', '/Users/u/Work/alpha/archived.md', 'local',
                       'removed', 'deleted_by_user', 'markdown', 50,
                       'full', 'allow', 'archived content')"""
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
