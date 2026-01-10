"""Tests for chat dedup detection and merge."""

from unittest.mock import MagicMock

import pytest

from footprinter.db import chats as chats_db
from footprinter.ingest.chat_dedup import ChatDedup
from footprinter.ingest.database import Database


@pytest.fixture
def dedup_db(tmp_path):
    """Database with full schema for dedup tests."""
    db_path = tmp_path / "test_dedup.db"
    db = Database(str(db_path))
    yield db
    db.close()


def _insert_chat(db, chat_id, account, title, message_count=0, status="active"):
    """Insert a chat directly."""
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO chats (external_id, account, title, message_count, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (chat_id, account, title, message_count, status),
    )
    db.conn.commit()
    return cursor.lastrowid


def _insert_message(db, chat_id, role, content):
    """Insert a message directly."""
    cursor = db.conn.cursor()
    cursor.execute(
        """
        INSERT INTO messages (chat_id, role, content)
        VALUES (?, ?, ?)
        """,
        (chat_id, role, content),
    )
    db.conn.commit()
    return cursor.lastrowid


class TestExactTitleDetection:
    """Exact title duplicate detection."""

    def test_exact_title_match(self, dedup_db):
        """Two chats with the same title should be detected."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "My Chat")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "My Chat")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        assert len(groups) == 1
        assert groups[0].reason == "exact_title"
        assert groups[0].confidence == "high"
        ids = {c["id"] for c in groups[0].chats}
        assert ids == {id1, id2}

    def test_case_insensitive_match(self, dedup_db):
        """Title matching should be case-insensitive."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "My Chat")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "my chat")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        assert len(groups) == 1
        assert groups[0].reason == "exact_title"

    def test_no_false_positives(self, dedup_db):
        """Different titles should not match."""
        _insert_chat(dedup_db, "uuid-1", "claude", "Chat About Python")
        _insert_chat(dedup_db, "uuid-2", "claude", "Chat About Rust")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        # Filter to exact_title only
        exact = [g for g in groups if g.reason == "exact_title"]
        assert len(exact) == 0


class TestFuzzyTitleDetection:
    """Fuzzy title duplicate detection."""

    def test_fuzzy_title_typo(self, dedup_db):
        """Titles with small differences should be detected as fuzzy matches."""
        _insert_chat(dedup_db, "uuid-1", "claude", "Debugging Python memory leak")
        _insert_chat(dedup_db, "uuid-2", "claude", "Debugging Python memory leaks")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        fuzzy = [g for g in groups if g.reason == "fuzzy_title"]
        assert len(fuzzy) == 1
        assert fuzzy[0].confidence == "medium"

    def test_very_different_titles_no_match(self, dedup_db):
        """Very different titles should not fuzzy match."""
        _insert_chat(dedup_db, "uuid-1", "claude", "Setting up Docker containers")
        _insert_chat(dedup_db, "uuid-2", "claude", "Python async programming guide")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        fuzzy = [g for g in groups if g.reason == "fuzzy_title"]
        assert len(fuzzy) == 0


class TestMessageOverlapDetection:
    """Message content overlap detection."""

    def test_message_overlap_detected(self, dedup_db):
        """Chats with >50% message overlap should be detected."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Setting up Docker containers", message_count=3)
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Python async programming guide", message_count=4)

        # Shared messages
        _insert_message(dedup_db, id1, "user", "Hello world")
        _insert_message(dedup_db, id1, "assistant", "Hi there!")
        _insert_message(dedup_db, id1, "user", "unique to conv1")

        _insert_message(dedup_db, id2, "user", "Hello world")
        _insert_message(dedup_db, id2, "assistant", "Hi there!")
        _insert_message(dedup_db, id2, "user", "unique to conv2")
        _insert_message(dedup_db, id2, "assistant", "also unique")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        overlap = [g for g in groups if g.reason == "message_overlap"]
        assert len(overlap) == 1
        assert overlap[0].confidence == "high"

    def test_no_cross_account_overlap(self, dedup_db):
        """Message overlap only checks within the same account."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Setting up Docker containers", message_count=2)
        id2 = _insert_chat(dedup_db, "uuid-2", "chatgpt", "Kubernetes deployment guide", message_count=2)

        # Same messages but different accounts
        _insert_message(dedup_db, id1, "user", "Hello world")
        _insert_message(dedup_db, id1, "assistant", "Hi there!")
        _insert_message(dedup_db, id2, "user", "Hello world")
        _insert_message(dedup_db, id2, "assistant", "Hi there!")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        overlap = [g for g in groups if g.reason == "message_overlap"]
        assert len(overlap) == 0


class TestMerge:
    """Merge operations."""

    def test_merge_moves_unique_messages(self, dedup_db):
        """Merge should move unique messages from source to target."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target", message_count=2)
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source", message_count=3)

        _insert_message(dedup_db, id1, "user", "shared message")
        _insert_message(dedup_db, id1, "assistant", "shared reply")

        _insert_message(dedup_db, id2, "user", "shared message")
        _insert_message(dedup_db, id2, "assistant", "shared reply")
        _insert_message(dedup_db, id2, "user", "unique source message")

        dedup = ChatDedup(dedup_db)
        result = dedup.merge(id1, id2)

        assert result["messages_moved"] == 1
        assert result["duplicates_skipped"] == 2
        assert result["new_message_count"] == 3  # 2 original + 1 moved

    def test_merge_marks_source_merged(self, dedup_db):
        """Source chat should be marked as merged after merge."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source")

        dedup = ChatDedup(dedup_db)
        dedup.merge(id1, id2)

        source = chats_db.get_chat_by_id(dedup_db.conn, id2)
        assert source["status"] == "merged"
        assert source["merged_into_id"] == id1

    def test_merged_excluded_from_listing(self, dedup_db):
        """Merged chats should not appear in list_chats."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source")

        dedup = ChatDedup(dedup_db)
        dedup.merge(id1, id2)

        convs = chats_db.list_chats_simple(dedup_db.conn)
        ids = {c["id"] for c in convs}
        assert id1 in ids
        assert id2 not in ids

    def test_merged_included_when_requested(self, dedup_db):
        """Merged chats appear when status='all'."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source")

        dedup = ChatDedup(dedup_db)
        dedup.merge(id1, id2)

        convs = chats_db.list_chats_simple(dedup_db.conn, status="all")
        ids = {c["id"] for c in convs}
        assert id1 in ids
        assert id2 in ids

    def test_cannot_merge_into_self(self, dedup_db):
        """Merging a chat into itself should raise ValueError."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Chat")

        dedup = ChatDedup(dedup_db)
        with pytest.raises(ValueError, match="Cannot merge a chat into itself"):
            dedup.merge(id1, id1)

    def test_cannot_merge_already_merged(self, dedup_db):
        """Cannot merge an already-merged chat."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source")
        id3 = _insert_chat(dedup_db, "uuid-3", "claude", "Another")

        dedup = ChatDedup(dedup_db)
        dedup.merge(id1, id2)

        with pytest.raises(ValueError, match="already merged"):
            dedup.merge(id3, id2)

    def test_cannot_merge_into_merged_target(self, dedup_db):
        """Cannot merge into a target that is already merged."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "A")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "B")
        id3 = _insert_chat(dedup_db, "uuid-3", "claude", "C")

        dedup = ChatDedup(dedup_db)
        dedup.merge(id1, id2)  # B merged into A

        with pytest.raises(ValueError, match="already merged"):
            dedup.merge(id2, id3)  # Can't merge into B (it's merged)

    def test_merge_nonexistent_raises(self, dedup_db):
        """Merging with nonexistent chat should raise ValueError."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Chat")

        dedup = ChatDedup(dedup_db)
        with pytest.raises(ValueError, match="not found"):
            dedup.merge(id1, 9999)

    def test_vector_store_update(self, dedup_db):
        """Vector store should be updated during merge when provided."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source")

        mock_vs = MagicMock()
        dedup = ChatDedup(dedup_db)
        result = dedup.merge(id1, id2, vector_store=mock_vs)

        assert result["vectors_updated"] is True
        mock_vs.delete_by_metadata.assert_called_once_with({"chat_id": id2})

    def test_vector_store_failure_nonfatal(self, dedup_db):
        """Vector store errors should not prevent merge."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "Target")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "Source")

        mock_vs = MagicMock()
        mock_vs.delete_by_metadata.side_effect = RuntimeError("vector error")

        dedup = ChatDedup(dedup_db)
        result = dedup.merge(id1, id2, vector_store=mock_vs)

        assert result["vectors_updated"] is False
        # Merge still completed
        source = chats_db.get_chat_by_id(dedup_db.conn, id2)
        assert source["status"] == "merged"


class TestDetectionExcludesMerged:
    """Merged chats should not appear in dedup detection."""

    def test_merged_excluded_from_detection(self, dedup_db):
        """Already-merged chats don't show up as duplicates."""
        id1 = _insert_chat(dedup_db, "uuid-1", "claude", "My Chat")
        id2 = _insert_chat(dedup_db, "uuid-2", "claude", "My Chat")
        id3 = _insert_chat(dedup_db, "uuid-3", "claude", "My Chat", status="merged")

        dedup = ChatDedup(dedup_db)
        groups = dedup.detect_duplicates()

        # Only id1 and id2 should be in the group, not id3
        assert len(groups) == 1
        ids = {c["id"] for c in groups[0].chats}
        assert ids == {id1, id2}


class TestModuleCleanup:
    """Verify dead code was removed from chat_dedup module."""

    def test_dead_constants_removed(self):
        """Threshold constants live in db.chats, not in chat_dedup."""
        from footprinter.ingest import chat_dedup

        assert not hasattr(chat_dedup, "FUZZY_THRESHOLD")
        assert not hasattr(chat_dedup, "MESSAGE_OVERLAP_THRESHOLD")

    def test_dead_normalize_title_removed(self):
        """Title normalization lives in db.chats, not in ChatDedup."""
        from footprinter.ingest.chat_dedup import ChatDedup

        assert not hasattr(ChatDedup, "_normalize_title")
