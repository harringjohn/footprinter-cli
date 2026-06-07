"""Tests for fp permission subcommands (list, set, reset, check).

Covers:
  - Parser tree: help exits 0 for all subcommands
  - List: show all policies with target terminology
  - Set: unified policy setter (--visibility / --access)
  - Set CSV: bulk record policies via CSV file
  - Reset: policy delete / reseed with confirmation changes
  - Check: resolve target with required argument
"""

import csv
import io
import os
import sqlite3
import tempfile
from argparse import Namespace
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_fp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_STATS = {"file": 5, "email": 2}
MOCK_LARGE_COUNTS = {"file": 200, "email": 50}


def _mock_conn():
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


def _render_table(mock_console) -> str:
    """Extract and render the first Rich Table from mock_console.print calls."""
    from io import StringIO

    from rich.console import Console as RichConsole
    from rich.table import Table

    for call_args in mock_console.print.call_args_list:
        if call_args[0] and isinstance(call_args[0][0], Table):
            buf = StringIO()
            RichConsole(file=buf, width=120).print(call_args[0][0])
            return buf.getvalue()
    raise AssertionError("Rich Table was not printed")


# ---------------------------------------------------------------------------
# Parser tree tests
# ---------------------------------------------------------------------------


class TestPermissionParserTree:
    @pytest.mark.parametrize(
        "args",
        [
            ("permission", "--help"),
            ("permission", "list", "--help"),
            ("permission", "set", "--help"),
            ("permission", "reset", "--help"),
            ("permission", "check", "--help"),
            ("permission", "recalculate", "--help"),
        ],
    )
    def test_help_exits_zero(self, args):
        stdout, stderr, code = run_fp(*args)
        assert code == 0

    def test_set_rejects_dry_run_flag(self):
        stdout, stderr, code = run_fp(
            "permission", "set", "global", "--dry-run", "--visibility", "full"
        )
        assert "unrecognized arguments: --dry-run" in stderr

    def test_bare_permission_shows_help(self):
        stdout, stderr, code = run_fp("permission")
        assert code == 0
        combined = stdout + stderr
        assert "list" in combined
        assert "set" in combined
        assert "reset" in combined
        assert "check" in combined
        assert "recalculate" in combined


class TestCheckHelpContent:
    def test_check_help_mentions_scope(self):
        stdout, stderr, code = run_fp("permission", "check", "--help")
        combined = stdout + stderr
        assert "scope" in combined.lower()
        assert "--folder" not in combined
        assert "--project" not in combined
        assert "--client" not in combined


class TestResetHelpContent:
    def test_reset_help_mentions_inheritance(self):
        stdout, stderr, code = run_fp("permission", "reset", "--help")
        combined = stdout + stderr
        assert code == 0
        assert "inherit" in combined.lower(), f"Expected 'inherit' in reset help, got: {combined}"

    def test_reset_help_mentions_reseed(self):
        stdout, stderr, code = run_fp("permission", "reset", "--help")
        combined = stdout + stderr
        assert "--all" in combined, f"Expected '--all' in reset help, got: {combined}"


# ---------------------------------------------------------------------------
# List subcommand
# ---------------------------------------------------------------------------


class TestListShowsAllPolicies:
    @patch(
        "footprinter.cli.permission_cmd.list_permission_policies",
        return_value=[{"scope": "global", "setting": "allow", "updated_at": "2026-01-01"}],
    )
    @patch(
        "footprinter.cli.permission_cmd.list_visibility_policies",
        return_value=[{"scope": "global", "setting": "full", "updated_at": "2026-01-01"}],
    )
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_shows_merged_table(self, mock_db, mock_vis, mock_perm, capsys):
        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=False))

        output = capsys.readouterr().out
        assert "Scope" in output
        assert "Visibility" in output
        assert "Access" in output

    @patch(
        "footprinter.cli.permission_cmd.list_permission_policies",
        return_value=[],
    )
    @patch(
        "footprinter.cli.permission_cmd.list_visibility_policies",
        return_value=[{"scope": "global", "setting": "full", "updated_at": "2026-01-01"}],
    )
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_displays_full_visibility(self, mock_db, mock_vis, mock_perm, capsys):
        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=False))

        output = capsys.readouterr().out
        assert "full" in output
        assert "visible" not in output.lower().replace("visibility", "")

    @patch("footprinter.cli.permission_cmd.list_permission_policies", return_value=[])
    @patch("footprinter.cli.permission_cmd.list_visibility_policies", return_value=[])
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_no_policies_shows_message(self, mock_db, mock_vis, mock_perm, capsys):
        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=False))

        output = capsys.readouterr().out
        assert "No policies configured" in output

    @patch("footprinter.cli.permission_cmd.list_permission_policies", return_value=[])
    @patch("footprinter.cli.permission_cmd.list_visibility_policies", return_value=[])
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_no_policies_hint_uses_permission_cmd(self, mock_db, mock_vis, mock_perm, capsys):
        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=False))

        output = capsys.readouterr().out
        assert "fp permission set" in output

    @patch(
        "footprinter.cli.permission_cmd.list_permission_policies",
        return_value=[{"scope": "global", "setting": "allow", "updated_at": "2026-01-01"}],
    )
    @patch(
        "footprinter.cli.permission_cmd.list_visibility_policies",
        return_value=[{"scope": "global", "setting": "full", "updated_at": "2026-01-01"}],
    )
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_json_mode(self, mock_db, mock_vis, mock_perm, capsys):
        import json

        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=True))

        output = capsys.readouterr().out
        data = json.loads(output)
        assert "visibility" in data
        assert "permission" in data

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_list_no_db_shows_message(self, mock_db, capsys):
        from footprinter.cli.permission_cmd import _list

        _list(Namespace(json=False))

        output = capsys.readouterr().out
        assert "No database found" in output

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_list_no_db_json_returns_empty(self, mock_db, capsys):
        import json

        from footprinter.cli.permission_cmd import _list

        _list(Namespace(json=True))

        output = capsys.readouterr().out
        data = json.loads(output)
        assert data == {"visibility": [], "permission": []}

    @patch("footprinter.cli.permission_cmd.list_permission_policies", return_value=[])
    @patch(
        "footprinter.cli.permission_cmd.list_visibility_policies",
        return_value=[{"scope": "global", "setting": "full", "updated_at": "2026-01-01"}],
    )
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_conn_closed(self, mock_db, mock_vis, mock_perm):
        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=False))

        conn.close.assert_called_once()

    @patch("footprinter.cli.permission_cmd.list_permission_policies", return_value=[])
    @patch(
        "footprinter.cli.permission_cmd.list_visibility_policies",
        return_value=[
            {"scope": "global", "setting": "full", "updated_at": "2026-01-01"},
        ],
    )
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_list_shows_baseline_hint(self, mock_db, mock_vis, mock_perm, capsys):
        from footprinter.cli.permission_cmd import _list

        conn = _mock_conn()
        mock_db.return_value = conn

        _list(Namespace(json=False))

        output = capsys.readouterr().out
        assert "access=allow" in output.lower()


# ---------------------------------------------------------------------------
# Set subcommand: validation
# ---------------------------------------------------------------------------


class TestSetValidation:
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_no_flags_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility=None, access=None))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_invalid_access_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility=None, access="hidden"))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_invalid_visibility_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility="allow", access=None))

        assert exc_info.value.code == 1

    def test_set_visit_scope_gives_clarifying_hint(self, capsys):
        from footprinter.cli.permission_cmd import _set

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="visit:1", visibility=None, access="allow", csv_file=None))

        assert exc_info.value.code == 1
        captured = capsys.readouterr().out
        assert "source:browser" in captured, (
            "set visit:<id> should hint that visits inherit from source:browser"
        )


# ---------------------------------------------------------------------------
# Set subcommand: terminology translation
# ---------------------------------------------------------------------------


class TestSetTranslation:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_visibility_full_translates_to_visible(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="full", access=None))

        mock_set_vis.assert_called_once_with(conn, "global", "full")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_visibility_opaque_passes_through(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="opaque", access=None))

        mock_set_vis.assert_called_once_with(conn, "global", "opaque")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_visibility_hidden_passes_through(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="hidden", access=None))

        mock_set_vis.assert_called_once_with(conn, "global", "hidden")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_access_maps_to_set_permission_policy(self, mock_db, mock_count, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="folder:~/Work", visibility=None, access="deny"))

        mock_set_perm.assert_called_once_with(conn, "folder:~/Work", "deny")


# ---------------------------------------------------------------------------
# Set subcommand: behavior
# ---------------------------------------------------------------------------


class TestSetBehavior:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_both_values(self, mock_db, mock_count, mock_set_vis, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="full", access="allow"))

        mock_set_vis.assert_called_once_with(conn, "global", "full")
        mock_set_perm.assert_called_once_with(conn, "global", "allow")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_triggers_recalculate_once(self, mock_db, mock_count, mock_set_vis, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="full", access="allow"))

        mock_recalc.assert_called_once_with(conn, "global")

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_set_no_db_exits_1(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility="full", access=None))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_conn_closed_after_success(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="full", access=None))

        conn.close.assert_called_once()

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", side_effect=RuntimeError("boom"))
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_conn_closed_on_error(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(RuntimeError):
            _set(Namespace(scope="global", visibility="full", access=None))

        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Set subcommand: entity preview
# ---------------------------------------------------------------------------


class TestSetEntityPreview:
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 5, "folder": 2})
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_preview_shows_pluralized_counts(self, mock_db, mock_set_vis, mock_recalc, mock_count, capsys):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="hidden", access=None))

        output = capsys.readouterr().out
        assert "5 files" in output
        assert "2 folders" in output

    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 1})
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_preview_shows_singular_count(self, mock_db, mock_set_vis, mock_recalc, mock_count, capsys):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="hidden", access=None))

        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output


# ---------------------------------------------------------------------------
# Set subcommand: no confirmation
# ---------------------------------------------------------------------------


class TestSetNoConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_never_prompts_even_large_scope(self, mock_db, mock_set_vis, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="hidden", access=None))

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()


# ---------------------------------------------------------------------------
# Reset subcommand: validation
# ---------------------------------------------------------------------------


class TestResetValidation:
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_no_args_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _reset(Namespace(scope=None, all=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_all_with_scope_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _reset(Namespace(scope="global", all=True))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_usage_hint_says_fp_permission(self, mock_db, capsys):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit):
            _reset(Namespace(scope=None, all=False))

        output = capsys.readouterr().out
        assert "fp permission reset" in output


# ---------------------------------------------------------------------------
# Reset subcommand: scope-specific
# ---------------------------------------------------------------------------


class TestResetScope:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_scope_deletes_both_policies(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()
        mock_db.return_value = conn

        _reset(Namespace(scope="project:3", all=False))

        mock_del_vis.assert_called_once()
        mock_del_perm.assert_called_once()

    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_scope_no_confirmation(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc, mock_confirm):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()
        mock_db.return_value = conn

        _reset(Namespace(scope="project:3", all=False))

        mock_confirm.assert_not_called()

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_scope_triggers_recalculate(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()
        mock_db.return_value = conn

        _reset(Namespace(scope="project:3", all=False))

        mock_recalc.assert_called_once_with(conn, "project:3")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.delete_visibility_policy")
    @patch("footprinter.cli.permission_cmd.delete_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_scope_nonexistent_no_recalculate(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        _reset(Namespace(scope="project:99", all=False))

        mock_recalc.assert_not_called()
        mock_del_vis.assert_not_called()
        mock_del_perm.assert_not_called()

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.delete_visibility_policy")
    @patch("footprinter.cli.permission_cmd.delete_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_scope_nonexistent_prints_message(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc, capsys):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        _reset(Namespace(scope="project:99", all=False))

        output = capsys.readouterr().out
        assert "No policies found" in output

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_conn_closed(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()
        mock_db.return_value = conn

        _reset(Namespace(scope="project:3", all=False))

        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Reset subcommand: --all
# ---------------------------------------------------------------------------


class TestResetAll:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.seed_permission_defaults")
    @patch("footprinter.cli.permission_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.permission_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.permission_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.permission_cmd.confirm_recalculation", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_all_clears_and_reseeds(
        self, mock_db, mock_confirm, mock_clear_vis, mock_clear_perm,
        mock_seed_vis, mock_seed_perm, mock_recalc,
    ):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        _reset(Namespace(scope=None, all=True, yes=False))

        mock_clear_vis.assert_called_once()
        mock_clear_perm.assert_called_once()
        mock_seed_vis.assert_called_once()
        mock_seed_perm.assert_called_once()
        mock_recalc.assert_called_once_with(conn, "global")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.seed_permission_defaults")
    @patch("footprinter.cli.permission_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.permission_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.permission_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.permission_cmd.confirm_recalculation", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_all_requires_confirmation(
        self, mock_db, mock_confirm, mock_clear_vis, mock_clear_perm,
        mock_seed_vis, mock_seed_perm, mock_recalc,
    ):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        _reset(Namespace(scope=None, all=True, yes=False))

        mock_confirm.assert_called_once()

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.seed_permission_defaults")
    @patch("footprinter.cli.permission_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.permission_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.permission_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.permission_cmd.confirm_recalculation", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_all_yes_bypasses_confirmation(
        self, mock_db, mock_confirm, mock_clear_vis, mock_clear_perm,
        mock_seed_vis, mock_seed_perm, mock_recalc,
    ):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        _reset(Namespace(scope=None, all=True, yes=True))

        mock_confirm.assert_called_once_with(conn, "global", yes=True)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.seed_permission_defaults")
    @patch("footprinter.cli.permission_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.permission_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.permission_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.permission_cmd.confirm_recalculation", return_value=False)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_all_cancelled_skips_everything(
        self, mock_db, mock_confirm, mock_clear_vis, mock_clear_perm,
        mock_seed_vis, mock_seed_perm, mock_recalc,
    ):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        _reset(Namespace(scope=None, all=True, yes=False))

        mock_clear_vis.assert_not_called()
        mock_clear_perm.assert_not_called()
        mock_recalc.assert_not_called()

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_reset_all_no_db_exits_1(self, mock_db):
        from footprinter.cli.permission_cmd import _reset

        with pytest.raises(SystemExit) as exc_info:
            _reset(Namespace(scope=None, all=True, yes=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.seed_permission_defaults")
    @patch("footprinter.cli.permission_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.permission_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.permission_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.permission_cmd.confirm_recalculation", return_value=True)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_reset_all_conn_closed(
        self, mock_db, mock_confirm, mock_clear_vis, mock_clear_perm,
        mock_seed_vis, mock_seed_perm, mock_recalc,
    ):
        from footprinter.cli.permission_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        _reset(Namespace(scope=None, all=True, yes=False))

        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Check subcommand: routing
# ---------------------------------------------------------------------------


class TestCheckScopeRouting:
    @patch("footprinter.cli.permission_cmd.check_file_path", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_bare_path_routes_to_check_file_path(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="~/Work/file.py", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "~/Work/file.py", False, False)

    @patch("footprinter.cli.permission_cmd.check_file_path", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_file_prefix_path_routes_to_check_file_path(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="file:~/Work/file.py", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "~/Work/file.py", False, False)

    @patch("footprinter.cli.permission_cmd.check_folder", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_folder_prefix_routes_to_check_folder(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="folder:~/Work", json=False, verbose=True))

        mock_check.assert_called_once_with(conn, "~/Work", False, True)

    @patch("footprinter.cli.permission_cmd.check_project", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_project_prefix_routes_to_check_project(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="project:3", json=False, verbose=True))

        mock_check.assert_called_once_with(conn, 3, False, True)

    @patch("footprinter.cli.permission_cmd.check_client", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_client_prefix_routes_to_check_client(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="client:7", json=False, verbose=True))

        mock_check.assert_called_once_with(conn, 7, False, True)

    def test_no_scope_returns_1(self):
        from footprinter.cli.permission_cmd import _check

        rc = _check(Namespace(scope=None, json=False, verbose=False))

        assert rc == 1

    def test_unsupported_scope_source_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="source:emails", json=False, verbose=False))

        assert exc_info.value.code == 1

    def test_unsupported_scope_global_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="global", json=False, verbose=False))

        assert exc_info.value.code == 1

    def test_unsupported_scope_account_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="account:personal", json=False, verbose=False))

        assert exc_info.value.code == 1

    def test_invalid_scope_prefix_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="bogus:42", json=False, verbose=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.check_file_path", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_file_numeric_id_routes_to_check_file_path(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        file_cursor = MagicMock()
        file_cursor.fetchone.return_value = {"path": "/home/user/test.py", "status": "listed"}
        conn.execute.return_value = file_cursor
        mock_db.return_value = conn

        _check(Namespace(scope="file:42", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "/home/user/test.py", False, False)

    @patch("footprinter.cli.permission_cmd.check_folder", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_folder_numeric_id_routes_to_check_folder(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        folder_cursor = MagicMock()
        folder_cursor.fetchone.return_value = {"path": "/home/user/Work"}
        conn.execute.return_value = folder_cursor
        mock_db.return_value = conn

        _check(Namespace(scope="folder:42", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "/home/user/Work", False, False)

    @patch("footprinter.cli.permission_cmd.check_entity", create=True, return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_email_routes_to_check_entity(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="email:10", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "email", 10, False, False)

    @patch("footprinter.cli.permission_cmd.check_entity", create=True, return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_chat_routes_to_check_entity(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="chat:5", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "chat", 5, False, False)

    @patch("footprinter.cli.permission_cmd.check_entity", create=True, return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_visit_routes_to_check_entity(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="visit:3", json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "visit", 3, False, False)

    def test_email_non_numeric_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="email:abc", json=False, verbose=False))

        assert exc_info.value.code == 1

    def test_chat_non_numeric_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="chat:abc", json=False, verbose=False))

        assert exc_info.value.code == 1

    def test_visit_non_numeric_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(scope="visit:abc", json=False, verbose=False))

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Check subcommand: no DB
# ---------------------------------------------------------------------------


class TestCheckNoDb:
    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_check_no_db_shows_baseline(self, mock_db, capsys):
        from footprinter.cli.permission_cmd import _check

        _check(Namespace(scope="~/Work/file.py", json=False, verbose=False))

        output = capsys.readouterr().out
        assert "Access" in output
        assert "baseline" in output.lower()

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_check_no_db_json(self, mock_db, capsys):
        import json

        from footprinter.cli.permission_cmd import _check

        _check(Namespace(scope="~/Work/file.py", json=True, verbose=False))

        output = capsys.readouterr().out
        data = json.loads(output)
        assert "permission" in data
        assert data["found_in_db"] is False

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_check_no_db_json_scope_in_path_field(self, mock_db, capsys):
        import json

        from footprinter.cli.permission_cmd import _check

        _check(Namespace(scope="folder:~/Work", json=True, verbose=False))

        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["path"] == "folder:~/Work"


# ---------------------------------------------------------------------------
# Check subcommand: connection lifecycle
# ---------------------------------------------------------------------------


class TestCheckConnection:
    @patch("footprinter.cli.permission_cmd.check_file_path", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_conn_closed_after_success(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(scope="~/Work/file.py", json=False, verbose=False))

        conn.close.assert_called_once()

    @patch("footprinter.cli.permission_cmd.check_file_path", side_effect=RuntimeError("boom"))
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_conn_closed_on_error(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(RuntimeError):
            _check(Namespace(scope="~/Work/file.py", json=False, verbose=False))

        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Check subcommand: numeric ID lookup edge cases
# ---------------------------------------------------------------------------


class TestCheckNumericIdLookup:
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_file_id_not_found_returns_1(self, mock_db, capsys):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor
        mock_db.return_value = conn

        rc = _check(Namespace(scope="file:99", json=False, verbose=False))

        assert rc == 1
        output = capsys.readouterr().out
        assert "not found" in output.lower()

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_folder_id_not_found_returns_1(self, mock_db, capsys):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor
        mock_db.return_value = conn

        rc = _check(Namespace(scope="folder:99", json=False, verbose=False))

        assert rc == 1
        output = capsys.readouterr().out
        assert "not found" in output.lower()


# ---------------------------------------------------------------------------
# Check subcommand: exit-code propagation
# ---------------------------------------------------------------------------


class TestCheckExitCodePropagation:
    """A check helper's return value must reach the process exit code.

    _check returns the helper's int rather than discarding it, and the CLI
    dispatch turns that int into the process exit status. Without both, a
    failed check (e.g. a non-existent entity) would print an error but exit 0.
    """

    @patch("footprinter.cli.permission_cmd.check_entity", create=True, return_value=1)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_does_not_swallow_entity_return(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        mock_db.return_value = _mock_conn()

        rc = _check(Namespace(scope="email:99", json=False, verbose=False))

        assert rc == 1

    @patch("footprinter.cli.permission_cmd.check_entity", create=True, return_value=1)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_main_converts_nonzero_return_to_exit_code(self, mock_db, mock_check):
        mock_db.return_value = _mock_conn()

        _stdout, _stderr, code = run_fp("permission", "check", "email:99")

        assert code == 1

    @patch("footprinter.cli.permission_cmd.check_entity", create=True, return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_main_exits_zero_on_successful_check(self, mock_db, mock_check):
        mock_db.return_value = _mock_conn()

        _stdout, _stderr, code = run_fp("permission", "check", "email:10")

        assert code == 0


# ---------------------------------------------------------------------------
# Check subcommand: verbose output for project / client
# ---------------------------------------------------------------------------


class TestCheckProjectVerbose:
    def _make_conn(self, project_row, file_rows):
        conn = _mock_conn()
        cursor_one = MagicMock()
        cursor_one.fetchone.return_value = project_row
        cursor_all = MagicMock()
        cursor_all.fetchall.return_value = file_rows
        conn.execute.side_effect = [cursor_one, cursor_all]
        return conn

    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_project_verbose_json(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_json
    ):
        from footprinter.cli._policy_helpers import check_project

        project_row = {"id": 3, "name": "My Project"}
        file_rows = [{"id": 10, "name": "report.pdf"}, {"id": 11, "name": "notes.md"}]
        conn = self._make_conn(project_row, file_rows)

        mock_batch_perm.return_value = {10: (True, "baseline"), 11: (False, "policy:5")}
        mock_batch_vis.return_value = {10: ("full", "baseline"), 11: ("hidden", "policy:5")}

        check_project(conn, 3, json_output=True, verbose=True)

        data = mock_json.call_args[0][0]
        assert data["project_id"] == 3
        assert "file_count" in data
        assert data["file_count"] == 2
        assert "permission_counts" in data
        assert "visibility_counts" in data
        assert "files" in data
        assert len(data["files"]) == 2
        assert data["files"][0]["name"] == "report.pdf"

    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    def test_check_project_nonverbose_json(self, mock_perm, mock_vis, mock_json):
        from footprinter.cli._policy_helpers import check_project

        project_row = {"id": 3, "name": "My Project"}
        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = project_row
        conn.execute.return_value = cursor

        check_project(conn, 3, json_output=True, verbose=False)

        data = mock_json.call_args[0][0]
        assert "files" not in data
        assert "file_count" not in data

    @patch("footprinter.cli._policy_helpers.console")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_project_verbose_table_output(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_console
    ):
        from footprinter.cli._policy_helpers import check_project

        project_row = {"id": 3, "name": "My Project"}
        file_rows = [{"id": 10, "name": "report.pdf"}, {"id": 11, "name": "notes.md"}]
        conn = self._make_conn(project_row, file_rows)

        mock_batch_perm.return_value = {10: (True, "baseline"), 11: (False, "policy:5")}
        mock_batch_vis.return_value = {10: ("full", "baseline"), 11: ("hidden", "policy:5")}

        check_project(conn, 3, json_output=False, verbose=True)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Files: 2" in printed
        assert "allow: 1" in printed
        assert "deny: 1" in printed
        assert "full: 1" in printed
        assert "hidden: 1" in printed

        rendered = _render_table(mock_console)
        assert "report.pdf" in rendered
        assert "notes.md" in rendered

    @patch("footprinter.cli._policy_helpers.console")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_project_verbose_table_renders_columns(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_console
    ):
        from footprinter.cli._policy_helpers import check_project

        project_row = {"id": 3, "name": "My Project"}
        file_rows = [{"id": 10, "name": "report.pdf"}]
        conn = self._make_conn(project_row, file_rows)

        mock_batch_perm.return_value = {10: (True, "baseline")}
        mock_batch_vis.return_value = {10: ("full", "baseline")}

        check_project(conn, 3, json_output=False, verbose=True)

        rendered = _render_table(mock_console)
        assert "Name" in rendered
        assert "Permission" in rendered
        assert "Source" in rendered
        assert "Visibility" in rendered


class TestCheckClientVerbose:
    def _make_conn(self, client_row, project_rows):
        conn = _mock_conn()
        cursor_one = MagicMock()
        cursor_one.fetchone.return_value = client_row
        cursor_all = MagicMock()
        cursor_all.fetchall.return_value = project_rows
        conn.execute.side_effect = [cursor_one, cursor_all]
        return conn

    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_client_verbose_json(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_json
    ):
        from footprinter.cli._policy_helpers import check_client

        client_row = {"id": 5, "name": "Acme Corp"}
        project_rows = [{"id": 20, "name": "Project A"}, {"id": 21, "name": "Project B"}]
        conn = self._make_conn(client_row, project_rows)

        mock_batch_perm.return_value = {20: (True, "baseline"), 21: (True, "policy:8")}
        mock_batch_vis.return_value = {20: ("full", "baseline"), 21: ("opaque", "policy:8")}

        check_client(conn, 5, json_output=True, verbose=True)

        data = mock_json.call_args[0][0]
        assert data["client_id"] == 5
        assert "project_count" in data
        assert data["project_count"] == 2
        assert "permission_counts" in data
        assert "visibility_counts" in data
        assert "projects" in data
        assert len(data["projects"]) == 2
        assert data["projects"][0]["name"] == "Project A"

    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    def test_check_client_nonverbose_json(self, mock_perm, mock_vis, mock_json):
        from footprinter.cli._policy_helpers import check_client

        client_row = {"id": 5, "name": "Acme Corp"}
        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = client_row
        conn.execute.return_value = cursor

        check_client(conn, 5, json_output=True, verbose=False)

        data = mock_json.call_args[0][0]
        assert "projects" not in data
        assert "project_count" not in data

    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_client_verbose_excludes_unlisted(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_json
    ):
        from footprinter.cli._policy_helpers import check_client

        client_row = {"id": 5, "name": "Acme Corp"}
        listed_only = [{"id": 20, "name": "Listed Project"}]
        conn = self._make_conn(client_row, listed_only)

        mock_batch_perm.return_value = {20: (True, "baseline")}
        mock_batch_vis.return_value = {20: ("full", "baseline")}

        check_client(conn, 5, json_output=True, verbose=True)

        proj_query = conn.execute.call_args_list[1][0][0]
        assert "status = 'listed'" in proj_query

        data = mock_json.call_args[0][0]
        assert data["project_count"] == 1
        assert len(data["projects"]) == 1
        assert data["projects"][0]["name"] == "Listed Project"

    @patch("footprinter.cli._policy_helpers.console")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_client_verbose_table_output(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_console
    ):
        from footprinter.cli._policy_helpers import check_client

        client_row = {"id": 5, "name": "Acme Corp"}
        project_rows = [{"id": 20, "name": "Project A"}, {"id": 21, "name": "Project B"}]
        conn = self._make_conn(client_row, project_rows)

        mock_batch_perm.return_value = {20: (True, "baseline"), 21: (True, "policy:8")}
        mock_batch_vis.return_value = {20: ("full", "baseline"), 21: ("opaque", "policy:8")}

        check_client(conn, 5, json_output=False, verbose=True)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Projects: 2" in printed
        assert "allow: 2" in printed
        assert "full: 1" in printed
        assert "opaque: 1" in printed

        rendered = _render_table(mock_console)
        assert "Project A" in rendered
        assert "Project B" in rendered

    @patch("footprinter.cli._policy_helpers.console")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.visibility.batch_resolve_visibility")
    @patch("footprinter.permissions.batch_resolve_permissions")
    def test_check_client_verbose_table_renders_columns(
        self, mock_batch_perm, mock_batch_vis, mock_perm, mock_vis, mock_console
    ):
        from footprinter.cli._policy_helpers import check_client

        client_row = {"id": 5, "name": "Acme Corp"}
        project_rows = [{"id": 20, "name": "Project A"}]
        conn = self._make_conn(client_row, project_rows)

        mock_batch_perm.return_value = {20: (True, "baseline")}
        mock_batch_vis.return_value = {20: ("full", "baseline")}

        check_client(conn, 5, json_output=False, verbose=True)

        rendered = _render_table(mock_console)
        assert "Name" in rendered
        assert "Permission" in rendered
        assert "Source" in rendered
        assert "Visibility" in rendered


# ---------------------------------------------------------------------------
# Check subcommand: verbose on single-file target
# ---------------------------------------------------------------------------


class TestCheckFilePathVerbose:
    """--verbose on a single-file target should not be silently ignored."""

    def _make_conn(self, file_row, project_row=None):
        conn = _mock_conn()
        cursors = []

        file_cursor = MagicMock()
        file_cursor.fetchone.return_value = file_row
        cursors.append(file_cursor)

        if file_row and file_row.get("project_id") is not None:
            proj_cursor = MagicMock()
            proj_cursor.fetchone.return_value = project_row
            cursors.append(proj_cursor)

        policy_cursor = MagicMock()
        policy_cursor.fetchone.return_value = None
        policy_cursor.fetchall.return_value = []
        cursors.append(policy_cursor)

        conn.execute.side_effect = lambda *a, **kw: cursors.pop(0) if cursors else policy_cursor
        return conn

    @patch("footprinter.cli._policy_helpers.print_policy_chain")
    @patch("footprinter.cli._policy_helpers.build_policy_chain", return_value=[])
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.cli._policy_helpers.console")
    def test_check_file_path_verbose_prints_note(
        self, mock_console, mock_perm, mock_vis, mock_chain, mock_print_chain
    ):
        from footprinter.cli._policy_helpers import check_file_path

        file_row = {"id": 1, "name": "test.py", "project_id": None}
        conn = self._make_conn(file_row)

        check_file_path(conn, "/tmp/test.py", json_output=False, verbose=True)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "verbose" in printed.lower()
        assert "policy chain" in printed.lower()

    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.cli._policy_helpers.build_policy_chain", return_value=[])
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    def test_check_file_path_verbose_json_includes_note(
        self, mock_perm, mock_vis, mock_chain, mock_json
    ):
        from footprinter.cli._policy_helpers import check_file_path

        file_row = {"id": 1, "name": "test.py", "project_id": None}
        conn = self._make_conn(file_row)

        check_file_path(conn, "/tmp/test.py", json_output=True, verbose=True)

        data = mock_json.call_args[0][0]
        assert "verbose_note" in data

    @patch("footprinter.cli._policy_helpers.print_policy_chain")
    @patch("footprinter.cli._policy_helpers.build_policy_chain", return_value=[])
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.cli._policy_helpers.console")
    def test_check_file_path_nonverbose_no_note(
        self, mock_console, mock_perm, mock_vis, mock_chain, mock_print_chain
    ):
        from footprinter.cli._policy_helpers import check_file_path

        file_row = {"id": 1, "name": "test.py", "project_id": None}
        conn = self._make_conn(file_row)

        check_file_path(conn, "/tmp/test.py", json_output=False, verbose=False)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "verbose" not in printed.lower()

    @patch("footprinter.cli._policy_helpers.build_policy_chain", return_value=[])
    @patch("footprinter.cli._policy_helpers.simulate_path_visibility", return_value=("full", "baseline"))
    @patch("footprinter.cli._policy_helpers.simulate_path_permission", return_value=("allow", "baseline"))
    @patch("footprinter.cli._policy_helpers.console")
    def test_check_file_path_not_found_tip_uses_scope_strings(
        self, mock_console, mock_sim_perm, mock_sim_vis, mock_chain
    ):
        from footprinter.cli._policy_helpers import check_file_path

        conn = self._make_conn(None)

        check_file_path(conn, "/tmp/unknown.py", json_output=False, verbose=False)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "--folder" not in printed
        assert "--project" not in printed
        assert "folder:" in printed
        assert "project:" in printed


# ---------------------------------------------------------------------------
# Check subcommand: entity check (email, chat, visit)
# ---------------------------------------------------------------------------


class TestCheckEntity:
    def _make_conn(self, entity_row):
        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = entity_row
        conn.execute.return_value = cursor
        return conn

    @patch("footprinter.cli._policy_helpers.build_entity_policy_chain", return_value=[])
    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    def test_check_entity_email_json(self, mock_perm, mock_vis, mock_json, mock_chain):
        from footprinter.cli._policy_helpers import check_entity

        email_row = {"id": 10, "subject": "Test Email", "account": "work", "project_id": None, "client_id": None}
        conn = self._make_conn(email_row)

        check_entity(conn, "email", 10, json_output=True, verbose=False)

        data = mock_json.call_args[0][0]
        assert data["entity_type"] == "email"
        assert data["entity_id"] == 10
        assert data["display_name"] == "Test Email"
        assert data["permission"]["resolved"] == "allow"
        assert data["visibility"]["resolved"] == "full"

    @patch("footprinter.cli._policy_helpers.console")
    def test_check_entity_email_not_found(self, mock_console):
        from footprinter.cli._policy_helpers import check_entity

        conn = self._make_conn(None)

        result = check_entity(conn, "email", 99, json_output=False, verbose=False)

        assert result == 1

    @patch("footprinter.cli._policy_helpers.build_entity_policy_chain", return_value=[])
    @patch("footprinter.cli._policy_helpers.print_policy_chain")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.cli._policy_helpers.console")
    def test_check_entity_chat_rich_output(
        self, mock_console, mock_perm, mock_vis, mock_chain_print, mock_chain_build
    ):
        from footprinter.cli._policy_helpers import check_entity

        chat_row = {"id": 5, "title": "Dev Chat", "account": "work", "project_id": 3, "client_id": 1}
        conn = self._make_conn(chat_row)

        check_entity(conn, "chat", 5, json_output=False, verbose=False)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Dev Chat" in printed
        assert "allow" in printed
        assert "full" in printed

    @patch("footprinter.cli._policy_helpers.build_entity_policy_chain", return_value=[])
    @patch("footprinter.cli._policy_helpers.output_json")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "source:browser"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "source:browser"))
    def test_check_entity_visit_json(self, mock_perm, mock_vis, mock_json, mock_chain):
        from footprinter.cli._policy_helpers import check_entity

        visit_row = {"id": 3, "title": "GitHub", "url": "https://github.com"}
        conn = self._make_conn(visit_row)

        check_entity(conn, "visit", 3, json_output=True, verbose=False)

        data = mock_json.call_args[0][0]
        assert data["entity_type"] == "visit"
        assert data["entity_id"] == 3
        assert data["display_name"] == "GitHub"

    @patch("footprinter.cli._policy_helpers.build_entity_policy_chain", return_value=[])
    @patch("footprinter.cli._policy_helpers.print_policy_chain")
    @patch("footprinter.visibility.resolve_visibility_with_source", return_value=("full", "baseline"))
    @patch("footprinter.permissions.resolve_permission_with_source", return_value=(True, "baseline"))
    @patch("footprinter.cli._policy_helpers.console")
    def test_check_entity_verbose_note(
        self, mock_console, mock_perm, mock_vis, mock_chain_print, mock_chain_build
    ):
        from footprinter.cli._policy_helpers import check_entity

        email_row = {"id": 10, "subject": "Test", "account": "work", "project_id": None, "client_id": None}
        conn = self._make_conn(email_row)

        check_entity(conn, "email", 10, json_output=False, verbose=True)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "verbose" in printed.lower()


# ---------------------------------------------------------------------------
# Set CSV: argument parsing
# ---------------------------------------------------------------------------


class TestSetCsvArgParsing:
    def test_csv_arg_accepted_by_parser(self):
        _, _, code = run_fp("permission", "set", "--help")
        assert code == 0

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_without_csv_unchanged(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="full", access=None, csv_file=None))

        mock_set_vis.assert_called_once_with(conn, "global", "full")


# ---------------------------------------------------------------------------
# Set CSV: validation (flag conflicts, scope, file, header)
# ---------------------------------------------------------------------------


class TestSetCsvValidation:
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_with_visibility_flag_errors(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="source:emails", csv_file="x.csv", visibility="full", access=None))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_with_access_flag_errors(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="source:emails", csv_file="x.csv", visibility=None, access="deny"))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_requires_source_scope(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", csv_file="x.csv", visibility=None, access=None))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_rejects_source_browser(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="source:browser", csv_file="x.csv", visibility=None, access=None))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_file_not_found(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(
                scope="source:emails", csv_file="/nonexistent/path.csv",
                visibility=None, access=None,
            ))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_missing_id_column(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("visibility,access\nhidden,deny\n")
            f.flush()
            try:
                with pytest.raises(SystemExit) as exc_info:
                    _set(Namespace(scope="source:emails", csv_file=f.name, visibility=None, access=None))
                assert exc_info.value.code == 1
            finally:
                os.unlink(f.name)

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_no_policy_columns(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name\n10,test\n")
            f.flush()
            try:
                with pytest.raises(SystemExit) as exc_info:
                    _set(Namespace(scope="source:emails", csv_file=f.name, visibility=None, access=None))
                assert exc_info.value.code == 1
            finally:
                os.unlink(f.name)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_csv_empty_no_data_rows(self, mock_db, mock_set_perm, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,visibility,access\n")
            f.flush()
            try:
                _set(Namespace(scope="source:emails", csv_file=f.name, visibility=None, access=None))
                mock_set_vis.assert_not_called()
                mock_set_perm.assert_not_called()
                mock_recalc.assert_not_called()
            finally:
                os.unlink(f.name)


# ---------------------------------------------------------------------------
# Set CSV: atomic abort on row validation
# ---------------------------------------------------------------------------


def _write_csv(content: str) -> str:
    """Write CSV content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    f.write(content)
    f.close()
    return f.name


def _mock_conn_with_id_check(valid_ids: set[int] | None = None):
    """Return a mock connection that responds to ID existence queries."""
    conn = _mock_conn()
    if valid_ids is None:
        valid_ids = set()

    def fake_execute(sql, params=None):
        result = MagicMock()
        if sql.startswith("SELECT 1 FROM") and params:
            result.fetchone.return_value = {"1": 1} if params[0] in valid_ids else None
        else:
            result.fetchone.return_value = None
            result.fetchall.return_value = []
        return result

    conn.execute = MagicMock(side_effect=fake_execute)
    return conn


class TestSetCsvAtomicAbort:
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_invalid_id_not_integer(self, mock_db, mock_set_perm, mock_set_vis):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn()
        csv_path = _write_csv("id,visibility\nabc,hidden\n")
        try:
            with pytest.raises(SystemExit) as exc_info:
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert exc_info.value.code == 1
            mock_set_vis.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_invalid_visibility(self, mock_db, mock_set_vis):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn_with_id_check({10})
        csv_path = _write_csv("id,visibility\n10,secret\n")
        try:
            with pytest.raises(SystemExit) as exc_info:
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert exc_info.value.code == 1
            mock_set_vis.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_invalid_access(self, mock_db, mock_set_perm):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn_with_id_check({10})
        csv_path = _write_csv("id,access\n10,maybe\n")
        try:
            with pytest.raises(SystemExit) as exc_info:
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert exc_info.value.code == 1
            mock_set_perm.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_row_with_neither_setting(self, mock_db, mock_set_perm, mock_set_vis):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn_with_id_check({10})
        csv_path = _write_csv("id,visibility,access\n10,,\n")
        try:
            with pytest.raises(SystemExit) as exc_info:
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert exc_info.value.code == 1
            mock_set_vis.assert_not_called()
            mock_set_perm.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_nonexistent_id(self, mock_db, mock_set_perm, mock_set_vis):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn_with_id_check(set())
        csv_path = _write_csv("id,visibility\n99,hidden\n")
        try:
            with pytest.raises(SystemExit) as exc_info:
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert exc_info.value.code == 1
            mock_set_vis.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_abort_prevents_any_writes(self, mock_db, mock_set_perm, mock_set_vis):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn_with_id_check({10})
        csv_path = _write_csv("id,visibility\n10,hidden\n20,secret\n")
        try:
            with pytest.raises(SystemExit):
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            mock_set_vis.assert_not_called()
            mock_set_perm.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_line_number_includes_header(self, mock_db, mock_set_vis, capsys):
        from footprinter.cli.permission_cmd import _set

        mock_db.return_value = _mock_conn_with_id_check({10, 20})
        csv_path = _write_csv("id,visibility\n10,hidden\n20,full\n30,secret\n")
        try:
            with pytest.raises(SystemExit):
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            output = capsys.readouterr().out
            assert "Row 4" in output
        finally:
            os.unlink(csv_path)


# ---------------------------------------------------------------------------
# Set CSV: successful apply
# ---------------------------------------------------------------------------


class TestSetCsvApply:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_applies_visibility_policies(self, mock_db, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10, 42})
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility\n10,hidden\n42,opaque\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert mock_set_vis.call_count == 2
            mock_set_vis.assert_any_call(conn, "email:10", "hidden", commit=False)
            mock_set_vis.assert_any_call(conn, "email:42", "opaque", commit=False)
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_applies_access_policies(self, mock_db, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10, 42})
        mock_db.return_value = conn

        csv_path = _write_csv("id,access\n10,deny\n42,allow\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            assert mock_set_perm.call_count == 2
            mock_set_perm.assert_any_call(conn, "email:10", "deny", commit=False)
            mock_set_perm.assert_any_call(conn, "email:42", "allow", commit=False)
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_applies_both_policies(self, mock_db, mock_set_vis, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10})
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility,access\n10,hidden,deny\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            mock_set_vis.assert_called_once_with(conn, "email:10", "hidden", commit=False)
            mock_set_perm.assert_called_once_with(conn, "email:10", "deny", commit=False)
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_skips_empty_cells(self, mock_db, mock_set_vis, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10})
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility,access\n10,hidden,\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            mock_set_vis.assert_called_once_with(conn, "email:10", "hidden", commit=False)
            mock_set_perm.assert_not_called()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_recalculates_once(self, mock_db, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10, 42})
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility\n10,hidden\n42,opaque\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            mock_recalc.assert_called_once_with(conn, "source:emails", commit=False)
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_conn_closed(self, mock_db, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10})
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility\n10,hidden\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            conn.close.assert_called_once()
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_prints_summary(self, mock_db, mock_set_vis, mock_recalc, capsys):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn_with_id_check({10, 42})
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility\n10,hidden\n42,opaque\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))
            output = capsys.readouterr().out
            assert "2" in output
        finally:
            os.unlink(csv_path)


# ---------------------------------------------------------------------------
# Fixture: real DB for integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def policy_db(tmp_path):
    """Real SQLite DB with schema + test entities for transaction and round-trip tests.

    Yields (conn, db_path).  _set closes conn in its finally block,
    so tests that need to verify post-call state should re-open from db_path.
    """
    from footprinter.ingest.database import Database

    db_path = tmp_path / "policy_test.db"
    db = Database(str(db_path))
    db.conn.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute(
        """INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled)
           VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"""
    )
    conn.execute(
        """INSERT INTO emails (id, message_id, thread_id, account, from_address, from_name,
                               to_addresses, subject, body_preview, received_at,
                               labels, status, visibility, access)
           VALUES
               (1, 'msg-1', 'thr-1', 'work', 'a@test.com', 'Alice',
                'b@test.com', 'Subj 1', 'Body 1', '2026-01-15T10:00:00',
                'inbox', 'listed', 'full', 'allow'),
               (2, 'msg-2', 'thr-2', 'work', 'b@test.com', 'Bob',
                'a@test.com', 'Subj 2', 'Body 2', '2026-01-15T11:00:00',
                'inbox', 'listed', 'opaque', 'allow'),
               (3, 'msg-3', 'thr-3', 'work', 'c@test.com', 'Carol',
                'a@test.com', 'Subj 3', 'Body 3', '2026-01-15T12:00:00',
                'inbox', 'listed', 'full', 'deny')"""
    )
    conn.commit()
    yield conn, db_path


# ---------------------------------------------------------------------------
# Set CSV: transaction atomicity
# ---------------------------------------------------------------------------


class TestSetCsvTransactionAtomicity:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_mid_write_failure_rolls_back(self, mock_db, mock_recalc, policy_db):
        from footprinter.cli.permission_cmd import _set

        conn, db_path = policy_db
        mock_db.return_value = conn
        call_count = 0
        original = __import__("footprinter.db.policies", fromlist=["set_visibility_policy"]).set_visibility_policy

        def explode_on_third(conn, scope, setting, *, commit=True):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError("simulated write failure")
            return original(conn, scope, setting, commit=commit)

        csv_path = _write_csv("id,visibility\n1,hidden\n2,opaque\n3,full\n")
        try:
            with patch("footprinter.cli.permission_cmd.set_visibility_policy", side_effect=explode_on_third):
                with pytest.raises(RuntimeError, match="simulated write failure"):
                    _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))

            verify = sqlite3.connect(str(db_path))
            verify.row_factory = sqlite3.Row
            rows = verify.execute("SELECT * FROM visibility_policies").fetchall()
            verify.close()
            assert len(rows) == 0, f"Expected rollback to clear all rows, found {len(rows)}"
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_successful_bulk_commits_all(self, mock_db, mock_recalc, policy_db):
        from footprinter.cli.permission_cmd import _set

        conn, db_path = policy_db
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility\n1,hidden\n2,opaque\n")
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))

            verify = sqlite3.connect(str(db_path))
            verify.row_factory = sqlite3.Row
            rows = verify.execute(
                "SELECT scope, setting FROM visibility_policies ORDER BY scope"
            ).fetchall()
            verify.close()
            assert len(rows) == 2
            by_scope = {r["scope"]: r["setting"] for r in rows}
            assert by_scope["email:1"] == "hidden"
            assert by_scope["email:2"] == "opaque"
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress",
           side_effect=RuntimeError("recalc boom"))
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_recalc_failure_rolls_back_policies(self, mock_db, mock_recalc, policy_db):
        from footprinter.cli.permission_cmd import _set

        conn, db_path = policy_db
        mock_db.return_value = conn

        csv_path = _write_csv("id,visibility\n1,hidden\n2,opaque\n")
        try:
            with pytest.raises(RuntimeError, match="recalc boom"):
                _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))

            verify = sqlite3.connect(str(db_path))
            verify.row_factory = sqlite3.Row
            rows = verify.execute("SELECT * FROM visibility_policies").fetchall()
            verify.close()
            assert len(rows) == 0, f"Expected rollback to undo writes, found {len(rows)}"
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_recalc_failure_rolls_back_entity_stamps(self, mock_db, policy_db):
        """Entity-table writes made during recalculation are also rolled back."""
        from footprinter.cli.permission_cmd import _set

        conn, db_path = policy_db
        mock_db.return_value = conn

        original_vis = {
            r["id"]: r["visibility"]
            for r in conn.execute("SELECT id, visibility FROM emails").fetchall()
        }

        def recalc_writes_then_explodes(conn, scope, *, commit=True):
            conn.execute("UPDATE emails SET visibility = 'hidden' WHERE id = 1")
            raise RuntimeError("recalc mid-flight failure")

        csv_path = _write_csv("id,visibility\n1,hidden\n2,opaque\n")
        try:
            with patch(
                "footprinter.cli.permission_cmd.recalculate_with_progress",
                side_effect=recalc_writes_then_explodes,
            ):
                with pytest.raises(RuntimeError, match="recalc mid-flight failure"):
                    _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))

            verify = sqlite3.connect(str(db_path))
            verify.row_factory = sqlite3.Row
            policy_rows = verify.execute("SELECT * FROM visibility_policies").fetchall()
            entity_rows = {
                r["id"]: r["visibility"]
                for r in verify.execute("SELECT id, visibility FROM emails").fetchall()
            }
            verify.close()
            assert len(policy_rows) == 0, f"Policy writes should be rolled back, found {len(policy_rows)}"
            assert entity_rows == original_vis, "Entity-table stamps should be rolled back"
        finally:
            os.unlink(csv_path)


# ---------------------------------------------------------------------------
# Set CSV: export round-trip
# ---------------------------------------------------------------------------


@contextmanager
def _open_db_stub(conn: sqlite3.Connection):
    """Yield *conn* as a context manager without closing it on exit."""
    try:
        yield conn
    finally:
        pass


def _export_email_csv(conn: sqlite3.Connection) -> str:
    """Call the real export path and return CSV stdout."""
    with patch("footprinter.cli.view.open_db", side_effect=lambda: _open_db_stub(conn)):
        stdout, _, code = run_fp("view", "emails", "--csv", "--all")
    assert code == 0, f"Export failed with code {code}"
    return stdout


def _rebuild_csv(fieldnames: list[str], rows: list[dict]) -> str:
    """Write rows back to a CSV string with the given column order."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


class TestSetCsvExportRoundTrip:
    """Verify that real export CSV round-trips through _set_csv."""

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_email_ids_roundtrip(self, mock_db, mock_recalc, policy_db):
        from footprinter.cli.permission_cmd import _set

        conn, db_path = policy_db

        csv_text = _export_email_csv(conn)
        reader = csv.DictReader(io.StringIO(csv_text))
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

        assert "id" in fieldnames
        assert "visibility" in fieldnames
        assert len(rows) == 3

        for row in rows:
            row["visibility"] = "hidden"

        mock_db.return_value = conn
        csv_path = _write_csv(_rebuild_csv(fieldnames, rows))
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))

            verify = sqlite3.connect(str(db_path))
            verify.row_factory = sqlite3.Row
            policies = verify.execute(
                "SELECT scope, setting FROM visibility_policies ORDER BY scope"
            ).fetchall()
            verify.close()

            by_scope = {r["scope"]: r["setting"] for r in policies}
            for row in rows:
                assert by_scope[f"email:{row['id']}"] == "hidden"
        finally:
            os.unlink(csv_path)

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_roundtrip_mixed_settings(self, mock_db, mock_recalc, policy_db):
        from footprinter.cli.permission_cmd import _set

        conn, db_path = policy_db

        csv_text = _export_email_csv(conn)
        reader = csv.DictReader(io.StringIO(csv_text))
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

        assert "id" in fieldnames
        assert "visibility" in fieldnames
        assert "access" in fieldnames

        settings = {"1": "hidden", "2": "opaque", "3": "full"}
        for row in rows:
            row["visibility"] = settings[row["id"]]
            row["access"] = "deny"

        mock_db.return_value = conn
        csv_path = _write_csv(_rebuild_csv(fieldnames, rows))
        try:
            _set(Namespace(scope="source:emails", csv_file=csv_path, visibility=None, access=None))

            verify = sqlite3.connect(str(db_path))
            verify.row_factory = sqlite3.Row
            vis_rows = verify.execute(
                "SELECT scope, setting FROM visibility_policies ORDER BY scope"
            ).fetchall()
            perm_rows = verify.execute(
                "SELECT scope, setting FROM permission_policies ORDER BY scope"
            ).fetchall()
            verify.close()

            vis_by_scope = {r["scope"]: r["setting"] for r in vis_rows}
            perm_by_scope = {r["scope"]: r["setting"] for r in perm_rows}
            for eid, vis in settings.items():
                assert vis_by_scope[f"email:{eid}"] == vis
                assert perm_by_scope[f"email:{eid}"] == "deny"
        finally:
            os.unlink(csv_path)


# ---------------------------------------------------------------------------
# _lookup_scope_policy helper
# ---------------------------------------------------------------------------


class TestLookupScopePolicy:
    def _make_conn(self, perm_setting, vis_setting):
        conn = _mock_conn()

        def fake_execute(sql, params=None):
            cursor = MagicMock()
            if "permission_policies" in sql:
                cursor.fetchone.return_value = (
                    {"setting": perm_setting} if perm_setting is not None else None
                )
            elif "visibility_policies" in sql:
                cursor.fetchone.return_value = (
                    {"setting": vis_setting} if vis_setting is not None else None
                )
            else:
                cursor.fetchone.return_value = None
            return cursor

        conn.execute = MagicMock(side_effect=fake_execute)
        return conn

    def test_returns_both_when_both_exist(self):
        from footprinter.cli._policy_helpers import _lookup_scope_policy

        conn = self._make_conn("allow", "full")
        result = _lookup_scope_policy(conn, "project:3")

        assert result == {"scope": "project:3", "permission": "allow", "visibility": "full"}

    def test_returns_none_when_no_policies(self):
        from footprinter.cli._policy_helpers import _lookup_scope_policy

        conn = self._make_conn(None, None)
        result = _lookup_scope_policy(conn, "project:99")

        assert result == {"scope": "project:99", "permission": None, "visibility": None}

    def test_returns_perm_only(self):
        from footprinter.cli._policy_helpers import _lookup_scope_policy

        conn = self._make_conn("deny", None)
        result = _lookup_scope_policy(conn, "source:files")

        assert result == {"scope": "source:files", "permission": "deny", "visibility": None}

    def test_returns_vis_only(self):
        from footprinter.cli._policy_helpers import _lookup_scope_policy

        conn = self._make_conn(None, "metadata")
        result = _lookup_scope_policy(conn, "global")

        assert result == {"scope": "global", "permission": None, "visibility": "metadata"}


# ---------------------------------------------------------------------------
# resolve_file_id / resolve_folder_id helpers
# ---------------------------------------------------------------------------


class TestResolveFileId:
    def test_returns_path_when_listed(self, capsys):
        from footprinter.cli._policy_helpers import resolve_file_id

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"path": "/home/user/test.py", "status": "listed"}
        conn.execute.return_value = cursor

        result = resolve_file_id(conn, 42)

        assert result == "/home/user/test.py"

    def test_returns_none_when_not_found(self, capsys):
        from footprinter.cli._policy_helpers import resolve_file_id

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor

        result = resolve_file_id(conn, 99)

        assert result is None
        output = capsys.readouterr().out
        assert "not found" in output.lower()

    def test_returns_none_when_not_listed(self, capsys):
        from footprinter.cli._policy_helpers import resolve_file_id

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"path": "/home/user/deleted.py", "status": "unlisted"}
        conn.execute.return_value = cursor

        result = resolve_file_id(conn, 50)

        assert result is None
        output = capsys.readouterr().out
        assert "status" in output.lower()
        assert "unlisted" in output


class TestResolveFolderId:
    def test_returns_path_when_found(self):
        from footprinter.cli._policy_helpers import resolve_folder_id

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = {"path": "/home/user/Work"}
        conn.execute.return_value = cursor

        result = resolve_folder_id(conn, 42)

        assert result == "/home/user/Work"

    def test_returns_none_when_not_found(self, capsys):
        from footprinter.cli._policy_helpers import resolve_folder_id

        conn = _mock_conn()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        conn.execute.return_value = cursor

        result = resolve_folder_id(conn, 99)

        assert result is None
        output = capsys.readouterr().out
        assert "not found" in output.lower()
