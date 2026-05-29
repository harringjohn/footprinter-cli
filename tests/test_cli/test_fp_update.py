"""Tests for fp update — update-only entity records.

Validates:
  1. fp update --help exits 0 and lists noun subcommands
  2. Bare fp update shows help
  3. Single-mode update (client/project) routes through db.update_client/project()
  4. Single-mode errors when entity not found
  5. Single-mode errors when no flags provided
  6. Single-mode JSON output on success and failure
  7. Single-mode status validation
  8. Data entity assign routes through service.assign()
  9. Data entity status update routes through db.update_file_status()
  10. Data entity combined assign + status
  11. Bulk path assign for files
  12. Bulk CSV update for files
  13. Bulk folder assign
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


class TestUpdateHelp:
    """fp update --help exits 0 and lists noun subcommands."""

    def test_help_exits_zero(self):
        _, _, code = run_fp("update", "--help")
        assert code == 0

    def test_help_lists_nouns(self):
        stdout, stderr, _ = run_fp("update", "--help")
        output = stdout + stderr
        for noun in ("client", "project", "file", "files", "folder", "folders"):
            assert noun in output, f"'{noun}' not in fp update --help"


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


class TestUpdateBare:
    """fp update with no noun shows help and exits 0."""

    def test_bare_update_exits_zero(self):
        _, _, code = run_fp("update")
        assert code == 0


# ---------------------------------------------------------------------------
# 3. Super entity single update — happy path
# ---------------------------------------------------------------------------


class TestUpdateSingle:
    """fp update client/project updates an existing entity via db layer."""

    @patch("footprinter.cli.update.open_db")
    @patch("footprinter.cli.update._update_entity")
    def test_update_client_by_id(self, mock_update, mock_open_db):
        mock_conn = _patched_open_db(mock_open_db)
        mock_update.return_value = True

        _, _, code = run_fp("update", "client", "5", "--name", "New Name")

        assert code == 0
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1] == 5  # entity_id
        assert call_args[1].get("name") == "New Name"

    @patch("footprinter.cli.update.open_db")
    @patch("footprinter.cli.update._update_entity")
    def test_update_project_by_id(self, mock_update, mock_open_db):
        _patched_open_db(mock_open_db)
        mock_update.return_value = True

        _, _, code = run_fp("update", "project", "3", "--description", "Updated desc")

        assert code == 0
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1] == 3
        assert call_args[1].get("description") == "Updated desc"

    @patch("footprinter.cli.update.open_db")
    @patch("footprinter.cli.update._update_entity")
    def test_update_client_multiple_fields(self, mock_update, mock_open_db):
        _patched_open_db(mock_open_db)
        mock_update.return_value = True

        _, _, code = run_fp(
            "update", "client", "5",
            "--name", "New Name", "--type", "internal",
        )

        assert code == 0
        kwargs = mock_update.call_args[1]
        assert kwargs.get("name") == "New Name"
        assert kwargs.get("client_type") == "internal"


# ---------------------------------------------------------------------------
# 4. Super entity single update — not found
# ---------------------------------------------------------------------------


class TestUpdateSingleNotFound:
    """fp update errors when entity not found."""

    @patch("footprinter.cli.update.open_db")
    @patch("footprinter.cli.update._update_entity")
    def test_update_client_not_found(self, mock_update, mock_open_db):
        _patched_open_db(mock_open_db)
        mock_update.return_value = None

        stdout, stderr, code = run_fp("update", "client", "999", "--name", "X")

        assert code == 1
        output = stdout + stderr
        assert "not found" in output.lower()


# ---------------------------------------------------------------------------
# 5. Super entity single update — no flags
# ---------------------------------------------------------------------------


class TestUpdateSingleNoFlags:
    """fp update with no optional flags errors."""

    @patch("footprinter.cli.update.open_db")
    def test_update_client_no_flags(self, mock_open_db):
        _patched_open_db(mock_open_db)

        stdout, stderr, code = run_fp("update", "client", "5")

        assert code == 1
        output = stdout + stderr
        assert "at least one" in output.lower()


# ---------------------------------------------------------------------------
# 6. Super entity single update — JSON output
# ---------------------------------------------------------------------------


class TestUpdateSingleJson:
    """--json flag produces parseable JSON on success and failure."""

    @patch("footprinter.cli.update.open_db")
    @patch("footprinter.cli.update._update_entity")
    def test_update_client_json_success(self, mock_update, mock_open_db):
        _patched_open_db(mock_open_db)
        mock_update.return_value = True

        stdout, _, code = run_fp(
            "update", "client", "5", "--name", "New", "--json",
        )

        assert code == 0
        result = json.loads(stdout)
        assert result["id"] == 5
        assert result["action"] == "updated"

    @patch("footprinter.cli.update.open_db")
    @patch("footprinter.cli.update._update_entity")
    def test_update_client_json_not_found(self, mock_update, mock_open_db):
        _patched_open_db(mock_open_db)
        mock_update.return_value = None

        stdout, _, code = run_fp(
            "update", "client", "999", "--name", "X", "--json",
        )

        assert code == 1
        result = json.loads(stdout)
        assert "error" in result


# ---------------------------------------------------------------------------
# 7. Super entity single update — status validation
# ---------------------------------------------------------------------------


class TestUpdateSingleStatusValidation:
    """Invalid status values are rejected."""

    @patch("footprinter.cli.update.open_db")
    def test_update_client_invalid_status(self, mock_open_db):
        _patched_open_db(mock_open_db)

        stdout, stderr, code = run_fp("update", "client", "5", "--status", "bogus")

        assert code == 1
        output = stdout + stderr
        assert "invalid status" in output.lower()


# ---------------------------------------------------------------------------
# 8. Data entity assign
# ---------------------------------------------------------------------------


class TestUpdateDataAssign:
    """fp update file/email/etc assigns relationships via service.assign()."""

    @patch("footprinter.cli.update._get_service")
    @patch("footprinter.cli.update.open_db")
    def test_assign_file_to_project(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = {"id": 42, "project_id": 3}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("update", "file", "42", "--project-id", "3")

        assert code == 0
        mock_svc.assign.assert_called_once()
        call_kwargs = mock_svc.assign.call_args.kwargs
        assert call_kwargs["project_id"] == 3

    @patch("footprinter.cli.update._get_service")
    @patch("footprinter.cli.update.open_db")
    def test_assign_file_both_ids(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = {"id": 42, "project_id": 3, "client_id": 1}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp(
            "update", "file", "42", "--project-id", "3", "--client-id", "1",
        )

        assert code == 0
        call_kwargs = mock_svc.assign.call_args.kwargs
        assert call_kwargs["project_id"] == 3
        assert call_kwargs["client_id"] == 1

    @patch("footprinter.cli.update._get_service")
    @patch("footprinter.cli.update.open_db")
    def test_assign_file_not_found(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = None
        mock_get_svc.return_value = mock_svc

        stdout, stderr, code = run_fp("update", "file", "42", "--project-id", "3")

        assert code == 1
        output = stdout + stderr
        assert "not found" in output.lower()

    @patch("footprinter.cli.update._get_service")
    @patch("footprinter.cli.update.open_db")
    def test_assign_email_to_client(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = {"id": 10, "client_id": 2}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("update", "email", "10", "--client-id", "2")

        assert code == 0
        mock_get_svc.assert_called_with("email_service")


# ---------------------------------------------------------------------------
# 9. Data entity status update (file only, Phase 1)
# ---------------------------------------------------------------------------


class TestUpdateDataStatus:
    """fp update file --status routes to db.update_file_status()."""

    @patch("footprinter.cli.update._update_file_status")
    @patch("footprinter.cli.update.open_db")
    def test_update_file_status(self, mock_open_db, mock_status_fn):
        _patched_open_db(mock_open_db)
        mock_status_fn.return_value = True

        _, _, code = run_fp("update", "file", "42", "--status", "unlisted")

        assert code == 0
        mock_status_fn.assert_called_once()
        args = mock_status_fn.call_args
        assert args[0][1] == 42  # file_id
        assert args[0][2] == "unlisted"  # status

    @patch("footprinter.cli.update._update_file_status")
    @patch("footprinter.cli.update.open_db")
    def test_update_file_status_not_found(self, mock_open_db, mock_status_fn):
        _patched_open_db(mock_open_db)
        mock_status_fn.return_value = None

        stdout, stderr, code = run_fp("update", "file", "42", "--status", "unlisted")

        assert code == 1
        output = stdout + stderr
        assert "not found" in output.lower()

    @patch("footprinter.cli.update._update_file_status")
    @patch("footprinter.cli.update.open_db")
    def test_update_file_status_invalid(self, mock_open_db, mock_status_fn):
        _patched_open_db(mock_open_db)
        mock_status_fn.side_effect = ValueError("Invalid status 'bogus'")

        stdout, stderr, code = run_fp("update", "file", "42", "--status", "bogus")

        assert code == 1
        output = stdout + stderr
        assert "invalid" in output.lower()


# ---------------------------------------------------------------------------
# 10. Data entity combined assign + status
# ---------------------------------------------------------------------------


class TestUpdateDataCombined:
    """fp update file with both --project-id and --status handles both."""

    @patch("footprinter.cli.update._update_file_status")
    @patch("footprinter.cli.update._get_service")
    @patch("footprinter.cli.update.open_db")
    def test_assign_and_status_together(self, mock_open_db, mock_get_svc, mock_status_fn):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = {"id": 42, "project_id": 3}
        mock_get_svc.return_value = mock_svc
        mock_status_fn.return_value = True

        _, _, code = run_fp(
            "update", "file", "42",
            "--project-id", "3", "--status", "unlisted",
        )

        assert code == 0
        mock_svc.assign.assert_called_once()
        mock_status_fn.assert_called_once()


# ---------------------------------------------------------------------------
# 11. Bulk path assign — files
# ---------------------------------------------------------------------------


class TestUpdateBulkPathAssign:
    """fp update files --folder /path --project-id N assigns files under folder."""

    @patch("footprinter.cli.update._get_service")
    @patch("footprinter.cli.update.open_db")
    def test_bulk_assign_files_under_folder(self, mock_open_db, mock_get_svc):
        mock_conn = _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = {"id": 1}
        mock_get_svc.return_value = mock_svc

        with patch("footprinter.db.files.list_file_ids_under_path", return_value=[1, 2, 3]):
            _, _, code = run_fp(
                "update", "files", "--folder", "/tmp/demo", "--project-id", "5",
            )

        assert code == 0
        assert mock_svc.assign.call_count == 3

    @patch("footprinter.cli.update.open_db")
    def test_bulk_assign_files_no_ids(self, mock_open_db):
        _patched_open_db(mock_open_db)

        stdout, stderr, code = run_fp("update", "files", "--folder", "/tmp/demo")

        assert code == 1
        output = stdout + stderr
        assert "at least one" in output.lower()


# ---------------------------------------------------------------------------
# 12. Bulk CSV update — files
# ---------------------------------------------------------------------------


class TestUpdateBulkCsv:
    """fp update files corrections.csv applies bulk CSV updates."""

    @patch("footprinter.cli.update.open_db")
    def test_bulk_csv_updates_rows(self, mock_open_db, tmp_path):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, status TEXT, "
            "project_id INTEGER, client_id INTEGER)"
        )
        conn.execute("INSERT INTO files VALUES (1, 'listed', NULL, NULL)")
        conn.execute("INSERT INTO files VALUES (2, 'listed', NULL, NULL)")
        conn.commit()

        mock_open_db.return_value.__enter__.return_value = conn
        mock_open_db.return_value.__exit__.return_value = False

        csv_path = _write_csv(tmp_path, [
            "id,status,project_id",
            "1,unlisted,5",
            "2,removed,",
        ])

        stdout, stderr, code = run_fp("update", "files", csv_path)

        assert code == 0
        row1 = conn.execute("SELECT status, project_id FROM files WHERE id = 1").fetchone()
        assert row1[0] == "unlisted"
        assert row1[1] == 5
        row2 = conn.execute("SELECT status FROM files WHERE id = 2").fetchone()
        assert row2[0] == "removed"
        conn.close()

    @patch("footprinter.cli.update.open_db")
    def test_bulk_csv_missing_id_column(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, ["status,project_id", "unlisted,5"])

        stdout, stderr, code = run_fp("update", "files", csv_path)

        assert code == 1
        output = stdout + stderr
        assert "id" in output.lower()

    @patch("footprinter.cli.update.open_db")
    def test_bulk_csv_nonexistent_ids(self, mock_open_db, tmp_path):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, status TEXT, "
            "project_id INTEGER, client_id INTEGER)"
        )
        conn.commit()

        mock_open_db.return_value.__enter__.return_value = conn
        mock_open_db.return_value.__exit__.return_value = False

        csv_path = _write_csv(tmp_path, ["id,status", "999,unlisted"])

        stdout, stderr, code = run_fp("update", "files", csv_path, "--json")

        assert code == 0
        result = json.loads(stdout)
        assert result["errors"] >= 1
        conn.close()

    @patch("footprinter.cli.update.open_db")
    def test_bulk_csv_empty(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, ["id,status"])

        stdout, stderr, code = run_fp("update", "files", csv_path)

        assert code == 0
        output = stdout + stderr
        assert "nothing" in output.lower()

    @patch("footprinter.cli.update.open_db")
    def test_bulk_csv_zero_clears_fk(self, mock_open_db, tmp_path):
        """Sentinel value '0' for project_id/client_id clears to NULL."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, status TEXT, "
            "project_id INTEGER, client_id INTEGER)"
        )
        conn.execute("INSERT INTO files VALUES (1, 'listed', 5, 2)")
        conn.commit()

        mock_open_db.return_value.__enter__.return_value = conn
        mock_open_db.return_value.__exit__.return_value = False

        csv_path = _write_csv(tmp_path, ["id,project_id", "1,0"])

        _, _, code = run_fp("update", "files", csv_path)

        assert code == 0
        row = conn.execute("SELECT project_id FROM files WHERE id = 1").fetchone()
        assert row[0] is None
        conn.close()


# ---------------------------------------------------------------------------
# 13. Bulk folder assign
# ---------------------------------------------------------------------------


class TestUpdateBulkFolderAssign:
    """fp update folders --folder /path --project-id N cascades assignment."""

    @patch("footprinter.db.folders.get_folder_by_path")
    @patch("footprinter.db.folders.cascade_project_id")
    @patch("footprinter.cli.update.open_db")
    def test_bulk_assign_folders(self, mock_open_db, mock_cascade, mock_get_folder):
        _patched_open_db(mock_open_db)
        mock_get_folder.return_value = {"id": 10}
        mock_cascade.return_value = {"folders_updated": 3, "files_updated": 7}

        _, _, code = run_fp(
            "update", "folders", "--folder", "/tmp/demo", "--project-id", "5",
        )

        assert code == 0
        mock_cascade.assert_called_once()
