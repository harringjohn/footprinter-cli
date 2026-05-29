"""Tests for fp update CLI commands.

Tests single-record vectorize toggle, review, and import subcommands.
"""

import argparse
import json
import sqlite3

import pytest


def _make_test_db(tmp_path):
    """Create a DB with files, chats, and messages for update CLI tests."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "test.db"
    db = Database(str(db_path))

    db.conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes) "
        "VALUES (1, 'a.txt', '/tmp/a.txt', 'local', 'listed', 'text', 100)"
    )
    db.conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, vectorize) "
        "VALUES (2, 'b.txt', '/tmp/b.txt', 'local', 'listed', 'text', 200, 0)"
    )
    db.conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, status) "
        "VALUES (3, 'removed.txt', '/tmp/r.txt', 'local', 'removed', 'text', 50, 1)"
    )

    db.conn.execute(
        "INSERT INTO chats (id, external_id, account, title, message_count) "
        "VALUES (1, 'chat-1', 'test', 'Test Chat', 2)"
    )

    db.conn.execute(
        "INSERT INTO messages (id, chat_id, role, content) VALUES (1, 1, 'user', 'Hello')"
    )
    db.conn.execute(
        "INSERT INTO messages (id, chat_id, role, content) VALUES (2, 1, 'assistant', 'Hi there')"
    )

    db.conn.commit()
    db.close()
    return db_path


class TestUpdateVectorize:
    """fp update file <id> --vectorize false should set files.vectorize = 0."""

    def test_update_file_vectorize_false(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_update

        args = argparse.Namespace(entity_table="files", id=1, vectorize="false")
        _handle_update(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_update_file_vectorize_true(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_update

        args = argparse.Namespace(entity_table="files", id=2, vectorize="true")
        _handle_update(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 2").fetchone()
        conn.close()
        assert row["vectorize"] == 1

    def test_update_message_vectorize(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_update

        args = argparse.Namespace(entity_table="messages", id=1, vectorize="false")
        _handle_update(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM messages WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_update_chat_vectorize(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_update

        args = argparse.Namespace(entity_table="chats", id=1, vectorize="false")
        _handle_update(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM chats WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_update_skips_removed_records(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_update

        args = argparse.Namespace(entity_table="files", id=3, vectorize="false")
        _handle_update(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 3").fetchone()
        conn.close()
        assert row["vectorize"] == 1, "removed records should not be updated"


class TestUpdateReview:
    """fp update review shows excluded record counts."""

    def test_review_shows_counts(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_review

        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        out = Console(file=buf, force_terminal=False, width=120)
        args = argparse.Namespace(entity=None)
        _handle_review(args, db_path=db_path, output=out)
        text = buf.getvalue()
        assert "1" in text, "should show 1 excluded file"

    def test_review_filters_by_entity(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_review

        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        out = Console(file=buf, force_terminal=False, width=120)
        args = argparse.Namespace(entity="messages")
        _handle_review(args, db_path=db_path, output=out)
        text = buf.getvalue()
        assert "messages" in text.lower() or "Messages" in text


class TestUpdateImport:
    """fp update import applies vectorize flags from JSON."""

    def test_import_structured_format(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_import

        import_file = tmp_path / "flags.json"
        import_file.write_text(json.dumps({"entity": "files", "action": "exclude", "ids": [1]}))

        args = argparse.Namespace(path=str(import_file))
        _handle_import(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_import_flat_list(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_import

        import_file = tmp_path / "flags.json"
        import_file.write_text(json.dumps([1]))

        args = argparse.Namespace(path=str(import_file))
        _handle_import(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_import_include_action(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_import

        import_file = tmp_path / "flags.json"
        import_file.write_text(json.dumps({"entity": "files", "action": "include", "ids": [2]}))

        args = argparse.Namespace(path=str(import_file))
        _handle_import(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 2").fetchone()
        conn.close()
        assert row["vectorize"] == 1
