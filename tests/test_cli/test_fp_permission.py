"""Tests for fp permission subcommands (list, set, reset, check).

Covers:
  - Parser tree: help exits 0 for all subcommands
  - List: show all policies with target terminology
  - Set: unified policy setter (--visibility / --access / --dry-run)
  - Reset: policy delete / reseed with confirmation changes
  - Check: resolve target with required argument
"""

from argparse import Namespace
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

    def test_bare_permission_shows_help(self):
        stdout, stderr, code = run_fp("permission")
        assert code == 0
        combined = stdout + stderr
        assert "list" in combined
        assert "set" in combined
        assert "reset" in combined
        assert "check" in combined
        assert "recalculate" in combined


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
            _set(Namespace(scope="global", visibility=None, access=None, dry_run=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_invalid_access_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility=None, access="hidden", dry_run=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_invalid_visibility_fails(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility="allow", access=None, dry_run=False))

        assert exc_info.value.code == 1


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

        _set(Namespace(scope="global", visibility="full", access=None, dry_run=False))

        mock_set_vis.assert_called_once_with(conn, "global", "full")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_visibility_opaque_passes_through(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="opaque", access=None, dry_run=False))

        mock_set_vis.assert_called_once_with(conn, "global", "opaque")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_visibility_hidden_passes_through(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="hidden", access=None, dry_run=False))

        mock_set_vis.assert_called_once_with(conn, "global", "hidden")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_permission_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_access_maps_to_set_permission_policy(self, mock_db, mock_count, mock_set_perm, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="folder:~/Work", visibility=None, access="deny", dry_run=False))

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

        _set(Namespace(scope="global", visibility="full", access="allow", dry_run=False))

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

        _set(Namespace(scope="global", visibility="full", access="allow", dry_run=False))

        mock_recalc.assert_called_once_with(conn, "global")

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_set_no_db_exits_1(self, mock_db):
        from footprinter.cli.permission_cmd import _set

        with pytest.raises(SystemExit) as exc_info:
            _set(Namespace(scope="global", visibility="full", access=None, dry_run=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 3})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_set_conn_closed_after_success(self, mock_db, mock_count, mock_set_vis, mock_recalc):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="global", visibility="full", access=None, dry_run=False))

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
            _set(Namespace(scope="global", visibility="full", access=None, dry_run=False))

        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Set subcommand: dry-run
# ---------------------------------------------------------------------------


class TestSetDryRun:
    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 10})
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_dry_run_skips_policy_write(self, mock_db, mock_set_vis, mock_recalc, mock_count):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="folder:~/Work", visibility="hidden", access=None, dry_run=True))

        mock_set_vis.assert_not_called()

    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 10})
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_dry_run_skips_recalculate(self, mock_db, mock_set_vis, mock_recalc, mock_count):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="folder:~/Work", visibility="hidden", access=None, dry_run=True))

        mock_recalc.assert_not_called()

    @patch("footprinter.cli.permission_cmd.count_affected_entities", return_value={"file": 10})
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.set_visibility_policy")
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_dry_run_prints_message(self, mock_db, mock_set_vis, mock_recalc, mock_count, capsys):
        from footprinter.cli.permission_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        _set(Namespace(scope="folder:~/Work", visibility="hidden", access=None, dry_run=True))

        output = capsys.readouterr().out
        assert "Dry run" in output


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

        _set(Namespace(scope="global", visibility="hidden", access=None, dry_run=True))

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

        _set(Namespace(scope="global", visibility="hidden", access=None, dry_run=True))

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

        _set(Namespace(scope="global", visibility="hidden", access=None, dry_run=False))

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


class TestCheckRouting:
    @patch("footprinter.cli.permission_cmd.check_file_path", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_path_calls_check_file_path(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(path="~/Work/file.py", folder=None, project=None, client=None, json=False, verbose=False))

        mock_check.assert_called_once_with(conn, "~/Work/file.py", False, False)

    @patch("footprinter.cli.permission_cmd.check_folder", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_folder_calls_check_folder(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(path=None, folder="~/Work", project=None, client=None, json=False, verbose=True))

        mock_check.assert_called_once_with(conn, "~/Work", False, True)

    @patch("footprinter.cli.permission_cmd.check_project", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_project_calls_check_project(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(path=None, folder=None, project=3, client=None, json=False, verbose=True))

        mock_check.assert_called_once_with(conn, 3, False, True)

    @patch("footprinter.cli.permission_cmd.check_client", return_value=0)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_client_calls_check_client(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        _check(Namespace(path=None, folder=None, project=None, client=5, json=False, verbose=True))

        mock_check.assert_called_once_with(conn, 5, False, True)

    def test_check_no_target_exits_1(self):
        from footprinter.cli.permission_cmd import _check

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(path=None, folder=None, project=None, client=None, json=False, verbose=False))

        assert exc_info.value.code == 1

    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_multiple_targets_exits_1(self, mock_db):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(SystemExit) as exc_info:
            _check(Namespace(path="~/file.py", folder="~/Work", project=None, client=None, json=False, verbose=False))

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Check subcommand: no DB
# ---------------------------------------------------------------------------


class TestCheckNoDb:
    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_check_no_db_shows_baseline(self, mock_db, capsys):
        from footprinter.cli.permission_cmd import _check

        _check(Namespace(path="~/Work/file.py", folder=None, project=None, client=None, json=False, verbose=False))

        output = capsys.readouterr().out
        assert "Access" in output
        assert "baseline" in output.lower()

    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_check_no_db_json(self, mock_db, capsys):
        import json

        from footprinter.cli.permission_cmd import _check

        _check(Namespace(path="~/Work/file.py", folder=None, project=None, client=None, json=True, verbose=False))

        output = capsys.readouterr().out
        data = json.loads(output)
        assert "permission" in data
        assert data["found_in_db"] is False


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

        _check(Namespace(path="~/Work/file.py", folder=None, project=None, client=None, json=False, verbose=False))

        conn.close.assert_called_once()

    @patch("footprinter.cli.permission_cmd.check_file_path", side_effect=RuntimeError("boom"))
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_check_conn_closed_on_error(self, mock_db, mock_check):
        from footprinter.cli.permission_cmd import _check

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(RuntimeError):
            _check(Namespace(path="~/Work/file.py", folder=None, project=None, client=None, json=False, verbose=False))

        conn.close.assert_called_once()


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
