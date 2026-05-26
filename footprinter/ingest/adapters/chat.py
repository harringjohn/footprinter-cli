"""Chat history adapter.

Wraps ChatIndexer for manual imports and scans ~/.claude/projects/
for Claude Code session JSONL files.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Any, Dict, List

from footprinter.db import chats as chats_db
from footprinter.ingest.adapters.ingest import ingest_entries
from footprinter.ingest.adapters.protocol import ErrorType, PipeContext, PipeResult
from footprinter.ingest.chat_indexer import ChatIndexer
from footprinter.ingest.chat_parsers.claude_code_parser import ClaudeCodeParser

logger = logging.getLogger(__name__)

CLAUDE_CODE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


class ChatAdapter:
    """Adapter wrapping ChatIndexer for the chat stage."""

    name = "chat"
    pipe_name = "chat"
    required_extras: List[str] = []

    def run(self, db: Any, ctx: PipeContext) -> PipeResult:
        try:
            scan_result = self._scan_claude_code(db, ctx)

            manager = ChatIndexer(db)
            stats = manager.get_stats()
            scan_result.data["current_chats"] = stats.get("total_chats", 0)
            scan_result.data["current_messages"] = stats.get("total_messages", 0)
            scan_result.data["by_account"] = stats.get("by_account", {})

            return scan_result
        except Exception as e:
            logger.error("chat stage failed: %s", e)
            return PipeResult.make_error(
                "chat",
                error=str(e),
                error_type=ErrorType.RUNTIME,
            )

    def _scan_claude_code(self, db: Any, ctx: PipeContext) -> PipeResult:
        scan_dirs = self._resolve_scan_dirs(ctx)
        if not scan_dirs:
            return PipeResult.completed("chat", sessions_indexed=0, skipped=0, errors=0)

        all_sessions = []
        for scan_dir in scan_dirs:
            parser = ClaudeCodeParser(scan_dir)
            all_sessions.extend(parser.parse_sessions())

        if not all_sessions:
            return PipeResult.completed("chat", sessions_indexed=0, skipped=0, errors=0)

        insert_fn = partial(self._insert_session, db)
        return ingest_entries(
            "chat",
            all_sessions,
            insert_fn,
            count_label="sessions_indexed",
            conn=db.conn,
            batch_size=50,
            on_progress=ctx.on_progress,
        )

    def _insert_session(self, db: Any, session: Dict) -> Any:
        existing_id = chats_db.get_chat_id_by_uuid(db.conn, session["external_id"])
        if existing_id:
            existing = db.conn.execute(
                "SELECT message_count FROM chats WHERE id = ?", (existing_id,)
            ).fetchone()
            if existing and existing["message_count"] == session["message_count"]:
                return False

            chats_db.delete_chat_messages(db.conn, existing_id)

        chats_db.insert_chat(db.conn, {
            "external_id": session["external_id"],
            "account": session["source"],
            "title": session["title"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "message_count": session["message_count"],
            "metadata": session.get("metadata", {}),
        })

        internal_id = chats_db.get_chat_id_by_uuid(db.conn, session["external_id"])
        for msg in session["messages"]:
            chats_db.insert_message(db.conn, {
                "chat_id": internal_id,
                "message_id": msg["message_id"],
                "role": msg["role"],
                "content": msg["content"],
                "created_at": msg["created_at"],
                "metadata": msg.get("metadata", {}),
            })

        return True

    def _resolve_scan_dirs(self, ctx: PipeContext) -> List[Path]:
        if ctx.scan_roots:
            return [Path(r) for r in ctx.scan_roots if Path(r).is_dir()]
        if CLAUDE_CODE_PROJECTS_DIR.is_dir():
            return [CLAUDE_CODE_PROJECTS_DIR]
        return []

    def status(self, db: Any) -> Dict[str, Any]:
        """Return chat and message counts."""
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats")
        chats = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages")
        messages = cursor.fetchone()[0]
        return {"chats": chats, "messages": messages}
