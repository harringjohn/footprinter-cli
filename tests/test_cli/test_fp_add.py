"""Tests for fp add — create-only entity records.

Validates:
  1. fp add --help exits 0 and lists noun subcommands
  2. Bare fp add shows help
  3. Single-mode create (client/project) routes through service.upsert()
  4. Single-mode errors when entity already exists
  5. Single-mode JSON output on success and failure
  6. Single-mode status validation
  7. Bulk super entity CSV creates new rows
  8. Bulk super entity CSV errors on existing rows
  9. Bulk CSV edge cases (missing columns, empty CSV, file not found)
  10. Data entity CSV routes to DB insert functions
  11. Data entity CSV error handling
  11b. Data entity CSV real-DB mutation guard (FPR-1885)
  12. Chat archive import routes to ChatIndexer.upload()
  13. Argument validation errors
"""

import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from conftest import run_fp


def _patched_open_db(mock_open_db):
    """Wire a MagicMock returned by patch() to behave as a context manager."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    mock_open_db.return_value.__enter__.return_value = mock_conn
    mock_open_db.return_value.__exit__.return_value = False
    return mock_conn


def _write_csv(tmp_path, lines: list[str]) -> str:
    """Write lines to a temp CSV under *tmp_path* and return the path."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("\n".join(lines))
    return str(csv_file)


# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestAddHelp:
    """fp add --help exits 0 and lists noun subcommands."""

    def test_help_exits_zero(self):
        _, _, code = run_fp("add", "--help")
        assert code == 0

    def test_help_lists_nouns(self):
        stdout, stderr, _ = run_fp("add", "--help")
        output = stdout + stderr
        for noun in ("client", "project", "clients", "projects", "files", "chats"):
            assert noun in output, f"'{noun}' not in fp add --help"


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


class TestAddBare:
    """fp add with no noun shows help and exits 0."""

    def test_bare_add_exits_zero(self):
        _, _, code = run_fp("add")
        assert code == 0


# ---------------------------------------------------------------------------
# 3. Single mode — happy path
# ---------------------------------------------------------------------------


class TestAddSingle:
    """fp add client/project creates a new entity via service.upsert()."""

    @patch("footprinter.cli.add._get_service")
    @patch("footprinter.cli.add.open_db")
    @patch("footprinter.cli.add._check_exists", return_value=False)
    def test_add_client_creates_new(self, _mock_exists, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.upsert.return_value = {"id": 1, "action": "created"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("add", "client", "--name", "Acme", "--type", "external")

        assert code == 0
        mock_get_svc.assert_called_with("client_service")
        mock_svc.upsert.assert_called_once()
        kwargs = mock_svc.upsert.call_args.kwargs
        assert kwargs.get("name") == "Acme"
        assert kwargs.get("client_type") == "external"

    @patch("footprinter.cli.add._get_service")
    @patch("footprinter.cli.add.open_db")
    @patch("footprinter.cli.add._check_exists", return_value=False)
    def test_add_project_creates_new(self, _mock_exists, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.upsert.return_value = {"id": 2, "action": "created"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("add", "project", "--name", "demo")

        assert code == 0
        mock_get_svc.assert_called_with("project_service")
        kwargs = mock_svc.upsert.call_args.kwargs
        assert kwargs.get("name") == "demo"


# ---------------------------------------------------------------------------
# 4. Single mode — already exists
# ---------------------------------------------------------------------------


class TestAddSingleAlreadyExists:
    """fp add errors when entity already exists."""

    @patch("footprinter.cli.add._get_service")
    @patch("footprinter.cli.add.open_db")
    @patch("footprinter.cli.add._check_exists", return_value=True)
    def test_add_client_existing_errors(self, _mock_exists, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        stdout, stderr, code = run_fp("add", "client", "--name", "Acme", "--type", "external")

        assert code == 1
        mock_svc.upsert.assert_not_called()
        output = stdout + stderr
        assert "already exists" in output.lower()


# ---------------------------------------------------------------------------
# 5. Single mode — JSON output
# ---------------------------------------------------------------------------


class TestAddSingleJson:
    """--json flag produces parseable JSON on success and failure."""

    @patch("footprinter.cli.add._get_service")
    @patch("footprinter.cli.add.open_db")
    @patch("footprinter.cli.add._check_exists", return_value=False)
    def test_add_client_json_success(self, _mock_exists, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.upsert.return_value = {"id": 1, "action": "created"}
        mock_get_svc.return_value = mock_svc

        stdout, _, code = run_fp(
            "add", "client", "--name", "Acme", "--type", "external", "--json",
        )

        assert code == 0
        result = json.loads(stdout)
        assert result["id"] == 1
        assert result["action"] == "created"

    @patch("footprinter.cli.add._get_service")
    @patch("footprinter.cli.add.open_db")
    @patch("footprinter.cli.add._check_exists", return_value=True)
    def test_add_client_json_already_exists(self, _mock_exists, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        stdout, _, code = run_fp(
            "add", "client", "--name", "Acme", "--type", "external", "--json",
        )

        assert code == 1
        result = json.loads(stdout)
        assert "error" in result


# ---------------------------------------------------------------------------
# 6. Single mode — status validation
# ---------------------------------------------------------------------------


class TestAddSingleStatusValidation:
    """Invalid --status value exits 1 before calling service."""

    @patch("footprinter.cli.add._get_service")
    @patch("footprinter.cli.add.open_db")
    def test_invalid_status_errors(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp(
            "add", "client", "--name", "X", "--type", "external", "--status", "bogus",
        )

        assert code == 1
        mock_svc.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Bulk super entity CSV — happy path
# ---------------------------------------------------------------------------


class TestAddBulkCsv:
    """fp add clients data.csv creates all new rows."""

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_clients_csv_creates_all_new(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "name,client_type",
            "Acme,external",
            "Beta,internal",
        ])
        with (
            patch("footprinter.cli.add._check_exists", return_value=False),
            patch("footprinter.cli.add._get_service") as mock_get_svc,
        ):
            mock_svc = MagicMock()
            mock_svc.upsert.return_value = {"id": 1, "action": "created"}
            mock_get_svc.return_value = mock_svc

            stdout, _, code = run_fp("add", "clients", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 2
        assert result["errors"] == 0

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_projects_csv_resolves_client_name(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "name,client",
            "my-api,Acme Corp",
        ])
        with (
            patch("footprinter.cli.add._check_exists", return_value=False),
            patch("footprinter.cli.add._get_service") as mock_get_svc,
            patch(
                "footprinter.db.clients.find_client_id_by_name",
                return_value=7,
            ),
        ):
            mock_svc = MagicMock()
            mock_svc.upsert.return_value = {"id": 1, "action": "created"}
            mock_get_svc.return_value = mock_svc

            stdout, _, code = run_fp("add", "projects", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 1
        kwargs = mock_svc.upsert.call_args.kwargs
        assert kwargs.get("client_id") == 7

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_projects_csv_unresolvable_client_name(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "name,client",
            "my-api,NoSuchClient",
        ])
        with (
            patch("footprinter.cli.add._check_exists", return_value=False),
            patch("footprinter.cli.add._get_service") as mock_get_svc,
            patch(
                "footprinter.db.clients.find_client_id_by_name",
                return_value=None,
            ),
        ):
            mock_svc = MagicMock()
            mock_get_svc.return_value = mock_svc

            stdout, _, code = run_fp("add", "projects", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 0
        assert result["errors"] == 1
        assert any("not found" in d["error"].lower() for d in result.get("error_details", []))
        mock_svc.upsert.assert_not_called()

    # ---------------------------------------------------------------------------
    # 8. Bulk super entity CSV — existing rows become errors
    # ---------------------------------------------------------------------------

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_clients_csv_existing_rows_are_errors(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "name,client_type",
            "Existing,external",
            "NewOne,internal",
        ])
        with (
            patch(
                "footprinter.cli.add._check_exists",
                side_effect=lambda conn, et, kw: kw.get("name") == "Existing",
            ),
            patch("footprinter.cli.add._get_service") as mock_get_svc,
        ):
            mock_svc = MagicMock()
            mock_svc.upsert.return_value = {"id": 2, "action": "created"}
            mock_get_svc.return_value = mock_svc

            stdout, _, code = run_fp("add", "clients", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 1
        assert result["errors"] == 1
        assert any(
            "already exists" in d["error"].lower()
            for d in result.get("error_details", [])
        )


# ---------------------------------------------------------------------------
# 9. Bulk CSV edge cases
# ---------------------------------------------------------------------------


class TestAddBulkCsvEdgeCases:
    """Missing columns, empty CSV, file not found."""

    def test_missing_required_column_exits_nonzero(self, tmp_path):
        csv_path = _write_csv(tmp_path, [
            "name",
            "Acme",
        ])
        _, _, code = run_fp("add", "clients", csv_path)
        assert code != 0

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_empty_csv_returns_zero_total(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "name,client_type",
        ])
        stdout, _, code = run_fp("add", "clients", csv_path, "--json")
        assert code == 0
        result = json.loads(stdout)
        assert result["total"] == 0

    def test_file_not_found_exits_nonzero(self):
        _, _, code = run_fp("add", "clients", "/nonexistent.csv")
        assert code != 0


# ---------------------------------------------------------------------------
# 10. Data entity CSV — routes to DB insert functions
# ---------------------------------------------------------------------------


class TestAddDataBulkCsv:
    """fp add files/emails/visits data.csv routes to DB insert functions."""

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_files_csv_creates_records(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/readme.md,readme.md",
            "/tmp/notes.txt,notes.txt",
        ])
        with patch(
            "footprinter.cli.add._get_insert_fn",
            return_value=MagicMock(return_value=("inserted", 1)),
        ):
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 2

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_emails_csv(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "message_id,thread_id,account,received_at",
            "msg-1,thread-1,gmail,2024-01-01T00:00:00",
        ])
        with patch(
            "footprinter.cli.add._get_insert_fn",
            return_value=MagicMock(return_value=1),
        ):
            stdout, _, code = run_fp("add", "emails", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 1

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_visits_csv(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "url,visit_time,browser",
            "https://example.com,2024-01-01T00:00:00,chrome",
        ])
        with patch(
            "footprinter.cli.add._get_insert_fn",
            return_value=MagicMock(return_value=1),
        ):
            stdout, _, code = run_fp("add", "visits", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 1

    # ---------------------------------------------------------------------------
    # 10b. Data entity CSV — existing rows become errors
    # ---------------------------------------------------------------------------

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_files_csv_updated_rows_are_errors(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/existing1.txt,existing1.txt",
            "/tmp/existing2.txt,existing2.txt",
        ])
        with patch(
            "footprinter.cli.add._get_insert_fn",
            return_value=MagicMock(return_value=("updated", 1)),
        ):
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 0
        assert result["errors"] == 2
        assert all(
            "already exists" in d["error"].lower()
            for d in result.get("error_details", [])
        )

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_files_csv_unchanged_rows_are_errors(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/existing.txt,existing.txt",
        ])
        with patch(
            "footprinter.cli.add._get_insert_fn",
            return_value=MagicMock(return_value=("unchanged", 1)),
        ):
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 0
        assert result["errors"] == 1

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_add_files_csv_mixed_new_and_existing(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/new.txt,new.txt",
            "/tmp/existing.txt,existing.txt",
        ])
        mock_insert = MagicMock(side_effect=[("inserted", 1), ("updated", 2)])
        with patch("footprinter.cli.add._get_insert_fn", return_value=mock_insert):
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 1
        assert result["errors"] == 1
        assert len(result.get("error_details", [])) == 1
        assert result["error_details"][0]["row"] == 2

    # ---------------------------------------------------------------------------
    # 11. Data entity CSV — insert error handling
    # ---------------------------------------------------------------------------

    @patch("footprinter.cli.add.IngestService")
    @patch("footprinter.cli.add.open_db")
    def test_insert_error_counted_not_crash(self, mock_open_db, _mock_ingest, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/a.txt,a.txt",
            "/tmp/b.txt,b.txt",
        ])
        mock_insert = MagicMock(side_effect=[("inserted", 1), Exception("constraint")])
        with patch("footprinter.cli.add._get_insert_fn", return_value=mock_insert):
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 1
        assert result["errors"] == 1

    def test_data_csv_missing_required_column(self, tmp_path):
        csv_path = _write_csv(tmp_path, [
            "file_name",
            "readme.md",
        ])
        _, _, code = run_fp("add", "files", csv_path)
        assert code != 0


# ---------------------------------------------------------------------------
# 11b. Data entity CSV — real-DB mutation guard (FPR-1885)
# ---------------------------------------------------------------------------


class TestAddDataBulkCsvNoMutation:
    """Verify that ``fp add files data.csv`` does not mutate existing rows.

    Uses a real SQLite database (not mocked ``_get_insert_fn``) so the
    actual write path is exercised.
    """

    @staticmethod
    def _seed_file(conn, *, path="/tmp/existing.txt"):
        """Insert a fully-populated file record and return the row dict."""
        from footprinter.db.files import insert_file

        result = insert_file(conn, {
            "file_path": path,
            "file_name": "existing.txt",
            "content_type": "text/plain",
            "size_bytes": 999,
            "sha256_hash": "aaa111",
            "content_preview": "hello world",
        })
        assert result[0] == "inserted"
        conn.commit()
        row = conn.execute(
            "SELECT sha256_hash, size_bytes, vectorized_at FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return dict(row)

    @staticmethod
    def _open_db_ctx(conn):
        """Return a context manager that yields *conn* (replaces ``open_db``)."""

        @contextmanager
        def _ctx():
            yield conn

        return _ctx

    def test_add_files_csv_does_not_mutate_existing_row(self, temp_db, tmp_path):
        """Seed a file, run CSV add with same path, assert columns unchanged."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row
        original = self._seed_file(db.conn)

        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/existing.txt,existing.txt",
        ])

        with (
            patch("footprinter.cli.add.open_db", self._open_db_ctx(db.conn)),
            patch("footprinter.cli.add.IngestService") as MockIngest,
        ):
            MockIngest.return_value.begin.return_value = 1
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        after = db.conn.execute(
            "SELECT sha256_hash, size_bytes, vectorized_at FROM files WHERE path = ?",
            ("/tmp/existing.txt",),
        ).fetchone()
        after = dict(after)
        db.conn.close()

        assert after["sha256_hash"] == original["sha256_hash"]
        assert after["size_bytes"] == original["size_bytes"]
        assert after["vectorized_at"] == original["vectorized_at"]

    def test_add_files_csv_existing_reports_error_without_mutation(self, temp_db, tmp_path):
        """Existing row → errors == 1, created == 0, 'already exists'."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row
        self._seed_file(db.conn)

        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/existing.txt,existing.txt",
        ])

        with (
            patch("footprinter.cli.add.open_db", self._open_db_ctx(db.conn)),
            patch("footprinter.cli.add.IngestService") as MockIngest,
        ):
            MockIngest.return_value.begin.return_value = 1
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        db.conn.close()

        assert code == 0
        result = json.loads(stdout)
        assert result["created"] == 0
        assert result["errors"] == 1
        assert "already exists" in result["error_details"][0]["error"].lower()

    def test_add_files_csv_new_row_still_inserts_real_db(self, temp_db, tmp_path):
        """New file path inserts successfully via the real insert function."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row

        csv_path = _write_csv(tmp_path, [
            "file_path,file_name",
            "/tmp/brand_new.txt,brand_new.txt",
        ])

        with (
            patch("footprinter.cli.add.open_db", self._open_db_ctx(db.conn)),
            patch("footprinter.cli.add.IngestService") as MockIngest,
        ):
            MockIngest.return_value.begin.return_value = 1
            stdout, _, code = run_fp("add", "files", csv_path, "--json")

        result = json.loads(stdout)
        row = db.conn.execute(
            "SELECT id FROM files WHERE path = ?",
            ("/tmp/brand_new.txt",),
        ).fetchone()
        db.conn.close()

        assert code == 0
        assert result["created"] == 1
        assert row is not None

    # -- message mutation guard (FPR-1894) ----------------------------------

    @staticmethod
    def _seed_message(conn, *, chat_id, message_id="msg-existing", role="user", content="hello world"):
        """Insert a chat + message and return (message_row_dict, internal_chat_id)."""
        from footprinter.db.chats import insert_chat, insert_message

        internal_chat_id = insert_chat(conn, {
            "external_id": f"chat-ext-{chat_id}",
            "account": "test",
            "title": "Test Chat",
            "message_count": 0,
        })
        insert_message(conn, {
            "chat_id": internal_chat_id,
            "message_id": message_id,
            "role": role,
            "content": content,
        })
        conn.commit()
        row = conn.execute(
            "SELECT chat_id, message_id, role, content, indexed_at "
            "FROM messages WHERE chat_id = ? AND message_id = ?",
            (internal_chat_id, message_id),
        ).fetchone()
        return dict(row), internal_chat_id

    def test_data_entity_exists_true_for_messages_with_key(self, temp_db):
        """Messages with (chat_id, message_id) key → existence check returns True."""
        from footprinter.ingest.database import Database
        from footprinter.cli.add import _data_entity_exists

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row
        _row, chat_id = self._seed_message(db.conn, chat_id="exist-test", message_id="msg-123")

        assert _data_entity_exists(
            db.conn, "messages", {"chat_id": str(chat_id), "message_id": "msg-123"},
        ) is True
        db.conn.close()

    def test_data_entity_exists_false_for_messages_without_message_id(self, temp_db):
        """Messages without message_id → existence check returns False (no stable key)."""
        from footprinter.ingest.database import Database
        from footprinter.cli.add import _data_entity_exists

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row

        assert _data_entity_exists(db.conn, "messages", {"chat_id": "1", "role": "user"}) is False
        db.conn.close()

    def test_add_messages_csv_existing_reports_error_without_duplication(self, temp_db, tmp_path):
        """Existing message → errors == 1, created == 0, no duplicate row inserted."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row
        _row, chat_id = self._seed_message(
            db.conn, chat_id="err", message_id="msg-dup", content="original",
        )

        csv_path = _write_csv(tmp_path, [
            "chat_id,role,message_id,content",
            f"{chat_id},assistant,msg-dup,duplicate attempt",
        ])

        with (
            patch("footprinter.cli.add.open_db", self._open_db_ctx(db.conn)),
            patch("footprinter.cli.add.IngestService") as MockIngest,
        ):
            MockIngest.return_value.begin.return_value = 1
            stdout, _, code = run_fp("add", "messages", csv_path, "--json")

        result = json.loads(stdout)
        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, "msg-dup"),
        ).fetchone()["cnt"]
        db.conn.close()

        assert code == 0
        assert result["created"] == 0
        assert result["errors"] == 1
        assert "already exists" in result["error_details"][0]["error"].lower()
        assert count == 1

    def test_add_messages_csv_new_row_still_inserts_real_db(self, temp_db, tmp_path):
        """New message inserts successfully via the real insert function."""
        from footprinter.ingest.database import Database
        from footprinter.db.chats import insert_chat

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row

        chat_id = insert_chat(db.conn, {
            "external_id": "chat-new",
            "account": "test",
            "title": "Test Chat",
            "message_count": 0,
        })
        db.conn.commit()

        csv_path = _write_csv(tmp_path, [
            "chat_id,role,message_id,content",
            f"{chat_id},user,msg-brand-new,hello world",
        ])

        with (
            patch("footprinter.cli.add.open_db", self._open_db_ctx(db.conn)),
            patch("footprinter.cli.add.IngestService") as MockIngest,
        ):
            MockIngest.return_value.begin.return_value = 1
            stdout, _, code = run_fp("add", "messages", csv_path, "--json")

        result = json.loads(stdout)
        row = db.conn.execute(
            "SELECT id FROM messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, "msg-brand-new"),
        ).fetchone()
        db.conn.close()

        assert code == 0
        assert result["created"] == 1
        assert row is not None

    def test_add_messages_csv_null_message_id_always_inserts(self, temp_db, tmp_path):
        """Messages without message_id bypass the existence guard and always insert."""
        from footprinter.ingest.database import Database
        from footprinter.db.chats import insert_chat

        db = Database(temp_db)
        db.conn.row_factory = sqlite3.Row

        chat_id = insert_chat(db.conn, {
            "external_id": "chat-null-mid",
            "account": "test",
            "title": "Test Chat",
            "message_count": 0,
        })
        db.conn.commit()

        csv_path = _write_csv(tmp_path, [
            "chat_id,role,content",
            f"{chat_id},user,first message",
            f"{chat_id},user,second message",
        ])

        with (
            patch("footprinter.cli.add.open_db", self._open_db_ctx(db.conn)),
            patch("footprinter.cli.add.IngestService") as MockIngest,
        ):
            MockIngest.return_value.begin.return_value = 1
            stdout, _, code = run_fp("add", "messages", csv_path, "--json")

        result = json.loads(stdout)
        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()["cnt"]
        db.conn.close()

        assert code == 0
        assert result["created"] == 2
        assert count == 2


# ---------------------------------------------------------------------------
# 12. Chat archive import
# ---------------------------------------------------------------------------


class TestAddChatImport:
    """fp add chats export.zip routes to ChatIndexer.upload()."""

    def test_add_chats_routes_to_upload(self, tmp_path):
        zip_path = tmp_path / "export.zip"
        zip_path.write_bytes(b"PK\x03\x04fake")

        with (
            patch("footprinter.ingest.chat_indexer.ChatIndexer") as MockIndexer,
            patch("footprinter.ingest.database.Database"),
            patch("footprinter.paths.get_db_path", return_value=tmp_path / "test.db"),
        ):
            mock_indexer = MockIndexer.return_value
            mock_indexer.upload.return_value = {
                "status": "completed",
                "chats_added": 3,
                "chats_updated": 0,
                "messages_imported": 15,
                "errors": 0,
            }

            _, _, code = run_fp("add", "chats", str(zip_path))

        assert code == 0
        mock_indexer.upload.assert_called_once()


# ---------------------------------------------------------------------------
# 13. Argument validation
# ---------------------------------------------------------------------------


class TestAddArgErrors:
    """Missing required args and invalid nouns exit non-zero."""

    def test_missing_required_args_exits_nonzero(self):
        _, _, code = run_fp("add", "client")
        assert code != 0

    def test_invalid_noun_exits_nonzero(self):
        _, _, code = run_fp("add", "bogus")
        assert code != 0

    def test_bulk_without_file_exits_nonzero(self):
        _, _, code = run_fp("add", "clients")
        assert code != 0
