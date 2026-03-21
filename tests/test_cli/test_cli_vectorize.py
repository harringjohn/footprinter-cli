"""Tests for fp vectorize CLI commands.

Tests exclude, include, review, and import subcommands.
"""

import argparse
import json
import sqlite3
from io import StringIO

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_db(tmp_path):
    """Create a DB with files, chats, and messages for vectorize CLI tests."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "test.db"
    db = Database(str(db_path))

    # Files with mixed vectorize state
    db.conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes) "
        "VALUES (1, 'a.txt', '/tmp/a.txt', 'local', 'active', 'text', 100)"
    )
    db.conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes) "
        "VALUES (2, 'b.txt', '/tmp/b.txt', 'local', 'active', 'text', 200)"
    )
    db.conn.execute(
        "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, metadata) "
        "VALUES (3, 'c.txt', '/tmp/c.txt', 'local', 'active', 'text', 300, ?)",
        (json.dumps({"vectorize": 0}),),
    )

    # Chats
    db.conn.execute(
        "INSERT INTO chats (id, external_id, account, title, message_count) "
        "VALUES (1, 'chat-1', 'test', 'Test Chat', 2)"
    )

    # Messages
    db.conn.execute("INSERT INTO messages (id, chat_id, role, content) VALUES (1, 1, 'user', 'Hello')")
    db.conn.execute("INSERT INTO messages (id, chat_id, role, content) VALUES (2, 1, 'assistant', 'Hi there')")

    db.conn.commit()
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# RED 5: TestExcludeCommand
# ---------------------------------------------------------------------------


class TestExcludeCommand:
    """fp vectorize exclude files 1 2 3 should set metadata.vectorize=0."""

    def test_exclude_sets_flag(self, tmp_path):
        db_path = _make_test_db(tmp_path)

        from footprinter.cli.vectorize_cmd import _handle_exclude

        args = argparse.Namespace(entity="files", ids=[1, 2])
        _handle_exclude(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        for fid in [1, 2]:
            row = conn.execute(
                "SELECT json_extract(metadata, '$.vectorize') as vec FROM files WHERE id = ?",
                (fid,),
            ).fetchone()
            assert row["vec"] == 0, f"File {fid} should have vectorize=0"
        conn.close()

    def test_exclude_preserves_other_metadata(self, tmp_path):
        """Existing metadata keys should not be lost when setting vectorize flag."""
        db_path = _make_test_db(tmp_path)

        # Pre-set some metadata on file 1
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE files SET metadata = ? WHERE id = 1",
            (json.dumps({"custom_key": "keep_me"}),),
        )
        conn.commit()
        conn.close()

        from footprinter.cli.vectorize_cmd import _handle_exclude

        args = argparse.Namespace(entity="files", ids=[1])
        _handle_exclude(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT metadata FROM files WHERE id = 1").fetchone()
        meta = json.loads(row["metadata"])
        assert meta["vectorize"] == 0
        assert meta["custom_key"] == "keep_me"
        conn.close()

    def test_exclude_messages(self, tmp_path):
        """fp vectorize exclude messages 1 should work on messages table."""
        db_path = _make_test_db(tmp_path)

        from footprinter.cli.vectorize_cmd import _handle_exclude

        args = argparse.Namespace(entity="messages", ids=[1])
        _handle_exclude(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT json_extract(metadata, '$.vectorize') as vec FROM messages WHERE id = 1",
        ).fetchone()
        assert row["vec"] == 0
        conn.close()

    def test_exclude_skips_removed_records(self, tmp_path):
        """Exclude should not modify removed records."""
        db_path = _make_test_db(tmp_path)

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO files (id, name, path, source, status, content_type, size_bytes) "
            "VALUES (20, 'gone.txt', '/tmp/gone.txt', 'local', 'removed', 'text', 10)"
        )
        conn.commit()
        conn.close()

        from footprinter.cli.vectorize_cmd import _handle_exclude

        out = __import__("io").StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)
        args = argparse.Namespace(entity="files", ids=[20])
        _handle_exclude(args, db_path=db_path, output=console)

        assert "Excluded 0" in out.getvalue()

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT metadata FROM files WHERE id = 20").fetchone()
        assert row["metadata"] is None
        conn.close()


# ---------------------------------------------------------------------------
# RED 6: TestIncludeCommand
# ---------------------------------------------------------------------------


class TestIncludeCommand:
    """fp vectorize include should set metadata.vectorize=1."""

    def test_include_restores_flag(self, tmp_path):
        db_path = _make_test_db(tmp_path)

        from footprinter.cli.vectorize_cmd import _handle_include

        # File 3 was created with vectorize=0
        args = argparse.Namespace(entity="files", ids=[3])
        _handle_include(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT json_extract(metadata, '$.vectorize') as vec FROM files WHERE id = 3",
        ).fetchone()
        assert row["vec"] == 1
        conn.close()

    def test_include_on_already_included(self, tmp_path):
        """Including an already-included record should be a no-op."""
        db_path = _make_test_db(tmp_path)

        from footprinter.cli.vectorize_cmd import _handle_include

        args = argparse.Namespace(entity="files", ids=[1])
        _handle_include(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT json_extract(metadata, '$.vectorize') as vec FROM files WHERE id = 1",
        ).fetchone()
        assert row["vec"] == 1
        conn.close()


# ---------------------------------------------------------------------------
# RED 7: TestReviewCommand
# ---------------------------------------------------------------------------


class TestReviewCommand:
    """fp vectorize review should show counts of excluded records."""

    def test_review_shows_counts(self, tmp_path):
        db_path = _make_test_db(tmp_path)

        from footprinter.cli.vectorize_cmd import _handle_review

        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        args = argparse.Namespace(entity=None)
        _handle_review(args, db_path=db_path, output=console)

        output = out.getvalue()
        # Should show that file 3 is excluded
        assert "1" in output  # 1 excluded file
        # Should mention files
        assert "files" in output.lower() or "file" in output.lower()

    def test_review_entity_filter(self, tmp_path):
        """fp vectorize review files should filter to files only."""
        db_path = _make_test_db(tmp_path)

        from footprinter.cli.vectorize_cmd import _handle_review

        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out)

        args = argparse.Namespace(entity="files")
        _handle_review(args, db_path=db_path, output=console)

        output = out.getvalue()
        assert "files" in output.lower() or "file" in output.lower()

    def test_review_excludes_removed_records(self, tmp_path):
        """Removed records should not appear in excluded or total counts."""
        db_path = _make_test_db(tmp_path)

        # Add a removed file (should not count toward total)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO files (id, name, path, source, status, content_type, size_bytes) "
            "VALUES (10, 'removed.txt', '/tmp/removed.txt', 'local', 'removed', 'text', 50)"
        )
        # Add a removed file with vectorize=0 (should not count toward excluded)
        conn.execute(
            "INSERT INTO files (id, name, path, source, status, content_type, size_bytes, metadata) "
            "VALUES (11, 'removed_excl.txt', '/tmp/removed_excl.txt', 'local', 'removed', 'text', 60, ?)",
            (json.dumps({"vectorize": 0}),),
        )
        # Add a removed message (should not count toward total)
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, status) VALUES (10, 1, 'user', 'removed msg', 'removed')"
        )
        # Add a removed chat (should not count toward total)
        conn.execute(
            "INSERT INTO chats (id, external_id, account, title, message_count, status) "
            "VALUES (10, 'chat-rm', 'test', 'Removed Chat', 0, 'removed')"
        )
        conn.commit()
        conn.close()

        from footprinter.cli.vectorize_cmd import _handle_review

        out = StringIO()
        console = __import__("rich.console", fromlist=["Console"]).Console(file=out, width=120)

        args = argparse.Namespace(entity=None)
        _handle_review(args, db_path=db_path, output=console)

        output = out.getvalue()
        # Parse the Rich table output to extract counts per entity.
        # The table has columns: Entity | Excluded | Total
        # files: 1 excluded (id=3, active, vectorize=0), 3 total (ids 1,2,3 active)
        # messages: 0 excluded, 2 total (ids 1,2 active)
        # chats: 0 excluded, 1 total (id=1 active)
        # Removed records (files 10,11; message 10; chat 10) excluded from all counts.
        lines = output.strip().split("\n")
        for line in lines:
            cells = [c.strip() for c in line.split("│") if c.strip()]
            if len(cells) == 3 and cells[0] == "files":
                assert cells[1] == "1", f"files excluded should be 1, got {cells[1]}"
                assert cells[2] == "3", f"files total should be 3, got {cells[2]}"
            elif len(cells) == 3 and cells[0] == "messages":
                assert cells[1] == "0", f"messages excluded should be 0, got {cells[1]}"
                assert cells[2] == "2", f"messages total should be 2, got {cells[2]}"
            elif len(cells) == 3 and cells[0] == "chats":
                assert cells[1] == "0", f"chats excluded should be 0, got {cells[1]}"
                assert cells[2] == "1", f"chats total should be 1, got {cells[2]}"


# ---------------------------------------------------------------------------
# RED 8: TestImportCommand
# ---------------------------------------------------------------------------


class TestImportCommand:
    """fp vectorize import <path> should apply flags from a JSON file."""

    def test_import_structured_format(self, tmp_path):
        """Import JSON with {"entity": "files", "action": "exclude", "ids": [...]}."""
        db_path = _make_test_db(tmp_path)

        json_file = tmp_path / "exclude.json"
        json_file.write_text(
            json.dumps(
                {
                    "entity": "files",
                    "action": "exclude",
                    "ids": [1, 2],
                }
            )
        )

        from footprinter.cli.vectorize_cmd import _handle_import

        args = argparse.Namespace(path=str(json_file))
        _handle_import(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        for fid in [1, 2]:
            row = conn.execute(
                "SELECT json_extract(metadata, '$.vectorize') as vec FROM files WHERE id = ?",
                (fid,),
            ).fetchone()
            assert row["vec"] == 0, f"File {fid} should be excluded after import"
        conn.close()

    def test_import_flat_list(self, tmp_path):
        """Import flat list of IDs (defaults to files + exclude)."""
        db_path = _make_test_db(tmp_path)

        json_file = tmp_path / "ids.json"
        json_file.write_text(json.dumps([1, 2]))

        from footprinter.cli.vectorize_cmd import _handle_import

        args = argparse.Namespace(path=str(json_file))
        _handle_import(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        for fid in [1, 2]:
            row = conn.execute(
                "SELECT json_extract(metadata, '$.vectorize') as vec FROM files WHERE id = ?",
                (fid,),
            ).fetchone()
            assert row["vec"] == 0
        conn.close()

    def test_import_include_action(self, tmp_path):
        """Import JSON with action=include should set vectorize=1."""
        db_path = _make_test_db(tmp_path)

        json_file = tmp_path / "include.json"
        json_file.write_text(
            json.dumps(
                {
                    "entity": "files",
                    "action": "include",
                    "ids": [3],  # File 3 was excluded in fixture
                }
            )
        )

        from footprinter.cli.vectorize_cmd import _handle_import

        args = argparse.Namespace(path=str(json_file))
        _handle_import(args, db_path=db_path)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT json_extract(metadata, '$.vectorize') as vec FROM files WHERE id = 3",
        ).fetchone()
        assert row["vec"] == 1
        conn.close()


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


class TestCLIRegistration:
    """fp vectorize should be registered and parseable."""

    def test_register_creates_subparser(self):
        from footprinter.cli.vectorize_cmd import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        # Should parse without error
        args = parent.parse_args(["vectorize", "exclude", "files", "1", "2"])
        assert args.entity == "files"
        assert args.ids == [1, 2]

    def test_review_subcommand(self):
        from footprinter.cli.vectorize_cmd import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        args = parent.parse_args(["vectorize", "review"])
        assert hasattr(args, "func")

    def test_import_subcommand(self):
        from footprinter.cli.vectorize_cmd import register

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        register(subs)

        args = parent.parse_args(["vectorize", "import", "/tmp/data.json"])
        assert args.path == "/tmp/data.json"


# ---------------------------------------------------------------------------
# RED 9: TestVectorizeRunFp — fp vectorize via run_fp()
# ---------------------------------------------------------------------------


class TestVectorizeRunFp:
    """Exercise fp vectorize through the CLI entry point (run_fp).

    Complements the handler-level tests above by validating that the
    subcommand is wired through the top-level parser and dispatches with
    the expected arguments. Patches open_db so tests stay hermetic.
    """

    def test_help_exits_zero(self):
        from conftest import run_fp

        _, _, code = run_fp("vectorize", "--help")
        assert code == 0

    def test_help_lists_actions(self):
        from conftest import run_fp

        stdout, stderr, _ = run_fp("vectorize", "--help")
        output = stdout + stderr
        for action in ("exclude", "include", "review", "import"):
            assert action in output, f"'{action}' not in fp vectorize --help"

    def test_bare_vectorize_exits_zero(self):
        from conftest import run_fp

        _, _, code = run_fp("vectorize")
        assert code == 0

    def test_exclude_dispatches_to_handler(self, tmp_path):
        from contextlib import contextmanager
        from unittest.mock import patch

        from conftest import run_fp

        db_path = _make_test_db(tmp_path)

        @contextmanager
        def _open(_db_path=None):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

        with patch("footprinter.cli.vectorize_cmd.open_db", _open):
            _, _, code = run_fp("vectorize", "exclude", "files", "1", "2")

        assert code == 0
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        for fid in (1, 2):
            row = conn.execute(
                "SELECT json_extract(metadata, '$.vectorize') AS vec FROM files WHERE id = ?",
                (fid,),
            ).fetchone()
            assert row["vec"] == 0
        conn.close()

    def test_review_runs(self, tmp_path):
        from contextlib import contextmanager
        from unittest.mock import patch

        from conftest import run_fp

        db_path = _make_test_db(tmp_path)

        @contextmanager
        def _open(_db_path=None):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

        with patch("footprinter.cli.vectorize_cmd.open_db", _open):
            stdout, stderr, code = run_fp("vectorize", "review")

        assert code == 0
        # Review prints a Rich table — at minimum, the entity labels.
        output = stdout + stderr
        assert "files" in output.lower()

    def test_import_applies_flags(self, tmp_path):
        from contextlib import contextmanager
        from unittest.mock import patch

        from conftest import run_fp

        db_path = _make_test_db(tmp_path)
        json_file = tmp_path / "exclude.json"
        json_file.write_text(json.dumps({"entity": "files", "action": "exclude", "ids": [1]}))

        @contextmanager
        def _open(_db_path=None):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

        with patch("footprinter.cli.vectorize_cmd.open_db", _open):
            _, _, code = run_fp("vectorize", "import", str(json_file))

        assert code == 0
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT json_extract(metadata, '$.vectorize') AS vec FROM files WHERE id = 1",
        ).fetchone()
        assert row["vec"] == 0
        conn.close()
