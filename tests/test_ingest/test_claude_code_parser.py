"""Tests for ClaudeCodeParser — JSONL session file parsing."""

import json
from pathlib import Path

import pytest

from footprinter.ingest.chat_parsers.claude_code_parser import ClaudeCodeParser


# ---------------------------------------------------------------------------
# Fixtures — minimal JSONL matching real Claude Code session structure
# ---------------------------------------------------------------------------

def _ts(minute: int) -> str:
    return f"2026-05-20T10:{minute:02d}:00.000Z"


def _make_user_entry(session_id: str, uuid: str, content: str, minute: int, **extra):
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": session_id,
        "timestamp": _ts(minute),
        "message": {"role": "user", "content": content},
        "cwd": "/home/dev/project",
        "entrypoint": "cli",
        "version": "2.1.150",
        "gitBranch": "main",
        **extra,
    }


def _make_assistant_entry(session_id: str, uuid: str, content_blocks: list, minute: int, **extra):
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": session_id,
        "timestamp": _ts(minute),
        "message": {
            "role": "assistant",
            "content": content_blocks,
            "model": "claude-opus-4-6",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
        "cwd": "/home/dev/project",
        "entrypoint": "cli",
        "version": "2.1.150",
        "gitBranch": "main",
        **extra,
    }


def _make_title_entry(session_id: str, title: str):
    return {"type": "ai-title", "sessionId": session_id, "title": title}


def _make_system_entry(session_id: str):
    return {"type": "system", "sessionId": session_id, "message": {"role": "system"}}


def _write_jsonl(path: Path, entries: list):
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


@pytest.fixture
def single_session_dir(tmp_path):
    """One session with a user message and an assistant reply."""
    sid = "aaaa-bbbb-cccc-dddd"
    entries = [
        _make_system_entry(sid),
        _make_title_entry(sid, "Fix the bug"),
        _make_user_entry(sid, "u1", "Please fix the bug", minute=0),
        _make_assistant_entry(
            sid,
            "a1",
            [{"type": "text", "text": "I'll fix it now."}],
            minute=1,
        ),
    ]
    _write_jsonl(tmp_path / f"{sid}.jsonl", entries)
    return tmp_path


@pytest.fixture
def multipart_session_dir(tmp_path):
    """Session with a multipart assistant message: thinking + text + tool_use + text."""
    sid = "1111-2222-3333-4444"
    entries = [
        _make_title_entry(sid, "Multipart test"),
        _make_user_entry(sid, "u1", "Do something complex", minute=0),
        _make_assistant_entry(
            sid,
            "a1",
            [
                {"type": "thinking", "text": "Let me think about this..."},
                {"type": "text", "text": "First, I'll read the file."},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "foo.py"}},
                {"type": "text", "text": "Done reading."},
            ],
            minute=1,
        ),
    ]
    _write_jsonl(tmp_path / f"{sid}.jsonl", entries)
    return tmp_path


@pytest.fixture
def empty_session_dir(tmp_path):
    """Session with only system/title entries — no user or assistant messages."""
    sid = "dead-beef-0000-0000"
    entries = [
        _make_system_entry(sid),
        _make_title_entry(sid, "Empty"),
        {"type": "permission-mode", "sessionId": sid, "permissionMode": "default"},
    ]
    _write_jsonl(tmp_path / f"{sid}.jsonl", entries)
    return tmp_path


@pytest.fixture
def nested_project_dir(tmp_path):
    """Simulates ~/.claude/projects/ with subdirectories containing JSONL files."""
    proj_a = tmp_path / "-Users-dev-projectA"
    proj_b = tmp_path / "-Users-dev-projectB"
    proj_a.mkdir()
    proj_b.mkdir()

    sid_a = "proj-a-session-0001"
    sid_b = "proj-b-session-0001"

    _write_jsonl(proj_a / f"{sid_a}.jsonl", [
        _make_title_entry(sid_a, "Session A"),
        _make_user_entry(sid_a, "ua1", "Hello from A", minute=0),
        _make_assistant_entry(sid_a, "aa1", [{"type": "text", "text": "Hi A"}], minute=1),
    ])
    _write_jsonl(proj_b / f"{sid_b}.jsonl", [
        _make_title_entry(sid_b, "Session B"),
        _make_user_entry(sid_b, "ub1", "Hello from B", minute=5),
        _make_assistant_entry(sid_b, "ab1", [{"type": "text", "text": "Hi B"}], minute=6),
    ])
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseSingleSession:
    def test_yields_one_chat(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chats = list(parser.parse_sessions())
        assert len(chats) == 1

    def test_external_id_matches_filename(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        assert chat["external_id"] == "aaaa-bbbb-cccc-dddd"

    def test_source_is_claude_code(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        assert chat["source"] == "claude_code"

    def test_title_from_ai_title(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        assert chat["title"] == "Fix the bug"

    def test_timestamps(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        assert chat["created_at"] == _ts(0)
        assert chat["updated_at"] == _ts(1)

    def test_message_count(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        assert chat["message_count"] == 2

    def test_messages_parsed(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        msgs = chat["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Please fix the bug"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "I'll fix it now."


class TestContentExtraction:
    def test_multipart_joins_text_blocks_only(self, multipart_session_dir):
        parser = ClaudeCodeParser(multipart_session_dir)
        chat = next(parser.parse_sessions())
        assistant_msg = chat["messages"][1]
        assert "First, I'll read the file." in assistant_msg["content"]
        assert "Done reading." in assistant_msg["content"]
        assert "Let me think" not in assistant_msg["content"]

    def test_tool_names_in_metadata(self, multipart_session_dir):
        parser = ClaudeCodeParser(multipart_session_dir)
        chat = next(parser.parse_sessions())
        assistant_msg = chat["messages"][1]
        assert "Read" in assistant_msg["metadata"]["tool_names"]

    def test_user_string_content(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        user_msg = chat["messages"][0]
        assert user_msg["content"] == "Please fix the bug"


class TestMetadataFields:
    def test_chat_metadata(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        meta = chat["metadata"]
        assert meta["project_dir"] == "/home/dev/project"
        assert meta["entrypoint"] == "cli"
        assert meta["version"] == "2.1.150"
        assert meta["cwd"] == "/home/dev/project"
        assert meta["git_branch"] == "main"

    def test_assistant_message_metadata(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        chat = next(parser.parse_sessions())
        assistant_msg = chat["messages"][1]
        assert assistant_msg["metadata"]["model"] == "claude-opus-4-6"
        assert "usage" in assistant_msg["metadata"]


class TestEmptySession:
    def test_yields_zero_chats(self, empty_session_dir):
        parser = ClaudeCodeParser(empty_session_dir)
        chats = list(parser.parse_sessions())
        assert len(chats) == 0


class TestGetStats:
    def test_returns_counts_and_range(self, single_session_dir):
        parser = ClaudeCodeParser(single_session_dir)
        stats = parser.get_stats()
        assert stats["total_sessions"] == 1
        assert stats["total_messages"] == 2
        assert stats["earliest_session"] is not None
        assert stats["latest_session"] is not None


class TestWalksSubdirectories:
    def test_finds_sessions_in_nested_dirs(self, nested_project_dir):
        parser = ClaudeCodeParser(nested_project_dir)
        chats = list(parser.parse_sessions())
        assert len(chats) == 2
        ids = {c["external_id"] for c in chats}
        assert "proj-a-session-0001" in ids
        assert "proj-b-session-0001" in ids


class TestExports:
    def test_importable_from_package(self):
        from footprinter.ingest.chat_parsers import ClaudeCodeParser as Exported
        assert Exported is ClaudeCodeParser
