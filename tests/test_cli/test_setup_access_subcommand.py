"""Tests for MCP access control seed defaults and DB helpers.

Verifies:
  1. seed_access_policies() seeds metadata-only defaults (idempotent)
  2. Wizard calls seed_access_policies()
  3. _get_db_connection() resolves DB path correctly
  4. Access policy explanation printed after seeding
  5. Posture change offered during setup
"""

import io
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console


@pytest.fixture
def policy_db(tmp_path):
    """Create a database with full schema for policy tests.

    Uses the real Database.init_db() to guarantee schema matches production.
    Sets FOOTPRINTER_DB_PATH env var so setup.py functions find it.
    """
    db_path = tmp_path / "test.db"
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    db.conn.close()
    return db_path


@pytest.fixture
def policy_db_env(policy_db, monkeypatch):
    """policy_db + FOOTPRINTER_DB_PATH env var set."""
    monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(policy_db))
    return policy_db


# ---------------------------------------------------------------------------
# TestSeedAccessPolicies
# ---------------------------------------------------------------------------


class TestSeedAccessPolicies:
    """seed_access_policies() should seed metadata-only defaults."""

    @patch("footprinter.cli.setup.Confirm")
    def test_seeds_exactly_two_rows(self, mock_confirm, policy_db_env):
        """Should insert one row into each policy table (scope=global)."""
        mock_confirm.ask.return_value = False
        from footprinter.cli.setup import seed_access_policies

        result = seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        vis_count = conn.execute("SELECT COUNT(*) FROM visibility_policies").fetchone()[0]
        perm_count = conn.execute("SELECT COUNT(*) FROM permission_policies").fetchone()[0]
        conn.close()

        assert vis_count == 1
        assert perm_count == 1
        assert result["visibility_seeded"] is True
        assert result["permission_seeded"] is True

    @patch("footprinter.cli.setup.Confirm")
    def test_visibility_is_visible(self, mock_confirm, policy_db_env):
        """Global visibility should be 'visible' (open access)."""
        mock_confirm.ask.return_value = False
        from footprinter.cli.setup import seed_access_policies

        seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "visible"

    @patch("footprinter.cli.setup.Confirm")
    def test_permission_is_allow(self, mock_confirm, policy_db_env):
        """Global permission should be 'allow' (open access)."""
        mock_confirm.ask.return_value = False
        from footprinter.cli.setup import seed_access_policies

        seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "allow"

    @patch("footprinter.cli.setup.Confirm")
    def test_idempotent(self, mock_confirm, policy_db_env):
        """Running twice should not duplicate rows."""
        mock_confirm.ask.return_value = False
        from footprinter.cli.setup import seed_access_policies

        seed_access_policies()
        result = seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        vis_count = conn.execute("SELECT COUNT(*) FROM visibility_policies").fetchone()[0]
        perm_count = conn.execute("SELECT COUNT(*) FROM permission_policies").fetchone()[0]
        conn.close()

        assert vis_count == 1
        assert perm_count == 1
        # Second run should report not seeded (already existed)
        assert result["visibility_seeded"] is False
        assert result["permission_seeded"] is False

    @patch("footprinter.cli.setup.Confirm")
    def test_preserves_existing_non_global_policies(self, mock_confirm, policy_db_env):
        """Should not affect existing non-global policies."""
        mock_confirm.ask.return_value = False
        conn = sqlite3.connect(str(policy_db_env))
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'opaque')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        conn.commit()
        conn.close()

        from footprinter.cli.setup import seed_access_policies

        seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        vis_rows = conn.execute("SELECT scope, setting FROM visibility_policies ORDER BY scope").fetchall()
        perm_rows = conn.execute("SELECT scope, setting FROM permission_policies ORDER BY scope").fetchall()
        conn.close()

        # Should have 2 visibility rows: global + source:files
        assert len(vis_rows) == 2
        assert ("global", "visible") in vis_rows
        assert ("source:files", "opaque") in vis_rows

        # Should have 2 permission rows: global + source:emails
        assert len(perm_rows) == 2
        assert ("global", "allow") in perm_rows
        assert ("source:emails", "allow") in perm_rows

    @patch("footprinter.cli.setup.Confirm")
    def test_does_not_overwrite_custom_global(self, mock_confirm, policy_db_env):
        """INSERT OR IGNORE should not overwrite existing global policy."""
        mock_confirm.ask.return_value = False
        conn = sqlite3.connect(str(policy_db_env))
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'opaque')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'allow')")
        conn.commit()
        conn.close()

        from footprinter.cli.setup import seed_access_policies

        result = seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        vis = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()[0]
        perm = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()[0]
        conn.close()

        # Should preserve the user's custom settings
        assert vis == "opaque"
        assert perm == "allow"
        assert result["visibility_seeded"] is False
        assert result["permission_seeded"] is False

    def test_returns_empty_dict_if_no_db(self, monkeypatch, tmp_path):
        """Should return empty dict if database file doesn't exist."""
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(tmp_path / "nonexistent.db"))

        from footprinter.cli.setup import seed_access_policies

        result = seed_access_policies()
        assert result == {}


# ---------------------------------------------------------------------------
# TestAccessPolicyExplanation
# ---------------------------------------------------------------------------


class TestAccessPolicyExplanation:
    """seed_access_policies() should explain what the defaults mean."""

    def test_explanation_printed_after_seeding(self, policy_db_env):
        """Should print plain-English explanation of visible + allow."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            seed_access_policies()

        output = buf.getvalue()
        # Should explain what "visible" means
        assert "file names" in output.lower() or "filenames" in output.lower()
        assert "path" in output.lower()
        # Should explain what "content allowed" means
        assert "read" in output.lower() or "content" in output.lower()

    def test_security_posture_note_printed(self, policy_db_env):
        """Should print a security posture note referencing fail-open and docs."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            seed_access_policies()

        output = buf.getvalue()
        assert "fail-open" in output.lower(), "Security posture note should mention 'fail-open'"
        assert "security posture" in output.lower(), (
            "Security posture note should reference the Security Posture docs section"
        )

    def test_security_posture_note_when_already_configured(self, policy_db_env):
        """Security posture note should appear even when policies already exist."""
        from footprinter.cli.setup import seed_access_policies

        # First call seeds
        with patch("footprinter.cli.setup.Confirm") as mock_confirm:
            mock_confirm.ask.return_value = False
            seed_access_policies()

        # Second call — already configured
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            seed_access_policies()

        output = buf.getvalue()
        assert "fail-open" in output.lower()
        assert "security posture" in output.lower()

    def test_explanation_printed_when_already_configured(self, policy_db_env):
        """Explanation should appear even when policies already exist."""
        from footprinter.cli.setup import seed_access_policies

        # First call seeds
        with patch("footprinter.cli.setup.Confirm") as mock_confirm:
            mock_confirm.ask.return_value = False
            seed_access_policies()

        # Second call — already configured
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            seed_access_policies()

        output = buf.getvalue()
        assert "file names" in output.lower() or "filenames" in output.lower()


# ---------------------------------------------------------------------------
# TestPostureChangeOffer
# ---------------------------------------------------------------------------


class TestPostureChangeOffer:
    """seed_access_policies() should offer to switch to metadata-only access."""

    def test_posture_change_offered(self, policy_db_env):
        """Should call Confirm.ask with metadata-only question."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            seed_access_policies()

        mock_confirm.ask.assert_called_once()
        call_args = mock_confirm.ask.call_args[0][0].lower()
        assert "metadata" in call_args, "Confirm prompt should mention 'metadata'"
        assert "inventory" not in call_args, "Confirm prompt should not use jargon 'inventory'"

    def test_accepting_metadata_only_sets_deny(self, policy_db_env):
        """Accepting metadata-only should set permission to deny."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = True
            seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "deny"

    def test_declining_keeps_allow(self, policy_db_env):
        """Declining metadata-only should keep permission as allow."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = False
            seed_access_policies()

        conn = sqlite3.connect(str(policy_db_env))
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "allow"

    def test_metadata_only_reuses_conn(self):
        """Should open only one DB connection, not a second for metadata-only."""
        mock_conn = MagicMock()
        with (
            patch("footprinter.cli.setup._get_db_connection", return_value=mock_conn) as mock_get,
            patch(
                "footprinter.cli.setup._seed_access_policies",
                return_value={
                    "visibility_seeded": True,
                    "permission_seeded": True,
                },
            ),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup.console"),
            patch("footprinter.db.policies.set_permission_policy"),
        ):
            mock_confirm.ask.return_value = True
            from footprinter.cli.setup import seed_access_policies

            seed_access_policies()

        assert mock_get.call_count == 1, f"Expected 1 call to _get_db_connection, got {mock_get.call_count}"

    def test_metadata_conn_not_leaked_on_policy_error(self):
        """Connection must be closed even when set_permission_policy raises."""
        mock_conn = MagicMock()
        with (
            patch("footprinter.cli.setup._get_db_connection", return_value=mock_conn),
            patch(
                "footprinter.cli.setup._seed_access_policies",
                return_value={
                    "visibility_seeded": True,
                    "permission_seeded": True,
                },
            ),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup.console"),
            patch(
                "footprinter.db.policies.set_permission_policy",
                side_effect=RuntimeError("db locked"),
            ),
        ):
            mock_confirm.ask.return_value = True
            from footprinter.cli.setup import seed_access_policies

            result = seed_access_policies()

        assert result == {}
        mock_conn.close.assert_called()


# ---------------------------------------------------------------------------
# TestAccessPolicySeedingFailure
# ---------------------------------------------------------------------------


class TestAccessPolicySeedingFailure:
    """seed_access_policies() should warn the user when seeding fails."""

    def test_prints_warning_on_seed_failure(self, policy_db_env):
        """Exception in _seed_access_policies should produce visible warning."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch(
                "footprinter.cli.setup._seed_access_policies",
                side_effect=RuntimeError("db locked"),
            ),
        ):
            result = seed_access_policies()

        output = buf.getvalue().lower()
        # Should warn the user visibly
        assert "warning" in output or "failed" in output
        # Should still return {} (not crash the wizard)
        assert result == {}

    def test_seed_failure_includes_recovery_hint(self, policy_db_env):
        """Warning should include a hint on how to recover."""
        from footprinter.cli.setup import seed_access_policies

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch(
                "footprinter.cli.setup._seed_access_policies",
                side_effect=RuntimeError("db locked"),
            ),
        ):
            seed_access_policies()

        output = buf.getvalue().lower()
        # Should tell the user how to fix it
        assert "fp setup" in output or "fp mcp" in output

    def test_conn_closed_on_seed_failure(self, policy_db_env):
        """Connection must be closed even when _seed_access_policies raises."""
        mock_conn = MagicMock()
        with (
            patch("footprinter.cli.setup._get_db_connection", return_value=mock_conn),
            patch(
                "footprinter.cli.setup._seed_access_policies",
                side_effect=RuntimeError("db locked"),
            ),
        ):
            from footprinter.cli.setup import seed_access_policies

            seed_access_policies()

        mock_conn.close.assert_called()


# ---------------------------------------------------------------------------
# TestConnectionCleanupOnFailure
# ---------------------------------------------------------------------------


class TestConnectionCleanupOnFailure:
    """Connection must be closed on both success and error paths."""

    def test_conn_closed_on_indexing_counts_failure(self):
        """_get_indexing_counts must close conn even when cursor.execute raises."""
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        with patch("footprinter.cli.setup._get_db_connection", return_value=mock_conn):
            from footprinter.cli.setup import _get_indexing_counts

            result = _get_indexing_counts()

        assert result == {}
        mock_conn.close.assert_called()


# ---------------------------------------------------------------------------
# TestWizardSeedStep
# ---------------------------------------------------------------------------


class TestWizardSeedStep:
    """Wizard should call seed_access_policies()."""

    def test_wizard_calls_seed_access_policies(self):
        """run_interactive_wizard should call seed_access_policies() in Phase 6 (Connect)."""
        from unittest.mock import MagicMock

        from tests.conftest import run_wizard_mocked

        mock_seed = MagicMock()
        mocks = run_wizard_mocked(seed_access_policies=mock_seed)
        mock_seed.assert_called_once()


# ---------------------------------------------------------------------------
# TestGetDbConnection
# ---------------------------------------------------------------------------


class TestGetDbConnection:
    """_get_db_connection() helper should resolve DB path correctly."""

    def test_returns_connection_when_db_exists(self, policy_db_env):
        """Should return a sqlite3 connection when DB exists."""
        from footprinter.cli.setup import _get_db_connection

        conn = _get_db_connection()
        assert conn is not None
        # Verify it's usable
        conn.execute("SELECT 1")
        conn.close()

    def test_returns_none_when_db_missing(self, monkeypatch, tmp_path):
        """Should return None when DB file doesn't exist."""
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(tmp_path / "nonexistent.db"))

        from footprinter.cli.setup import _get_db_connection

        result = _get_db_connection()
        assert result is None

    def test_respects_env_var(self, policy_db_env):
        """Should use FOOTPRINTER_DB_PATH when set."""
        from footprinter.cli.setup import _get_db_connection

        conn = _get_db_connection()
        assert conn is not None
        conn.close()

    def test_connection_has_row_factory(self, policy_db_env):
        """Connection should have sqlite3.Row as row_factory."""
        from footprinter.cli.setup import _get_db_connection

        conn = _get_db_connection()
        assert conn.row_factory == sqlite3.Row
        conn.close()
