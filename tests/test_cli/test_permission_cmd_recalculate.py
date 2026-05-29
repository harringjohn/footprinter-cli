"""Tests for permission_cmd.py recalculate handler.

Mirrors test_mcp_cmd_recalculate.py patterns: mock get_policy_db() and
recalculate_with_progress(), build Namespace args, call handler, assert
mocks called with correct arguments.
"""

import re
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

MOCK_STATS = {"file": 5, "email": 2}

MOCK_LARGE_COUNTS = {"file": 200, "email": 50}


def _mock_conn():
    conn = MagicMock()
    conn.close = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Scope routing
# ---------------------------------------------------------------------------


class TestRecalculateScope:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_default_scope_global(self, mock_db, mock_recalc):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope=None)
        _recalculate(args)

        mock_recalc.assert_called_once_with(conn, "global")

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_explicit_scope_passed_through(self, mock_db, mock_recalc):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        args = Namespace(scope="folder:~/Work")
        _recalculate(args)

        mock_recalc.assert_called_once_with(conn, "folder:~/Work")


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestRecalculateConnection:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_conn_closed_after_success(self, mock_db, mock_recalc):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        _recalculate(Namespace(scope="global"))

        conn.close.assert_called_once()

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", side_effect=RuntimeError("boom"))
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_conn_closed_on_error(self, mock_db, mock_recalc):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        with pytest.raises(RuntimeError):
            _recalculate(Namespace(scope="global"))

        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# No database
# ---------------------------------------------------------------------------


class TestRecalculateNoDb:
    @patch("footprinter.cli.permission_cmd.get_policy_db", return_value=None)
    def test_no_db_exits_1(self, mock_db):
        from footprinter.cli.permission_cmd import _recalculate

        with pytest.raises(SystemExit) as exc_info:
            _recalculate(Namespace(scope=None))

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# No confirmation prompt (non-destructive)
# ---------------------------------------------------------------------------


class TestRecalculateNoPrompt:
    @patch("footprinter.cli._policy_helpers.Confirm.ask")
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_never_prompts_for_confirmation(self, mock_db, mock_recalc, mock_confirm):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        _recalculate(Namespace(scope="global"))

        mock_confirm.assert_not_called()
        mock_recalc.assert_called_once()


# ---------------------------------------------------------------------------
# Stats output
# ---------------------------------------------------------------------------


class TestRecalculateStats:
    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_prints_entity_counts(self, mock_db, mock_recalc, capsys):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        _recalculate(Namespace(scope="global"))

        output = capsys.readouterr().out
        assert "5 files" in output
        assert "2 emails" in output

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value=MOCK_STATS)
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_prints_elapsed_time(self, mock_db, mock_recalc, capsys):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        _recalculate(Namespace(scope="global"))

        output = capsys.readouterr().out
        assert re.search(r"\d+\.\d+s", output), f"No elapsed time found in output: {output!r}"

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value={"file": 1})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_singular_pluralization(self, mock_db, mock_recalc, capsys):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        _recalculate(Namespace(scope="global"))

        output = capsys.readouterr().out
        assert "1 file" in output
        assert "1 files" not in output

    @patch("footprinter.cli.permission_cmd.recalculate_with_progress", return_value={"file": 0, "email": 2})
    @patch("footprinter.cli.permission_cmd.get_policy_db")
    def test_zero_count_excluded(self, mock_db, mock_recalc, capsys):
        from footprinter.cli.permission_cmd import _recalculate

        conn = _mock_conn()
        mock_db.return_value = conn

        _recalculate(Namespace(scope="global"))

        output = capsys.readouterr().out
        assert "2 emails" in output
        assert "0 file" not in output
