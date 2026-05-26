"""Parser for Claude Code session JSONL files.

Claude Code stores session transcripts as JSONL in
~/.claude/projects/{encoded-path}/{session-uuid}.jsonl.
Each line is a JSON object with a ``type`` field: ``user``, ``assistant``,
``ai-title``, ``system``, ``attachment``, ``permission-mode``, etc.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


class ClaudeCodeParser:
    """Parser for Claude Code JSONL session files."""

    def __init__(self, source_dir: Path):
        self.source_dir = Path(source_dir)

    def parse_sessions(self) -> Generator[Dict, None, None]:
        """Yield chat dicts for each session with at least one user/assistant message."""
        for jsonl_path in self._find_session_files():
            try:
                chat = self._parse_session(jsonl_path)
                if chat is not None:
                    yield chat
            except Exception as e:
                logger.warning("Skipping %s: %s", jsonl_path.name, e)

    def get_stats(self) -> Dict[str, Any]:
        total_sessions = 0
        total_messages = 0
        timestamps: list[str] = []

        for jsonl_path in self._find_session_files():
            try:
                entries = self._read_entries(jsonl_path)
                msg_entries = [e for e in entries if e.get("type") in ("user", "assistant")]
                if not msg_entries:
                    continue
                total_sessions += 1
                total_messages += len(msg_entries)
                ts = [e["timestamp"] for e in msg_entries if e.get("timestamp")]
                timestamps.extend(ts)
            except Exception:
                continue

        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "earliest_session": min(timestamps) if timestamps else None,
            "latest_session": max(timestamps) if timestamps else None,
        }

    def _find_session_files(self) -> List[Path]:
        return sorted(self.source_dir.rglob("*.jsonl"))

    def _read_entries(self, jsonl_path: Path) -> List[Dict]:
        entries = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def _parse_session(self, jsonl_path: Path) -> Optional[Dict]:
        entries = self._read_entries(jsonl_path)
        session_id = jsonl_path.stem

        title = self._extract_title(entries)
        msg_entries = [e for e in entries if e.get("type") in ("user", "assistant")]
        if not msg_entries:
            return None

        messages = [self._parse_message(e) for e in msg_entries]
        timestamps = [e["timestamp"] for e in msg_entries if e.get("timestamp")]
        metadata = self._extract_session_metadata(msg_entries)

        return {
            "external_id": session_id,
            "source": "claude_code",
            "title": title,
            "summary": "",
            "created_at": min(timestamps) if timestamps else None,
            "updated_at": max(timestamps) if timestamps else None,
            "message_count": len(messages),
            "messages": messages,
            "metadata": metadata,
        }

    def _extract_title(self, entries: List[Dict]) -> str:
        for entry in entries:
            if entry.get("type") == "ai-title":
                title = entry.get("aiTitle") or entry.get("title")
                if title:
                    return title
        return ""

    def _parse_message(self, entry: Dict) -> Dict:
        msg = entry.get("message", {})
        role = msg.get("role", entry.get("type", "unknown"))
        content = self._extract_content(msg)
        metadata: Dict[str, Any] = {}

        if role == "assistant":
            if msg.get("model"):
                metadata["model"] = msg["model"]
            if msg.get("usage"):
                metadata["usage"] = msg["usage"]
            tool_names = self._extract_tool_names(msg)
            if tool_names:
                metadata["tool_names"] = tool_names

        return {
            "message_id": entry.get("uuid"),
            "role": role,
            "content": content,
            "created_at": entry.get("timestamp"),
            "metadata": metadata,
        }

    def _extract_content(self, message: Dict) -> str:
        content = message.get("content")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        parts.append(text)
            return "\n".join(parts)
        return str(content)

    def _extract_tool_names(self, message: Dict) -> List[str]:
        content = message.get("content")
        if not isinstance(content, list):
            return []
        names = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if name and name not in names:
                    names.append(name)
        return names

    def _extract_session_metadata(self, msg_entries: List[Dict]) -> Dict[str, Any]:
        first = msg_entries[0] if msg_entries else {}
        return {
            "project_dir": first.get("cwd", ""),
            "entrypoint": first.get("entrypoint", ""),
            "version": first.get("version", ""),
            "cwd": first.get("cwd", ""),
            "git_branch": first.get("gitBranch", ""),
        }
