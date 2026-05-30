"""Tests for fp update vectorize functionality.

Tests single-record vectorize toggle, review, and import subcommands.
"""

import argparse
import json
import sqlite3
from unittest.mock import patch


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
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, vectorize) "
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

    def _run_update(self, db_path, noun, entity_id, vectorize_val):
        from footprinter.cli.update import _handle_data_single

        args = argparse.Namespace(
            noun=noun, id=entity_id, vectorize=vectorize_val,
            project_id=None, client_id=None, status=None, json=False,
        )
        with patch("footprinter.cli.update.open_db") as mock_open:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            mock_open.return_value.__enter__ = lambda s: conn
            mock_open.return_value.__exit__ = lambda s, *a: conn.close()
            try:
                _handle_data_single(args)
            except SystemExit:
                pass

    def test_update_file_vectorize_false(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        self._run_update(db_path, "file", 1, "false")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_update_file_vectorize_true(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        self._run_update(db_path, "file", 2, "true")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 2").fetchone()
        conn.close()
        assert row["vectorize"] == 1

    def test_update_message_vectorize(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        self._run_update(db_path, "message", 1, "false")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM messages WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_update_chat_vectorize(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        self._run_update(db_path, "chat", 1, "false")

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM chats WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_update_skips_removed_records(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        self._run_update(db_path, "file", 3, "false")

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

        args = argparse.Namespace(entity=None)
        with patch("footprinter.cli.update.open_db") as mock_open:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            mock_open.return_value.__enter__ = lambda s: conn
            mock_open.return_value.__exit__ = lambda s, *a: conn.close()
            with patch("footprinter.cli.update.console") as mock_console:
                _handle_review(args)
                mock_console.print.assert_called_once()

    def test_review_filters_by_entity(self, tmp_path):
        db_path = _make_test_db(tmp_path)
        from footprinter.cli.update import _handle_review

        args = argparse.Namespace(entity="messages")
        with patch("footprinter.cli.update.open_db") as mock_open:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            mock_open.return_value.__enter__ = lambda s: conn
            mock_open.return_value.__exit__ = lambda s, *a: conn.close()
            with patch("footprinter.cli.update.console") as mock_console:
                _handle_review(args)
                mock_console.print.assert_called_once()


class TestUpdateImport:
    """fp update import applies vectorize flags from JSON."""

    def _run_import(self, db_path, import_file):
        from footprinter.cli.update import _handle_import

        args = argparse.Namespace(path=str(import_file))
        with patch("footprinter.cli.update.open_db") as mock_open:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            mock_open.return_value.__enter__ = lambda s: conn
            mock_open.return_value.__exit__ = lambda s, *a: conn.close()
            _handle_import(args)

    def test_import_structured_format(self, tmp_path):
        db_path = _make_test_db(tmp_path)

        import_file = tmp_path / "flags.json"
        import_file.write_text(json.dumps({"entity": "files", "action": "exclude", "ids": [1]}))

        self._run_import(db_path, import_file)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_import_flat_list(self, tmp_path):
        db_path = _make_test_db(tmp_path)

        import_file = tmp_path / "flags.json"
        import_file.write_text(json.dumps([1]))

        self._run_import(db_path, import_file)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 1").fetchone()
        conn.close()
        assert row["vectorize"] == 0

    def test_import_include_action(self, tmp_path):
        db_path = _make_test_db(tmp_path)

        import_file = tmp_path / "flags.json"
        import_file.write_text(json.dumps({"entity": "files", "action": "include", "ids": [2]}))

        self._run_import(db_path, import_file)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT vectorize FROM files WHERE id = 2").fetchone()
        conn.close()
        assert row["vectorize"] == 1
