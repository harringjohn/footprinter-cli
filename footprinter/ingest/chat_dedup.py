"""Chat dedup detection and merge.

Orchestrates near-duplicate chat detection via db.chats and merges
duplicates by combining unique messages from source into target,
marking the source as status='merged', and updating vector embeddings.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from footprinter.db import chats as chats_db

logger = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    """A group of chats detected as potential duplicates."""

    reason: str  # 'exact_title', 'fuzzy_title', 'message_overlap'
    confidence: str  # 'high', 'medium'
    chats: List[Dict]  # list of chat dicts
    detail: str = ""  # human-readable explanation


class ChatDedup:
    """Duplicate detection and merge for chats."""

    def __init__(self, db):
        self.db = db
        self._hash_cache: Dict[int, List[str]] = {}

    def _get_hashes(self, chat_id: int) -> List[str]:
        """Get message content hashes, with caching."""
        if chat_id not in self._hash_cache:
            self._hash_cache[chat_id] = chats_db.get_chat_message_hashes(self.db.conn, chat_id)
        return self._hash_cache[chat_id]

    def detect_duplicates(self) -> List[DuplicateGroup]:
        """Detect potential duplicate chats.

        Delegates to ``footprinter.db.chats.detect_duplicates`` and
        converts plain dicts back to ``DuplicateGroup`` dataclasses.
        """
        from footprinter.db.chats import detect_duplicates as _detect

        raw_groups = _detect(self.db.conn)
        return [
            DuplicateGroup(
                reason=g["reason"],
                confidence=g["confidence"],
                chats=g["chats"],
                detail=g["detail"],
            )
            for g in raw_groups
        ]

    def merge(
        self,
        target_id: int,
        source_id: int,
        vector_store: Optional[Any] = None,
    ) -> Dict:
        """Merge source chat into target.

        1. Validate both exist and aren't already merged
        2. Hash target's messages
        3. Identify unique messages in source
        4. Move unique messages to target
        5. Recount target's message_count
        6. Mark source as merged
        7. Update vectors if vector_store provided

        Returns dict with merge stats.
        """
        if target_id == source_id:
            raise ValueError("Cannot merge a chat into itself")

        target = chats_db.get_chat_by_id(self.db.conn, target_id)
        source = chats_db.get_chat_by_id(self.db.conn, source_id)

        if not target:
            raise ValueError(f"Target chat {target_id} not found")
        if not source:
            raise ValueError(f"Source chat {source_id} not found")
        if target.get("status") == "merged":
            raise ValueError(f"Target chat {target_id} is already merged")
        if source.get("status") == "merged":
            raise ValueError(f"Source chat {source_id} is already merged")

        # Hash target's messages to identify what's already there
        target_hashes = set(self._get_hashes(target_id))

        # Find unique messages in source (not already in target)
        source_messages = chats_db.get_chat_messages(self.db.conn, source_id)
        unique_message_ids = []
        duplicate_count = 0
        for msg in source_messages:
            content = msg["content"] or ""
            msg_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if msg_hash not in target_hashes:
                unique_message_ids.append(msg["id"])
            else:
                duplicate_count += 1

        # Move unique messages to target
        moved = 0
        if unique_message_ids:
            moved = chats_db.move_messages_to_chat(self.db.conn, source_id, target_id, unique_message_ids)

        # Recount target's messages
        new_count = chats_db.update_chat_message_count(self.db.conn, target_id)

        # Mark source as merged
        chats_db.mark_chat_merged(self.db.conn, source_id, target_id)

        # Commit the entire merge atomically (move + recount + mark)
        self.db.conn.commit()

        # Invalidate hash cache
        self._hash_cache.pop(target_id, None)
        self._hash_cache.pop(source_id, None)

        # Update vectors if store provided
        vectors_updated = False
        if vector_store:
            try:
                # Delete source chat vectors
                vector_store.delete_by_metadata({"chat_id": source_id})
                # Re-index moved messages under target
                # (Caller is responsible for full re-vectorization)
                vectors_updated = True
            except Exception as e:
                logger.warning("Vector update failed (non-fatal): %s", e)

        result = {
            "target_id": target_id,
            "source_id": source_id,
            "target_title": target.get("title"),
            "source_title": source.get("title"),
            "messages_moved": moved,
            "duplicates_skipped": duplicate_count,
            "new_message_count": new_count,
            "vectors_updated": vectors_updated,
        }

        logger.info(
            "Merged chat %d into %d: %d messages moved, %d duplicates skipped",
            source_id,
            target_id,
            moved,
            duplicate_count,
        )
        return result
