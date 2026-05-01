"""
Parser for ChatGPT chat export format.

ChatGPT exports chats as conversations.json with structure:
{
  "title": "Chat Title",
  "create_time": 1764563881.923587,  # Unix timestamp
  "update_time": 1764564263.347883,
  "conversation_id": "uuid",
  "mapping": {
    "node-uuid": {
      "parent": "parent-uuid" or None,
      "children": ["child-uuid", ...],
      "message": {
        "author": {"role": "user"|"assistant"|"system"},
        "content": {"content_type": "text", "parts": ["message text"]},
        "create_time": 1764563881.0
      }
    }
  }
}

Messages form a tree structure - we walk from root to extract in order.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List

from footprinter.utils.time import UTC_FMT

logger = logging.getLogger(__name__)


class ChatGPTParser:
    """Parser for ChatGPT chat export files."""

    def __init__(self, export_file: Path):
        """
        Initialize parser with export file path.

        Args:
            export_file: Path to ChatGPT conversations.json
        """
        self.export_file = Path(export_file)

        if not self.export_file.exists():
            raise FileNotFoundError(f"conversations.json not found at {export_file}")

    def parse_chats(self) -> Generator[Dict, None, None]:
        """
        Parse chats from conversations.json and yield chat records.

        Yields:
            Dict with chat data including messages
        """
        logger.info(f"Parsing ChatGPT export from {self.export_file}")

        with open(self.export_file, "r", encoding="utf-8") as f:
            chats_data = json.load(f)

        logger.info(f"Found {len(chats_data)} chats")

        for conv in chats_data:
            messages = self._extract_messages(conv.get("mapping", {}))

            # Convert timestamps
            create_time = conv.get("create_time")
            update_time = conv.get("update_time")
            created_at = datetime.fromtimestamp(create_time, tz=timezone.utc).strftime(UTC_FMT) if create_time else None
            updated_at = datetime.fromtimestamp(update_time, tz=timezone.utc).strftime(UTC_FMT) if update_time else None

            yield {
                "external_id": conv.get("conversation_id")  # ChatGPT export format
                or conv.get("id")
                or str(uuid.uuid4()),
                "source": "chatgpt",
                "title": conv.get("title", ""),
                "summary": "",  # ChatGPT doesn't provide summaries
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": len(messages),
                "messages": messages,
                "metadata": {
                    "model": conv.get("default_model_slug"),
                    "gizmo_id": conv.get("gizmo_id"),
                    "gizmo_type": conv.get("gizmo_type"),
                    "is_archived": conv.get("is_archived"),
                },
            }

    def _extract_messages(self, mapping: Dict) -> List[Dict]:
        """
        Extract messages from the mapping tree structure.

        Uses iterative BFS to avoid recursion depth issues with long chats.

        Args:
            mapping: The mapping dict from the chat

        Returns:
            List of message dicts in chronological order
        """
        if not mapping:
            return []

        # Find root node (parent is None)
        root_id = None
        for node_id, node_data in mapping.items():
            if node_data.get("parent") is None:
                root_id = node_id
                break

        if not root_id:
            return []

        # Walk tree iteratively using a stack (DFS)
        messages = []
        visited = set()
        stack = [root_id]

        while stack:
            node_id = stack.pop(0)  # BFS - pop from front for chronological order

            if not node_id or node_id in visited:
                continue

            visited.add(node_id)
            node = mapping.get(node_id, {})
            msg = node.get("message")

            if msg:
                author = msg.get("author", {}).get("role", "unknown")

                # Only include user and assistant messages
                if author in ["user", "assistant"]:
                    content = self._extract_content(msg.get("content", {}))
                    create_time = msg.get("create_time")
                    created_at = (
                        datetime.fromtimestamp(create_time, tz=timezone.utc).strftime(UTC_FMT) if create_time else None
                    )

                    # Skip empty messages
                    if content.strip():
                        messages.append(
                            {
                                "message_id": msg.get("id") or str(uuid.uuid4()),
                                "role": author,
                                "content": content,
                                "created_at": created_at,
                                "metadata": {
                                    "model_slug": msg.get("metadata", {}).get("model_slug"),
                                },
                            }
                        )

            # Add children to stack (in reverse for correct order when popping)
            children = node.get("children", [])
            stack = children + stack  # Add to front for DFS-like traversal

        return messages

    def _extract_content(self, content: Dict) -> str:
        """
        Extract text content from message content structure.

        Args:
            content: Content dict with content_type and parts

        Returns:
            Extracted text string
        """
        if not isinstance(content, dict):
            return str(content) if content else ""

        parts = content.get("parts", [])
        if not parts:
            return ""

        # Parts is typically a list of strings or dicts
        text_parts = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                # Some parts are dicts (like images, code blocks)
                if "text" in part:
                    text_parts.append(part["text"])

        return "\n".join(text_parts)

    def get_stats(self) -> Dict:
        """
        Get statistics about the export without full parsing.

        Returns:
            Dict with chat count, message count, date range
        """
        with open(self.export_file, "r", encoding="utf-8") as f:
            chats_data = json.load(f)

        total_messages = 0
        dates = []

        for conv in chats_data:
            # Count user/assistant messages in mapping
            mapping = conv.get("mapping", {})
            for node_data in mapping.values():
                msg = node_data.get("message")
                if msg and msg.get("author", {}).get("role") in ["user", "assistant"]:
                    total_messages += 1

            create_time = conv.get("create_time")
            if create_time:
                dates.append(datetime.fromtimestamp(create_time, tz=timezone.utc))

        earliest = min(dates).strftime(UTC_FMT) if dates else None
        latest = max(dates).strftime(UTC_FMT) if dates else None

        return {
            "total_chats": len(chats_data),
            "chats_with_messages": sum(1 for conv in chats_data if conv.get("mapping")),
            "total_messages": total_messages,
            "earliest_chat": earliest,
            "latest_chat": latest,
        }
