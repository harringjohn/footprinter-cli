"""Tests for policy-change → recalculate_access triggers.

Every CLI command that modifies a policy must call recalculate_access()
afterward so cached entity columns stay in sync.
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_STATS = {"file": 5, "email": 2}
"""Return value for the mocked recalculate_access."""

MOCK_LARGE_COUNTS = {"file": 200, "email": 50}
"""count_affected_entities return value simulating >100 entities."""

MOCK_SMALL_COUNTS = {"file": 3}
"""count_affected_entities return value simulating ≤100 entities."""


def _mock_conn():
    """A mock connection that survives .close() calls."""
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Set handler triggers recalculate
# ---------------------------------------------------------------------------


class TestSetTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_visibility_set_calls_recalculate(self, mock_db, mock_set_vis, mock_recalc):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="project:3", visibility="hidden", permission=None)
        _set(args)

        mock_recalc.assert_called_once_with(conn, "project:3")

    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_permission_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_permission_set_calls_recalculate(self, mock_db, mock_set_perm, mock_recalc):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="folder:~/Work", visibility=None, permission="deny")
        _set(args)

        mock_recalc.assert_called_once_with(conn, "folder:~/Work")

    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_permission_policy")
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_both_set_calls_recalculate_once(self, mock_db, mock_set_vis, mock_set_perm, mock_recalc):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="global", visibility="visible", permission="allow")
        _set(args)

        mock_recalc.assert_called_once_with(conn, "global")


# ---------------------------------------------------------------------------
# Reset handler triggers recalculate
# ---------------------------------------------------------------------------


class TestResetScopeTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_on_reset(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc):
        from footprinter.cli.mcp_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()
        mock_db.return_value = conn

        args = Namespace(scope="project:3", all=False)
        _reset(args)

        mock_recalc.assert_called_once_with(conn, "project:3")

    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_skips_recalculate_when_no_policies_exist(self, mock_db, mock_del_perm, mock_del_vis, mock_recalc):
        from footprinter.cli.mcp_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        args = Namespace(scope="project:99", all=False)
        _reset(args)

        mock_recalc.assert_not_called()
        mock_del_vis.assert_not_called()
        mock_del_perm.assert_not_called()


class TestResetAllTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.seed_permission_defaults")
    @patch("footprinter.cli.mcp_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.mcp_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.mcp_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_global(
        self, mock_db, mock_clear_vis, mock_clear_perm, mock_seed_vis, mock_seed_perm, mock_recalc,
    ):
        from footprinter.cli.mcp_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope=None, all=True, yes=False)
        _reset(args)

        mock_recalc.assert_called_once_with(conn, "global")


# ---------------------------------------------------------------------------
# Stats output
# ---------------------------------------------------------------------------


class TestPrintRecalcStatsPluralization:
    def test_singular_no_trailing_s(self, capsys):
        from footprinter.cli.mcp_cmd import _print_recalc_stats

        _print_recalc_stats({"file": 1})
        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output

    def test_plural_has_trailing_s(self, capsys):
        from footprinter.cli.mcp_cmd import _print_recalc_stats

        _print_recalc_stats({"file": 5})
        output = capsys.readouterr().out
        assert "5 files" in output

    def test_mixed_singular_and_plural(self, capsys):
        from footprinter.cli.mcp_cmd import _print_recalc_stats

        _print_recalc_stats({"file": 1, "email": 3})
        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output
        assert "3 emails" in output

    def test_zero_count_excluded(self, capsys):
        from footprinter.cli.mcp_cmd import _print_recalc_stats

        _print_recalc_stats({"file": 0, "email": 2})
        output = capsys.readouterr().out
        assert "file" not in output.lower().split("email")[0]
        assert "2 emails" in output


# ---------------------------------------------------------------------------
# Set never prompts (recalculation is non-destructive)
# ---------------------------------------------------------------------------


class TestSetNeverPrompts:
    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli.mcp_cmd.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_without_yes_never_prompts(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", visibility="hidden", permission=None)
        _set(args)

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()


# ---------------------------------------------------------------------------
# Set handler: entity count preview
# ---------------------------------------------------------------------------


class TestSetEntityCountPreview:
    @patch("footprinter.cli.mcp_cmd.count_affected_entities", return_value={"file": 5, "folder": 2})
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_preview_shows_pluralized_counts(self, mock_db, mock_set_vis, mock_recalc, mock_count, capsys):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", visibility="hidden", permission=None)
        _set(args)

        captured = capsys.readouterr().out
        assert "5 files" in captured
        assert "2 folders" in captured

    @patch("footprinter.cli.mcp_cmd.count_affected_entities", return_value={"file": 1})
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_preview_shows_singular_count(self, mock_db, mock_set_vis, mock_recalc, mock_count, capsys):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", visibility="hidden", permission=None)
        _set(args)

        captured = capsys.readouterr().out
        assert "1 file" in captured
        assert "1 files" not in captured


# ---------------------------------------------------------------------------
# Set handler: no confirmation prompts (non-destructive recalculation)
# ---------------------------------------------------------------------------


class TestSetNoConfirmationPrompt:
    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli.mcp_cmd.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_no_confirmation(self, mock_db, mock_set_vis, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", visibility="hidden", permission=None)
        _set(args)

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()


# ---------------------------------------------------------------------------
# Individual scope reset: no confirmation prompt
# ---------------------------------------------------------------------------


class TestResetScopeNoConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_scope_reset_executes_without_prompting(
        self, mock_db, mock_del_perm, mock_del_vis, mock_recalc, mock_confirm,
    ):
        from footprinter.cli.mcp_cmd import _reset

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()
        mock_db.return_value = conn
        args = Namespace(scope="global", all=False)
        _reset(args)

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()


class TestResetAllConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.seed_permission_defaults")
    @patch("footprinter.cli.mcp_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.mcp_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.mcp_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(
        self, mock_db, mock_clear_vis, mock_clear_perm, mock_seed_vis,
        mock_seed_perm, mock_recalc, mock_count, mock_confirm,
    ):
        from footprinter.cli.mcp_cmd import _reset

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope=None, all=True, yes=False)
        _reset(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()


class TestRecalculateStatsPrinted:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_prints_recalc_stats(self, mock_db, mock_set, mock_recalc, capsys):
        from footprinter.cli.mcp_cmd import _set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="project:3", visibility="hidden", permission=None)
        _set(args)

        output = capsys.readouterr().out
        assert "5" in output or "file" in output.lower()


# ---------------------------------------------------------------------------
# Confirmation helper pluralization
# ---------------------------------------------------------------------------


class TestConfirmRecalculationPluralization:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch(
        "footprinter.cli._policy_helpers.count_affected_entities",
        return_value={"file": 1, "email": 150},
    )
    def test_singular_entity_type_no_trailing_s(self, mock_count, mock_confirm, capsys):
        from footprinter.cli._policy_helpers import confirm_recalculation

        conn = _mock_conn()
        confirm_recalculation(conn, "global")

        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output

    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch(
        "footprinter.cli._policy_helpers.count_affected_entities",
        return_value={"file": 5, "email": 150},
    )
    def test_plural_entity_type_has_trailing_s(self, mock_count, mock_confirm, capsys):
        from footprinter.cli._policy_helpers import confirm_recalculation

        conn = _mock_conn()
        confirm_recalculation(conn, "global")

        output = capsys.readouterr().out
        assert "5 files" in output
        assert "150 emails" in output


