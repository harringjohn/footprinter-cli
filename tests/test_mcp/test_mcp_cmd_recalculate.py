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
# View handlers (visibility)
# ---------------------------------------------------------------------------


class TestViewSetTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_with_scope(self, mock_db, mock_set, mock_recalc):
        from footprinter.cli.mcp_cmd import _view_set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="project:3", level="hidden")
        _view_set(args)

        mock_recalc.assert_called_once_with(conn, "project:3")


class TestViewDeleteTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_on_delete(self, mock_db, mock_del, mock_recalc):
        from footprinter.cli.mcp_cmd import _view_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()  # truthy: policy exists
        mock_db.return_value = conn

        args = Namespace(scope="project:3")
        _view_delete(args)

        mock_recalc.assert_called_once_with(conn, "project:3")

    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=False)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_skips_recalculate_when_nothing_deleted(self, mock_db, mock_del, mock_recalc):
        from footprinter.cli.mcp_cmd import _view_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        args = Namespace(scope="project:99")
        _view_delete(args)

        mock_recalc.assert_not_called()


class TestViewDeleteSkipsConfirmWhenNoPolicyExists:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value={})
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=False)
    @patch("footprinter.cli.mcp_cmd.confirm_recalculation", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_no_confirm_when_policy_missing(self, mock_db, mock_confirm, mock_del, mock_recalc):
        from footprinter.cli.mcp_cmd import _view_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        args = Namespace(scope="project:99")
        _view_delete(args)

        mock_confirm.assert_not_called()
        mock_del.assert_not_called()


class TestViewResetTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.mcp_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_global(self, mock_db, mock_clear, mock_seed, mock_recalc):
        from footprinter.cli.mcp_cmd import _view_reset

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace()
        _view_reset(args)

        mock_recalc.assert_called_once_with(conn, "global")


# ---------------------------------------------------------------------------
# Read handlers (permission)
# ---------------------------------------------------------------------------


class TestReadSetTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_permission_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_with_scope(self, mock_db, mock_set, mock_recalc):
        from footprinter.cli.mcp_cmd import _read_set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="folder:~/Work", level="deny")
        _read_set(args)

        mock_recalc.assert_called_once_with(conn, "folder:~/Work")


class TestReadDeleteTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_on_delete(self, mock_db, mock_del, mock_recalc):
        from footprinter.cli.mcp_cmd import _read_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()  # truthy: policy exists
        mock_db.return_value = conn

        args = Namespace(scope="folder:~/Work")
        _read_delete(args)

        mock_recalc.assert_called_once_with(conn, "folder:~/Work")

    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=False)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_skips_recalculate_when_nothing_deleted(self, mock_db, mock_del, mock_recalc):
        from footprinter.cli.mcp_cmd import _read_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        args = Namespace(scope="folder:~/Nowhere")
        _read_delete(args)

        mock_recalc.assert_not_called()


class TestReadDeleteSkipsConfirmWhenNoPolicyExists:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value={})
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=False)
    @patch("footprinter.cli.mcp_cmd.confirm_recalculation", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_no_confirm_when_policy_missing(self, mock_db, mock_confirm, mock_del, mock_recalc):
        from footprinter.cli.mcp_cmd import _read_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value = conn

        args = Namespace(scope="folder:~/Nowhere")
        _read_delete(args)

        mock_confirm.assert_not_called()
        mock_del.assert_not_called()


class TestReadResetTriggersRecalculate:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.seed_permission_defaults")
    @patch("footprinter.cli.mcp_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_calls_recalculate_global(self, mock_db, mock_clear, mock_seed, mock_recalc):
        from footprinter.cli.mcp_cmd import _read_reset

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace()
        _read_reset(args)

        mock_recalc.assert_called_once_with(conn, "global")


# ---------------------------------------------------------------------------
# Bulk handler
# ---------------------------------------------------------------------------


class TestBulkTriggersRecalculate:
    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli._policy_helpers.set_visibility_policy")
    @patch("footprinter.cli._policy_helpers.set_permission_policy")
    def test_calls_recalculate_with_folder_scope(self, mock_set_perm, mock_set_vis, mock_recalc, tool_db):
        from footprinter.cli._policy_helpers import bulk_apply

        # Insert a file so the count > 0
        tool_db.execute(
            "INSERT INTO files (id, source, name, path, account, status) "
            "VALUES (1, 'local', 'a.py', '/Users/me/Work/a.py', 'work', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder="~/Work",
            project=None,
            permission="allow",
            visibility="visible",
            dry_run=False,
            yes=True,
        )

        mock_recalc.assert_called_once_with(tool_db, "folder:~/Work")

    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli._policy_helpers.set_visibility_policy")
    def test_skips_recalculate_on_dry_run(self, mock_set_vis, mock_recalc, tool_db):
        from footprinter.cli._policy_helpers import bulk_apply

        tool_db.execute(
            "INSERT INTO files (id, source, name, path, account, status) "
            "VALUES (1, 'local', 'a.py', '/Users/me/Work/a.py', 'work', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder="~/Work",
            project=None,
            permission=None,
            visibility="hidden",
            dry_run=True,
            yes=True,
        )

        mock_recalc.assert_not_called()


# ---------------------------------------------------------------------------
# Bulk per-type display & threshold confirmation
# ---------------------------------------------------------------------------


class TestBulkPerTypeDisplay:
    """bulk_apply() should show per-entity-type counts, not just files."""

    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value={"file": 5, "folder": 2})
    @patch("footprinter.cli._policy_helpers.set_visibility_policy")
    @patch("footprinter.cli._policy_helpers.set_permission_policy")
    def test_bulk_folder_shows_per_type_counts(
        self, mock_set_perm, mock_set_vis, mock_count, mock_recalc, tool_db, capsys
    ):
        from footprinter.cli._policy_helpers import bulk_apply

        # Insert a file so the folder scope resolves
        tool_db.execute(
            "INSERT INTO files (id, source, name, path, account, status) "
            "VALUES (1, 'local', 'a.py', '/Users/me/Work/a.py', 'work', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder="~/Work",
            project=None,
            permission="allow",
            visibility="visible",
            dry_run=False,
            yes=True,
        )

        output = capsys.readouterr().out
        # Should show per-type breakdown, not just "N files affected"
        assert "files affected" not in output.lower()
        assert "file" in output.lower()
        assert "folder" in output.lower()

    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value=MOCK_STATS)
    @patch(
        "footprinter.cli._policy_helpers.count_affected_entities", return_value={"file": 3, "email": 2, "project": 1}
    )
    @patch("footprinter.cli._policy_helpers.set_visibility_policy")
    @patch("footprinter.cli._policy_helpers.set_permission_policy")
    def test_bulk_project_shows_per_type_counts(
        self, mock_set_perm, mock_set_vis, mock_count, mock_recalc, tool_db, capsys
    ):
        from footprinter.cli._policy_helpers import bulk_apply

        # Insert a project so the scope resolves
        tool_db.execute(
            "INSERT INTO projects (id, project_name, project_type, root_path, status) "
            "VALUES (1, 'TestProj', 'python', '/Users/me/Work/test', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder=None,
            project=1,
            permission="allow",
            visibility="visible",
            dry_run=False,
            yes=True,
        )

        output = capsys.readouterr().out
        # Should show per-type breakdown including emails
        assert "files affected" not in output.lower()
        assert "email" in output.lower()


class TestBulkThresholdConfirmation:
    """bulk_apply() should use CONFIRM_THRESHOLD like confirm_recalculation()."""

    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_SMALL_COUNTS)
    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli._policy_helpers.set_permission_policy")
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    def test_bulk_small_scope_skips_confirmation(self, mock_confirm, mock_set_perm, mock_recalc, mock_count, tool_db):
        from footprinter.cli._policy_helpers import bulk_apply

        # Insert a project so scope resolves
        tool_db.execute(
            "INSERT INTO projects (id, project_name, project_type, root_path, status) "
            "VALUES (1, 'SmallProj', 'python', '/Users/me/Work/small', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder=None,
            project=1,
            permission="allow",
            visibility=None,
            dry_run=False,
            yes=False,
        )

        # Small scope (total <= 100) should NOT prompt
        mock_confirm.assert_not_called()

    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli._policy_helpers.set_permission_policy")
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    def test_bulk_large_scope_prompts_confirmation(
        self, mock_confirm, mock_set_perm, mock_recalc, mock_count, tool_db, capsys
    ):
        from footprinter.cli._policy_helpers import bulk_apply

        # Insert a project so scope resolves
        tool_db.execute(
            "INSERT INTO projects (id, project_name, project_type, root_path, status) "
            "VALUES (1, 'BigProj', 'python', '/Users/me/Work/big', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder=None,
            project=1,
            permission="allow",
            visibility=None,
            dry_run=False,
            yes=False,
        )

        # Large scope (total > 100) should prompt
        mock_confirm.assert_called_once()
        # Output should show per-type breakdown, not "files affected"
        output = capsys.readouterr().out
        assert "files affected" not in output.lower()


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


class TestBulkApplyStatsPluralization:
    @patch("footprinter.cli._policy_helpers.recalculate_with_progress", return_value={"file": 1})
    @patch("footprinter.cli._policy_helpers.set_visibility_policy")
    @patch("footprinter.cli._policy_helpers.set_permission_policy")
    def test_singular_in_bulk_output(self, mock_set_perm, mock_set_vis, mock_recalc, tool_db, capsys):
        from footprinter.cli._policy_helpers import bulk_apply

        tool_db.execute(
            "INSERT INTO files (id, source, name, path, account, status) "
            "VALUES (1, 'local', 'a.py', '/Users/me/Work/a.py', 'work', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder="~/Work",
            project=None,
            permission="allow",
            visibility="visible",
            dry_run=False,
            yes=True,
        )

        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output


# ---------------------------------------------------------------------------
# Confirmation UX
# ---------------------------------------------------------------------------


class TestViewSetConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _view_set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", level="hidden", yes=False)
        _view_set(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()

    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=False)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_cancelled_skips_recalculate(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _view_set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", level="hidden", yes=False)
        _view_set(args)

        mock_confirm.assert_called_once()
        mock_set.assert_not_called()
        mock_recalc.assert_not_called()

    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_SMALL_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_small_scope_skips_confirmation(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _view_set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="file:42", level="hidden", yes=False)
        _view_set(args)

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()

    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_yes_flag_skips_confirmation(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _view_set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", level="hidden", yes=True)
        _view_set(args)

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()


class TestViewDeleteConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(self, mock_db, mock_del, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _view_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()  # truthy: policy exists
        mock_db.return_value = conn
        args = Namespace(scope="global", yes=False)
        _view_delete(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()

    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=False)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_visibility_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_cancelled_skips_recalculate(self, mock_db, mock_del, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _view_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()  # truthy: policy exists
        mock_db.return_value = conn
        args = Namespace(scope="global", yes=False)
        _view_delete(args)

        mock_confirm.assert_called_once()
        mock_del.assert_not_called()
        mock_recalc.assert_not_called()


class TestViewResetConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.seed_visibility_defaults")
    @patch("footprinter.cli.mcp_cmd.clear_visibility_policies", return_value=3)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(
        self, mock_db, mock_clear, mock_seed, mock_recalc, mock_count, mock_confirm
    ):
        from footprinter.cli.mcp_cmd import _view_reset

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(yes=False)
        _view_reset(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()


class TestReadSetConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_permission_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _read_set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", level="deny", yes=False)
        _read_set(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()

    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=False)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_permission_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_cancelled_skips_recalculate(self, mock_db, mock_set, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _read_set

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(scope="global", level="deny", yes=False)
        _read_set(args)

        mock_confirm.assert_called_once()
        mock_set.assert_not_called()
        mock_recalc.assert_not_called()


class TestReadDeleteConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(self, mock_db, mock_del, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _read_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()  # truthy: policy exists
        mock_db.return_value = conn
        args = Namespace(scope="global", yes=False)
        _read_delete(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()

    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=False)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.delete_permission_policy", return_value=True)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_cancelled_skips_recalculate(self, mock_db, mock_del, mock_recalc, mock_count, mock_confirm):
        from footprinter.cli.mcp_cmd import _read_delete

        conn = _mock_conn()
        conn.execute.return_value.fetchone.return_value = MagicMock()  # truthy: policy exists
        mock_db.return_value = conn
        args = Namespace(scope="global", yes=False)
        _read_delete(args)

        mock_confirm.assert_called_once()
        mock_del.assert_not_called()
        mock_recalc.assert_not_called()


class TestReadResetConfirmation:
    @patch("footprinter.cli._policy_helpers.Confirm.ask", return_value=True)
    @patch("footprinter.cli._policy_helpers.count_affected_entities", return_value=MOCK_LARGE_COUNTS)
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.seed_permission_defaults")
    @patch("footprinter.cli.mcp_cmd.clear_permission_policies", return_value=2)
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_large_scope_prompts_confirmation(
        self, mock_db, mock_clear, mock_seed, mock_recalc, mock_count, mock_confirm
    ):
        from footprinter.cli.mcp_cmd import _read_reset

        conn = _mock_conn()
        mock_db.return_value = conn
        args = Namespace(yes=False)
        _read_reset(args)

        mock_confirm.assert_called_once()
        mock_recalc.assert_called_once()


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


class TestBulkApplyScopePreviewPluralization:
    @patch(
        "footprinter.cli._policy_helpers.count_affected_entities",
        return_value={"file": 1},
    )
    @patch("footprinter.cli._policy_helpers.set_visibility_policy")
    def test_singular_entity_type_no_trailing_s(self, mock_set_vis, mock_count, tool_db, capsys):
        from footprinter.cli._policy_helpers import bulk_apply

        tool_db.execute(
            "INSERT INTO files (id, source, name, path, account, status) "
            "VALUES (1, 'local', 'a.py', '/Users/me/Work/a.py', 'work', 'listed')"
        )
        tool_db.commit()

        bulk_apply(
            tool_db,
            folder="~/Work",
            project=None,
            permission=None,
            visibility="hidden",
            dry_run=True,
            yes=True,
        )

        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output


class TestRecalculateStatsPrinted:
    @patch("footprinter.cli.mcp_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.mcp_cmd.set_visibility_policy")
    @patch("footprinter.cli.mcp_cmd.get_policy_db")
    def test_prints_recalc_stats(self, mock_db, mock_set, mock_recalc, capsys):
        from footprinter.cli.mcp_cmd import _view_set

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="project:3", level="hidden")
        _view_set(args)

        output = capsys.readouterr().out
        # Stats line should mention entity counts
        assert "5" in output or "file" in output.lower()
