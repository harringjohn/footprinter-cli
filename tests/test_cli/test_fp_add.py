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
  12. Chat archive import routes to ChatIndexer.upload()
  13. Argument validation errors
"""

import json
from unittest.mock import MagicMock, patch

from conftest import run_fp


def _patched_open_db(mock_open_db):
    """Wire a MagicMock returned by patch() to behave as a context manager."""
    mock_conn = MagicMock()
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
        mock_conn = _patched_open_db(mock_open_db)
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
        ) as mock_get_fn:
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
# 12. Chat archive import
# ---------------------------------------------------------------------------


class TestAddChatImport:
    """fp add chats export.zip routes to ChatIndexer.upload()."""

    def test_add_chats_routes_to_upload(self, tmp_path):
        zip_path = tmp_path / "export.zip"
        zip_path.write_bytes(b"PK\x03\x04fake")

        with (
            patch("footprinter.ingest.chat_indexer.ChatIndexer") as MockIndexer,
            patch("footprinter.ingest.database.Database") as MockDatabase,
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
