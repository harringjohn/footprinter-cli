"""Tests for fp delete — soft-delete entity records.

Validates:
  1. fp delete --help exits 0 and lists noun subcommands
  2. Bare fp delete shows help (exits 0)
  3. Routing: fp delete client <id> dispatches to client_service.delete()
  4. Errors: invalid ID and not-found record exit 1
  5. Confirmation: SafeConfirm gate respected unless --yes
  6. JSON output via --json
"""

from unittest.mock import MagicMock, patch

from conftest import run_fp


def _patched_open_db(mock_open_db):
    """Wire a MagicMock returned by patch() to behave as a context manager."""
    mock_conn = MagicMock()
    mock_open_db.return_value.__enter__.return_value = mock_conn
    mock_open_db.return_value.__exit__.return_value = False
    return mock_conn


# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestDeleteHelp:
    """fp delete --help exits 0 and lists noun subcommands."""

    def test_help_exits_zero(self):
        _, _, code = run_fp("delete", "--help")
        assert code == 0

    def test_help_lists_nouns(self):
        stdout, stderr, _ = run_fp("delete", "--help")
        output = stdout + stderr
        for noun in ("client", "project"):
            assert noun in output, f"'{noun}' not in fp delete --help"


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


class TestDeleteBare:
    """fp delete with no noun shows help and exits 0."""

    def test_bare_delete_exits_zero(self):
        _, _, code = run_fp("delete")
        assert code == 0


# ---------------------------------------------------------------------------
# 3. Routing
# ---------------------------------------------------------------------------


class TestDeleteRouting:
    """fp delete <noun> <id> --yes dispatches to <noun>_service.delete()."""

    @patch("footprinter.cli.delete._get_service")
    @patch("footprinter.cli.delete.open_db")
    def test_delete_client_calls_service(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.get.return_value = {"id": 42, "name": "Acme"}
        mock_svc.delete.return_value = {"id": 42, "status": "removed"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("delete", "client", "42", "--yes")

        assert code == 0
        mock_get_svc.assert_called_with("client_service")
        mock_svc.delete.assert_called_once()
        # entity_id is the second positional arg after conn
        assert mock_svc.delete.call_args[0][1] == 42

    @patch("footprinter.cli.delete._get_service")
    @patch("footprinter.cli.delete.open_db")
    def test_delete_project_routes_to_project_service(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.get.return_value = {"id": 7, "project_name": "demo"}
        mock_svc.delete.return_value = {"id": 7, "status": "removed"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("delete", "project", "7", "--yes")

        assert code == 0
        mock_get_svc.assert_called_with("project_service")


# ---------------------------------------------------------------------------
# 4. Errors
# ---------------------------------------------------------------------------


class TestDeleteErrors:
    """Invalid IDs and not-found records exit 1."""

    def test_invalid_id_exits_one(self):
        # Non-integer ID is rejected before the DB is opened.
        _, _, code = run_fp("delete", "client", "abc", "--yes")
        assert code == 1

    @patch("footprinter.cli.delete._get_service")
    @patch("footprinter.cli.delete.open_db")
    def test_not_found_exits_one(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.get.return_value = None
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("delete", "client", "999", "--yes")

        assert code == 1
        mock_svc.delete.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Confirmation
# ---------------------------------------------------------------------------


class TestDeleteConfirmation:
    """Without --yes, SafeConfirm gates the delete."""

    @patch("footprinter.cli.delete._get_service")
    @patch("footprinter.cli.delete.open_db")
    @patch("footprinter.cli._prompt.SafeConfirm.ask")
    def test_confirm_no_aborts(self, mock_ask, mock_open_db, mock_get_svc):
        mock_ask.return_value = False
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.get.return_value = {"id": 42, "name": "Acme"}
        mock_get_svc.return_value = mock_svc

        stdout, stderr, code = run_fp("delete", "client", "42")

        assert code == 0
        mock_svc.delete.assert_not_called()
        assert "ancel" in (stdout + stderr)  # "Cancelled."

    @patch("footprinter.cli.delete._get_service")
    @patch("footprinter.cli.delete.open_db")
    @patch("footprinter.cli._prompt.SafeConfirm.ask")
    def test_confirm_yes_proceeds(self, mock_ask, mock_open_db, mock_get_svc):
        mock_ask.return_value = True
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.get.return_value = {"id": 42, "name": "Acme"}
        mock_svc.delete.return_value = {"id": 42, "status": "removed"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("delete", "client", "42")

        assert code == 0
        mock_svc.delete.assert_called_once()


# ---------------------------------------------------------------------------
# 6. JSON output
# ---------------------------------------------------------------------------


class TestDeleteJson:
    """fp delete --json emits JSON instead of human-readable output."""

    @patch("footprinter.cli.delete._get_service")
    @patch("footprinter.cli.delete.open_db")
    def test_json_output(self, mock_open_db, mock_get_svc):
        import json

        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.get.return_value = {"id": 42, "name": "Acme"}
        mock_svc.delete.return_value = {"id": 42, "status": "removed"}
        mock_get_svc.return_value = mock_svc

        stdout, _, code = run_fp("delete", "client", "42", "--yes", "--json")

        assert code == 0
        data = json.loads(stdout)
        assert data["id"] == 42
        assert data["status"] == "removed"
