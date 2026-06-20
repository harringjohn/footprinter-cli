"""Regression guard: governance fields never reach a VIEWER.

Two independent mechanisms strip governance metadata from VIEWER-facing
results: a *denylist* (``access_service.strip_governance_fields`` /
``GOVERNANCE_FIELDS``) on the keyword/listing paths, and an *allowlist trim*
(``semantic_service._trim_file_result`` / ``_trim_chat_result`` /
``_chunk_excerpt``) on the semantic path. Both are correct today, but because
they are two mechanisms they can drift apart. This test asserts the invariant
across BOTH paths at once — keyword (incl. FTS5 fallback) and semantic (vector
+ fallback, files + chats, including the nested per-chunk dicts) — keyed off the
canonical ``GOVERNANCE_FIELDS`` set so it stays in sync with the denylist source
of truth rather than a hand-copied list.
"""

from unittest.mock import MagicMock, patch

from footprinter.services import Role, search_service, semantic_service
from footprinter.services.access_service import GOVERNANCE_FIELDS


def _assert_no_governance(rows, *, label):
    """No result dict — nor any nested ``chunks`` entry — carries a governance field."""
    for row in rows:
        leaked = GOVERNANCE_FIELDS & row.keys()
        assert not leaked, f"{label}: governance fields leaked to VIEWER: {sorted(leaked)} in {row}"
        for chunk in row.get("chunks", []):
            leaked_chunk = GOVERNANCE_FIELDS & chunk.keys()
            assert not leaked_chunk, (
                f"{label}: governance fields leaked in a chunk: {sorted(leaked_chunk)} in {chunk}"
            )


class TestGovernanceFieldInvariant:
    """``GOVERNANCE_FIELDS`` must never reach a VIEWER on any content path."""

    def _rebuild_fts(self, conn):
        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO chats_fts(chats_fts) VALUES('rebuild')")
        conn.commit()

    def test_keyword_path_strips_governance(self, service_db):
        """search_service (files + emails + chats + browser) — denylist path."""
        result = search_service.search(service_db, query="", role=Role.VIEWER)
        rows = (
            result["files"] + result["emails"] + result["chats"] + result["browser"]
        )
        # Guard the guard: the fixture seeds visible rows, so results are non-empty —
        # otherwise the assertion below would pass vacuously.
        assert rows, "expected content-bearing VIEWER results to check"
        _assert_no_governance(rows, label="keyword")

    def test_semantic_fts5_fallback_strips_governance(self, service_db):
        """semantic_search FTS5 fallback (files + chats) — allowlist-trim path.

        The autouse ``_isolate_vector_store`` fixture forces
        ``_semantic_available()`` to ``False``, so this exercises the degraded
        FTS5 fallback rather than the vector path.
        """
        self._rebuild_fts(service_db)
        result = semantic_service.semantic_search(
            service_db, "Visible", role=Role.VIEWER, source="all"
        )
        rows = result.get("files", []) + result.get("chats", [])
        assert rows, "expected FTS5 fallback results to check"
        _assert_no_governance(rows, label="semantic-fts5")

    def test_semantic_vector_path_strips_governance(self, service_db):
        """semantic_search vector path — files + chats, including nested ``chunks``."""
        mock_store = MagicMock()
        mock_store.search_files.return_value = [
            {
                "file_id": 1,  # the fixture's visible+allowed file
                "distance": 0.2,
                "content_snippet": "x" * 500,
                "content_length": 1800,
                "chunk_index": 2,
                "total_chunks": 9,
            }
        ]
        mock_store.search_chats.return_value = [
            {
                "chat_id": 1,  # the fixture's visible+allowed chat
                "chat_title": "Visible Chat",
                "snippet": "the matched conversation window",
                "content_length": 1200,
                "chunk_index": 0,
                "total_chunks": 3,
                "relevance_score": 0.9,
                "source": "claude",
                "created_at": "",
                "message_id": 11,
            }
        ]
        mock_module = MagicMock()
        mock_module.VectorStore.get_instance.return_value = mock_store
        with patch.dict(
            "sys.modules", {"footprinter.semantic.vector_store": mock_module}
        ):
            result = semantic_service.semantic_search(
                service_db, "anything", role=Role.VIEWER, source="all"
            )
        rows = result.get("files", []) + result.get("chats", [])
        assert rows, "expected vector results to check"
        _assert_no_governance(rows, label="semantic-vector")
