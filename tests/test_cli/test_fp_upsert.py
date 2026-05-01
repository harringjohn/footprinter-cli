"""Tests for fp upsert — create, update, or assign entity records.

Validates:
  1. fp upsert --help exits 0 and lists noun subcommands
  2. Bare fp upsert shows help
  3. Single-mode dispatch (client/project) routes through service.upsert()
  4. Assign-mode dispatch (file --project-id N) routes through service.assign()
  5. Errors: missing required args, invalid noun
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


class TestUpsertHelp:
    """fp upsert --help exits 0 and lists noun subcommands."""

    def test_help_exits_zero(self):
        _, _, code = run_fp("upsert", "--help")
        assert code == 0

    def test_help_lists_nouns(self):
        stdout, stderr, _ = run_fp("upsert", "--help")
        output = stdout + stderr
        for noun in ("client", "project", "file"):
            assert noun in output, f"'{noun}' not in fp upsert --help"


# ---------------------------------------------------------------------------
# 2. Bare invocation
# ---------------------------------------------------------------------------


class TestUpsertBare:
    """fp upsert with no noun shows help and exits 0."""

    def test_bare_upsert_exits_zero(self):
        _, _, code = run_fp("upsert")
        assert code == 0


# ---------------------------------------------------------------------------
# 3. Single mode
# ---------------------------------------------------------------------------


class TestUpsertSingle:
    """fp upsert client/project --name X dispatches to service.upsert()."""

    @patch("footprinter.cli.upsert._get_service")
    @patch("footprinter.cli.upsert.open_db")
    def test_upsert_client_calls_service(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.upsert.return_value = {"id": 1, "action": "created"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp(
            "upsert",
            "client",
            "--name",
            "Acme",
            "--type",
            "external",
        )

        assert code == 0
        mock_get_svc.assert_called_with("client_service")
        mock_svc.upsert.assert_called_once()
        # CLI flags arrive as kwargs.
        kwargs = mock_svc.upsert.call_args.kwargs
        assert kwargs.get("name") == "Acme"
        assert kwargs.get("client_type") == "external"

    @patch("footprinter.cli.upsert._get_service")
    @patch("footprinter.cli.upsert.open_db")
    def test_upsert_project_calls_service(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.upsert.return_value = {"id": 1, "action": "created"}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("upsert", "project", "--name", "demo")

        assert code == 0
        mock_get_svc.assert_called_with("project_service")
        kwargs = mock_svc.upsert.call_args.kwargs
        assert kwargs.get("project_name") == "demo"


# ---------------------------------------------------------------------------
# 4. Assign mode
# ---------------------------------------------------------------------------


class TestUpsertAssign:
    """fp upsert file <id> --project-id N routes through service.assign()."""

    @patch("footprinter.cli.upsert._get_service")
    @patch("footprinter.cli.upsert.open_db")
    def test_assign_file_to_project(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_svc.assign.return_value = {"id": 42, "project_id": 3}
        mock_get_svc.return_value = mock_svc

        _, _, code = run_fp("upsert", "file", "42", "--project-id", "3")

        assert code == 0
        mock_get_svc.assert_called_with("file_service")
        mock_svc.assign.assert_called_once()
        kwargs = mock_svc.assign.call_args.kwargs
        assert kwargs.get("project_id") == 3


# ---------------------------------------------------------------------------
# 5. Errors
# ---------------------------------------------------------------------------


class TestUpsertErrors:
    """Missing required args and invalid nouns exit non-zero."""

    def test_missing_required_args_exits_nonzero(self):
        # `client` requires --name and --type
        _, _, code = run_fp("upsert", "client")
        assert code != 0

    def test_invalid_noun_exits_nonzero(self):
        _, _, code = run_fp("upsert", "bogus")
        assert code != 0

    @patch("footprinter.cli.upsert._get_service")
    @patch("footprinter.cli.upsert.open_db")
    def test_assign_without_target_exits_one(self, mock_open_db, mock_get_svc):
        _patched_open_db(mock_open_db)
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # No --project-id or --client-id given → exits 1 before service called.
        _, _, code = run_fp("upsert", "file", "42")

        assert code == 1
        mock_svc.assign.assert_not_called()
