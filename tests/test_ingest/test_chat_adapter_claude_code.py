"""Tests for ChatAdapter Claude Code scanning — incremental indexing, scan_roots override."""

import json
from pathlib import Path

import pytest

from footprinter.ingest.adapters.chat import ChatAdapter
from footprinter.ingest.adapters.protocol import PipeContext, PipeResult, PipeStatus
from footprinter.ingest.database import Database


# ---------------------------------------------------------------------------
# Fixture helpers (reused from parser tests)
# ---------------------------------------------------------------------------

def _ts(minute: int) -> str:
    return f"2026-05-20T10:{minute:02d}:00.000Z"


def _write_session(directory: Path, session_id: str, title: str, minute_start: int = 0):
    entries = [
        {"type": "ai-title", "sessionId": session_id, "title": title},
        {
            "type": "user",
            "uuid": f"{session_id}-u1",
            "sessionId": session_id,
            "timestamp": _ts(minute_start),
            "message": {"role": "user", "content": "Hello"},
            "cwd": "/home/dev/project",
            "entrypoint": "cli",
            "version": "2.1.150",
            "gitBranch": "main",
        },
        {
            "type": "assistant",
            "uuid": f"{session_id}-a1",
            "sessionId": session_id,
            "timestamp": _ts(minute_start + 1),
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi there!"}],
                "model": "claude-opus-4-6",
                "usage": {"input_tokens": 50, "output_tokens": 20},
            },
            "cwd": "/home/dev/project",
            "entrypoint": "cli",
            "version": "2.1.150",
            "gitBranch": "main",
        },
    ]
    path = directory / f"{session_id}.jsonl"
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


@pytest.fixture
def claude_code_dir(tmp_path):
    """Simulates ~/.claude/projects/ with one project dir containing two sessions."""
    proj_dir = tmp_path / "-Users-dev-myproject"
    proj_dir.mkdir()
    _write_session(proj_dir, "session-aaa", "First session", minute_start=0)
    _write_session(proj_dir, "session-bbb", "Second session", minute_start=5)
    return tmp_path


@pytest.fixture
def db_instance(temp_db):
    db = Database(temp_db)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanClaudeCodeSessions:
    def test_returns_completed_with_sessions(self, db_instance, claude_code_dir):
        ctx = PipeContext(source_config={}, scan_roots=[str(claude_code_dir)])
        adapter = ChatAdapter()
        result = adapter.run(db_instance, ctx)
        assert isinstance(result, PipeResult)
        assert result.status == PipeStatus.COMPLETED
        assert result.data["sessions_indexed"] >= 2


class TestIncrementalSkip:
    def test_second_run_skips_existing(self, db_instance, claude_code_dir):
        ctx = PipeContext(source_config={}, scan_roots=[str(claude_code_dir)])
        adapter = ChatAdapter()

        result1 = adapter.run(db_instance, ctx)
        assert result1.data["sessions_indexed"] == 2

        result2 = adapter.run(db_instance, ctx)
        assert result2.data.get("skipped", 0) >= 2

        cursor = db_instance.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats WHERE external_id = 'session-aaa'")
        assert cursor.fetchone()[0] == 1


class TestAccountIsClaudeCode:
    def test_account_set_correctly(self, db_instance, claude_code_dir):
        ctx = PipeContext(source_config={}, scan_roots=[str(claude_code_dir)])
        adapter = ChatAdapter()
        adapter.run(db_instance, ctx)

        cursor = db_instance.conn.cursor()
        cursor.execute("SELECT DISTINCT account FROM chats WHERE account = 'claude_code'")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "claude_code"


class TestScanRootsOverride:
    def test_scans_override_dir(self, db_instance, tmp_path):
        custom_dir = tmp_path / "custom-root"
        custom_dir.mkdir()
        _write_session(custom_dir, "custom-session", "Custom session")

        ctx = PipeContext(source_config={}, scan_roots=[str(custom_dir)])
        adapter = ChatAdapter()
        result = adapter.run(db_instance, ctx)

        assert result.status == PipeStatus.COMPLETED
        assert result.data["sessions_indexed"] >= 1

        cursor = db_instance.conn.cursor()
        cursor.execute("SELECT external_id FROM chats WHERE external_id = 'custom-session'")
        assert cursor.fetchone() is not None


class TestExistingManualImportsPreserved:
    def test_preserves_other_accounts(self, db_instance, claude_code_dir):
        from footprinter.db import chats as chats_db

        chats_db.insert_chat(db_instance.conn, {
            "external_id": "claude-desktop-001",
            "account": "claude",
            "title": "Claude Desktop chat",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:01:00Z",
            "message_count": 3,
            "metadata": {},
        })
        chats_db.insert_chat(db_instance.conn, {
            "external_id": "chatgpt-001",
            "account": "chatgpt",
            "title": "ChatGPT chat",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:01:00Z",
            "message_count": 5,
            "metadata": {},
        })
        db_instance.conn.commit()

        ctx = PipeContext(source_config={}, scan_roots=[str(claude_code_dir)])
        adapter = ChatAdapter()
        adapter.run(db_instance, ctx)

        cursor = db_instance.conn.cursor()
        cursor.execute("SELECT account, COUNT(*) FROM chats GROUP BY account ORDER BY account")
        rows = {row[0]: row[1] for row in cursor.fetchall()}
        assert rows.get("claude") == 1
        assert rows.get("chatgpt") == 1
        assert rows.get("claude_code") == 2
