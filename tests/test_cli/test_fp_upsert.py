"""Tests for fp upsert — create, update, or assign entity records.

Validates:
  1. fp upsert --help exits 0 and lists noun subcommands
  2. Bare fp upsert shows help
  3. Single-mode dispatch (client/project) routes through service.upsert()
  4. Assign-mode dispatch (file --project-id N) routes through service.assign()
  5. Errors: missing required args, invalid noun
  6. CSV folder import: bulk folder-to-project/client assignment
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
        assert kwargs.get("name") == "demo"


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


# ---------------------------------------------------------------------------
# 6. CSV folder import
# ---------------------------------------------------------------------------


def _write_csv(tmp_path, lines: list[str]) -> str:
    """Write lines to a temp CSV under *tmp_path* and return the path."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("\n".join(lines))
    return str(csv_file)


class TestUpsertFoldersCsv:
    """fp upsert folders <csv> imports folder-to-project/client assignments."""

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_routes_to_assign(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "/tmp/docs,5",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value={"id": 10, "project_id": None},
            ),
            patch(
                "footprinter.services.folder_service.assign",
                return_value={"id": 10, "project_id": 5},
            ) as mock_assign,
        ):
            _, _, code = run_fp("upsert", "folders", csv_path, "--commit")

        assert code == 0
        mock_assign.assert_called_once()
        kwargs = mock_assign.call_args.kwargs
        assert kwargs["project_id"] == 5

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_dry_run_default(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "/tmp/docs,5",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value={"id": 10, "project_id": None},
            ),
            patch(
                "footprinter.services.folder_service.assign",
            ) as mock_assign,
        ):
            _, _, code = run_fp("upsert", "folders", csv_path)

        assert code == 0
        mock_assign.assert_not_called()

    def test_csv_missing_folder_path_column(self, tmp_path):
        csv_path = _write_csv(tmp_path, [
            "path,project_id",
            "/tmp/docs,5",
        ])
        _, _, code = run_fp("upsert", "folders", csv_path, "--commit")
        assert code != 0

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_unresolvable_folder_path(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "/nonexistent,5",
            "/also/missing,3",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value=None,
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
                return_value=None,
            ),
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--commit", "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["errors"] == 2
        assert result["assigned"] == 0

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_commit_json_total_is_assigned_plus_errors(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "/tmp/docs,5",
            "/nonexistent,3",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                side_effect=lambda conn, p: {"id": 10, "project_id": None} if p == "/tmp/docs" else None,
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
                return_value=None,
            ),
            patch(
                "footprinter.services.folder_service.assign",
                return_value={"id": 10, "project_id": 5},
            ),
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--commit", "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["assigned"] == 1
        assert result["errors"] == 1
        assert result["total"] == result["assigned"] + result["errors"]
        assert "skipped" not in result

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_unresolvable_project_name(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_name",
            "/tmp/docs,NoSuchProject",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value={"id": 10, "project_id": None},
            ),
            patch(
                "footprinter.db.projects.find_project_id_by_key",
                return_value=None,
            ),
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--commit", "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["errors"] == 1

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_row_without_target(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path",
            "/tmp/docs",
        ])
        with patch(
            "footprinter.db.folders.get_folder_by_path",
            return_value={"id": 10, "project_id": None},
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--commit", "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["errors"] == 1

    @patch("footprinter.cli.upsert._handle_bulk_assign")
    @patch("footprinter.cli.upsert.open_db")
    def test_folder_flag_still_works(self, mock_open_db, mock_bulk_assign):
        _patched_open_db(mock_open_db)
        mock_bulk_assign.return_value = None

        _, _, code = run_fp(
            "upsert", "folders", "--folder", "/tmp/x", "--project-id", "1",
        )

        assert code == 0
        mock_bulk_assign.assert_called_once()

    def test_csv_and_folder_flag_mutual_exclusion(self, tmp_path):
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "/tmp/docs,5",
        ])
        _, _, code = run_fp(
            "upsert", "folders", csv_path, "--folder", "/tmp/x",
            "--project-id", "1",
        )
        assert code != 0

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_dry_run_client_only_mismatch(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,client_id",
            "/tmp/docs,7",
        ])
        with patch(
            "footprinter.db.folders.get_folder_by_path",
            return_value={"id": 10, "project_id": None, "client_id": None},
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["would_assign"] == 1
        assert result["already_matched"] == 0

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_dry_run_client_already_matched(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,client_id",
            "/tmp/docs,7",
        ])
        with patch(
            "footprinter.db.folders.get_folder_by_path",
            return_value={"id": 10, "project_id": None, "client_id": 7},
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["already_matched"] == 1
        assert result["would_assign"] == 0

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_dry_run_mixed_project_match_client_mismatch(self, mock_open_db, tmp_path):
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id,client_id",
            "/tmp/docs,5,7",
        ])
        with patch(
            "footprinter.db.folders.get_folder_by_path",
            return_value={"id": 10, "project_id": 5, "client_id": None},
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["would_assign"] == 1
        assert result["already_matched"] == 0

    # -- path resolution ----------------------------------------

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_bare_relative_path_resolves(self, mock_open_db, tmp_path):
        """Bare relative path like Work/sample-tool resolves via relative_path fallback."""
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "Work/sample-tool,5",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value=None,
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
                return_value={"id": 10, "project_id": None},
            ),
            patch(
                "footprinter.services.folder_service.assign",
                return_value={"id": 10, "project_id": 5},
            ) as mock_assign,
        ):
            _, _, code = run_fp("upsert", "folders", csv_path, "--commit")

        assert code == 0
        mock_assign.assert_called_once()

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_tilde_path_resolves_via_expanduser(self, mock_open_db, tmp_path):
        """~/Work/sample-tool should expand to absolute and match path column."""
        from pathlib import Path

        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "~/Work/sample-tool,5",
        ])
        expected_abs = str(Path("~/Work/sample-tool").expanduser())
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                side_effect=lambda conn, p: {"id": 10, "project_id": None}
                if p == expected_abs
                else None,
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
            ) as mock_rel,
            patch(
                "footprinter.services.folder_service.assign",
                return_value={"id": 10, "project_id": 5},
            ),
        ):
            _, _, code = run_fp("upsert", "folders", csv_path, "--commit")

        assert code == 0
        mock_rel.assert_not_called()

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_absolute_path_still_works(self, mock_open_db, tmp_path):
        """Absolute paths should resolve on the first try without fallback."""
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "/Users/test/Work/demo,5",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value={"id": 10, "project_id": None},
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
            ) as mock_rel,
            patch(
                "footprinter.services.folder_service.assign",
                return_value={"id": 10, "project_id": 5},
            ),
        ):
            _, _, code = run_fp("upsert", "folders", csv_path, "--commit")

        assert code == 0
        mock_rel.assert_not_called()

    @patch("footprinter.cli.upsert.open_db")
    def test_csv_unresolvable_after_fallback(self, mock_open_db, tmp_path):
        """Path matching neither path nor relative_path still produces a clear error."""
        _patched_open_db(mock_open_db)
        csv_path = _write_csv(tmp_path, [
            "folder_path,project_id",
            "nonexistent/nope,5",
        ])
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value=None,
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
                return_value=None,
            ),
        ):
            stdout, _, code = run_fp(
                "upsert", "folders", csv_path, "--commit", "--json",
            )

        assert code == 0
        import json
        result = json.loads(stdout)
        assert result["errors"] == 1
        assert result["assigned"] == 0

    @patch("footprinter.cli.upsert.open_db")
    def test_folder_flag_relative_path_resolves(self, mock_open_db):
        """--folder Work/sample-tool resolves via relative_path fallback."""
        _patched_open_db(mock_open_db)
        with (
            patch(
                "footprinter.db.folders.get_folder_by_path",
                return_value=None,
            ),
            patch(
                "footprinter.db.folders.get_folder_by_relative_path",
                return_value={"id": 10},
            ),
            patch(
                "footprinter.db.folders.cascade_project_id",
                return_value={"folders_updated": 1, "files_updated": 0},
            ),
        ):
            _, _, code = run_fp(
                "upsert", "folders", "--folder", "Work/sample-tool",
                "--project-id", "1",
            )

        assert code == 0
