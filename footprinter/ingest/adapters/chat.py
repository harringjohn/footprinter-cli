"""Chat history adapter.

Wraps ChatIndexer to conform to PipeAdapter protocol.
Chat imports are manual — this adapter provides read-only status.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from footprinter.ingest.adapters.protocol import ErrorType, PipeContext, PipeResult
from footprinter.ingest.chat_indexer import ChatIndexer

logger = logging.getLogger(__name__)


class ChatAdapter:
    """Adapter wrapping ChatIndexer for the chat stage."""

    name = "chat"
    pipe_name = "chat"
    required_extras: List[str] = []

    def run(self, db: Any, ctx: PipeContext) -> PipeResult:
        """Report chat history stats (read-only).

        Chat imports are manual via the chat_indexer CLI, so this
        just reports current counts.
        """
        try:
            manager = ChatIndexer(db)
            stats = manager.get_stats()

            return PipeResult.info(
                "chat",
                note="Chat imports are manual - run chat_indexer import-claude or import-chatgpt",
                current_chats=stats.get("total_chats", 0),
                current_messages=stats.get("total_messages", 0),
                by_account=stats.get("by_account", {}),
            )
        except Exception as e:
            logger.error(f"chat stage failed: {e}")
            return PipeResult.make_error(
                "chat",
                error=str(e),
                error_type=ErrorType.RUNTIME,
            )

    def status(self, db: Any) -> Dict[str, Any]:
        """Return chat and message counts."""
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats")
        chats = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages")
        messages = cursor.fetchone()[0]
        return {"chats": chats, "messages": messages}
