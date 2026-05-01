"""
Parser for Claude chat export format.

Claude exports chats as a single conversations.json file with structure:
{
  "uuid": "chat-id",
  "name": "Chat Title",
  "summary": "AI-generated summary",
  "created_at": "2025-11-22T12:59:49.779155Z",
  "updated_at": "2025-11-22T13:00:01.934675Z",
  "chat_messages": [
    {
      "uuid": "message-id",
      "text": "Full message text",
      "content": [{"start_timestamp": "...", "type": "text", "text": "..."}]
    }
  ]
}

Messages alternate user/assistant - role inferred from position (even=user, odd=assistant).
"""

import json
import logging
from pathlib import Path
from typing import Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


class ClaudeParser:
    """Parser for Claude chat export files."""

    def __init__(self, export_dir: Path):
        """
        Initialize parser with export directory path.

        Args:
            export_dir: Path to Claude export directory containing conversations.json
        """
        self.export_dir = Path(export_dir)
        self.chats_file = self.export_dir / "conversations.json"

        if not self.chats_file.exists():
            raise FileNotFoundError(f"conversations.json not found in {export_dir}")

    def parse_chats(self) -> Generator[Dict, None, None]:
        """
        Parse chats from conversations.json and yield chat records.

        Yields:
            Dict with chat data including messages
        """
        logger.info(f"Parsing Claude export from {self.export_dir}")

        with open(self.chats_file, "r", encoding="utf-8") as f:
            chats_data = json.load(f)

        logger.info(f"Found {len(chats_data)} chats")

        for conv in chats_data:
            messages = conv.get("chat_messages", [])

            yield {
                "external_id": conv["uuid"],
                "source": "claude",
                "title": conv.get("name", ""),
                "summary": conv.get("summary", ""),
                "created_at": conv.get("created_at"),
                "updated_at": conv.get("updated_at"),
                "message_count": len(messages),
                "messages": self._parse_messages(messages),
                "metadata": {
                    "account_uuid": conv.get("account", {}).get("uuid"),
                },
            }

    def _parse_messages(self, messages: List[Dict]) -> List[Dict]:
        """
        Parse chat messages, inferring role from position.

        Args:
            messages: List of message dicts from export

        Returns:
            List of parsed message dicts with role, content, timestamp
        """
        parsed = []

        for i, msg in enumerate(messages):
            # Role alternates: even index = user, odd index = assistant
            role = "user" if i % 2 == 0 else "assistant"

            # Get timestamp from content array if available
            created_at = None
            if msg.get("content") and len(msg["content"]) > 0:
                created_at = msg["content"][0].get("start_timestamp")

            parsed.append(
                {
                    "message_id": msg.get("uuid"),
                    "role": role,
                    "content": msg.get("text", ""),
                    "created_at": created_at,
                    "metadata": {},
                }
            )

        return parsed

    def get_stats(self) -> Dict:
        """
        Get statistics about the export without full parsing.

        Returns:
            Dict with chat count, message count, date range
        """
        with open(self.chats_file, "r", encoding="utf-8") as f:
            chats_data = json.load(f)

        total_messages = sum(len(conv.get("chat_messages", [])) for conv in chats_data)

        dates = [conv.get("created_at") for conv in chats_data if conv.get("created_at")]
        earliest = min(dates) if dates else None
        latest = max(dates) if dates else None

        return {
            "total_chats": len(chats_data),
            "chats_with_messages": sum(1 for conv in chats_data if conv.get("chat_messages")),
            "total_messages": total_messages,
            "earliest_chat": earliest,
            "latest_chat": latest,
        }

    def parse_memories(self) -> Optional[List[Dict]]:
        """
        Parse memories.json if present.

        Returns:
            List of memory records or None if file doesn't exist
        """
        memories_file = self.export_dir / "memories.json"
        if not memories_file.exists():
            return None

        with open(memories_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def parse_projects(self) -> Optional[List[Dict]]:
        """
        Parse projects.json if present.

        Returns:
            List of project records or None if file doesn't exist
        """
        projects_file = self.export_dir / "projects.json"
        if not projects_file.exists():
            return None

        with open(projects_file, "r", encoding="utf-8") as f:
            return json.load(f)
