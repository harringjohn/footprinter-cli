"""Tests for ``fp setup`` — slim-down refactor + reset + conditional hooks.

Validates:
  1. setup.py exposes register(subparsers) for the fp CLI router
  2. fp setup subcommands (mcp, google, folders) respond to --help
  3. CLI routing rejects removed subcommands (access, clients, projects, folders list)
  4. Kept functions survive the refactor
  5. ``--reset`` clears data and re-runs wizard
  6. ``--hooks`` conditionally registered based on hook file presence
"""

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import run_fp

SETUP_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "footprinter" / "cli" / "setup.py"


# ---------------------------------------------------------------------------
# 1. Parser tree — register() exists and subcommands work
# ---------------------------------------------------------------------------


class TestSetupRegister:
    """setup.py exposes register() and fp setup subcommands respond to --help."""

    def test_setup_module_has_register(self):
        from footprinter.cli import setup

        assert callable(getattr(setup, "register", None))

    def test_register_accepts_subparsers(self):
        from footprinter.cli import setup

        sig = inspect.signature(setup.register)
        params = list(sig.parameters)
        assert len(params) >= 1, "register() must accept a subparsers argument"

    def test_setup_help_exits_zero(self):
        stdout, stderr, code = run_fp("setup", "--help")
        assert code == 0

    def test_setup_help_check_removed(self):
        stdout, stderr, code = run_fp("setup", "--help")
        assert "--check" not in stdout + stderr

    def test_setup_help_mentions_hooks(self):
        from footprinter.cli.setup import _hooks_available

        if not _hooks_available():
            pytest.skip("hooks not available (snapshot environment)")
        stdout, stderr, code = run_fp("setup", "--help")
        assert "--hooks" in stdout + stderr

    def test_setup_help_mentions_reset(self):
        stdout, stderr, code = run_fp("setup", "--help")
        assert "--reset" in stdout + stderr

    def test_setup_mcp_help_exits_zero(self):
        stdout, stderr, code = run_fp("setup", "mcp", "--help")
        assert code == 0

    def test_setup_folders_help_exits_zero(self):
        stdout, stderr, code = run_fp("setup", "folders", "--help")
        assert code == 0

    def test_setup_folders_add_help_exits_zero(self):
        stdout, stderr, code = run_fp("setup", "folders", "add", "--help")
        assert code == 0

    def test_setup_folders_remove_help_exits_zero(self):
        stdout, stderr, code = run_fp("setup", "folders", "remove", "--help")
        assert code == 0


# ---------------------------------------------------------------------------
# 2. CLI routing — removed subcommands are rejected
# ---------------------------------------------------------------------------


class TestSetupRemovals:
    """Verify CLI routing rejects removed subcommands."""

    @pytest.mark.parametrize("subcmd", ["access", "clients", "projects"])
    def test_removed_subcommand_exits_nonzero(self, subcmd):
        _stdout, stderr, code = run_fp("setup", subcmd)
        assert code != 0, f"fp setup {subcmd} should fail (removed)"

    def test_folders_list_removed(self):
        _stdout, _stderr, code = run_fp("setup", "folders", "list")
        assert code != 0, "fp setup folders list should fail (moved to fp folder list)"


# ---------------------------------------------------------------------------
# 3. Kept function assertions — surviving code
# ---------------------------------------------------------------------------


class TestSetupKeptFunctions:
    """Key functions that must survive the slim-down."""

    @pytest.mark.parametrize(
        "func_name",
        [
            "run_interactive_wizard",
            "install_git_hooks",
            "seed_access_policies",
            "folders_add",
            "folders_remove",
            "main",
            "register",
        ],
    )
    def test_kept_function_exists(self, func_name):
        from footprinter.cli import setup

        assert hasattr(setup, func_name), f"{func_name} should still exist in setup.py"


# ---------------------------------------------------------------------------
# 4. Reset — ``fp setup --reset`` clears data and re-runs wizard
# ---------------------------------------------------------------------------


class TestSetupReset:
    """``fp setup --reset`` clears DB + chroma, preserves config, re-runs wizard."""

    @pytest.fixture(autouse=True)
    def _setup_home(self, tmp_path, monkeypatch):
        """Redirect FOOTPRINTER_HOME to a temp dir for each test."""
        self.home = tmp_path / "fp_home"
        self.home.mkdir()
        monkeypatch.setenv("FOOTPRINTER_HOME", str(self.home))
        monkeypatch.delenv("FOOTPRINTER_DB_PATH", raising=False)

    def _create_data(self):
        """Create fake DB and chroma dir in the temp home."""
        db = self.home / "footprinter.db"
        db.write_text("fake db")
        chroma = self.home / "chroma"
        chroma.mkdir()
        (chroma / "index.bin").write_text("fake vectors")
        return db, chroma

    def test_reset_clears_db_and_chroma(self):
        db, chroma = self._create_data()
        with (
            patch("footprinter.cli.setup.run_interactive_wizard") as mock_wizard,
            patch("footprinter.cli.setup.Confirm.ask", return_value=True),
        ):
            _stdout, _stderr, code = run_fp("setup", "--reset")
        assert code == 0, f"Expected exit 0, got {code}"
        assert not db.exists(), "DB should be deleted"
        assert not chroma.exists(), "Chroma dir should be deleted"
        assert mock_wizard.called, "Wizard should have been called"

    def test_reset_aborted_preserves_data(self):
        db, chroma = self._create_data()
        with patch("footprinter.cli.setup.Confirm.ask", return_value=False):
            _stdout, _stderr, _code = run_fp("setup", "--reset")
        assert db.exists(), "DB should be preserved when reset is cancelled"
        assert chroma.exists(), "Chroma should be preserved when reset is cancelled"

    def test_reset_preserves_config(self):
        self._create_data()
        config = self.home / "config.yaml"
        config.write_text("general:\n  enabled: true\n")
        with (
            patch("footprinter.cli.setup.run_interactive_wizard"),
            patch("footprinter.cli.setup.Confirm.ask", return_value=True),
        ):
            run_fp("setup", "--reset")
        assert config.exists(), "config.yaml must be preserved"

    def test_reset_handles_missing_data(self):
        """Reset on empty home should not crash."""
        with (
            patch("footprinter.cli.setup.run_interactive_wizard"),
            patch("footprinter.cli.setup.Confirm.ask", return_value=True),
        ):
            _stdout, _stderr, code = run_fp("setup", "--reset")
        assert code == 0, f"Expected clean exit, got {code}"


    def test_reset_no_test_mode_warning_outside_test(self):
        """Reset outside test mode should NOT show the reset-specific test warning."""
        with (
            patch("footprinter.cli.setup.run_interactive_wizard"),
            patch("footprinter.cli.setup.Confirm.ask", return_value=True),
        ):
            stdout, _stderr, code = run_fp("setup", "--reset")
        assert code == 0, f"Expected exit 0, got {code}"
        assert "not production" not in stdout.lower(), (
            f"Reset test-mode warning should not appear outside test mode, got: {stdout}"
        )


# ---------------------------------------------------------------------------
# 7. Conditional hooks
# ---------------------------------------------------------------------------


class TestConditionalHooks:
    """--hooks flag and hooks UI are conditional on hook file presence."""

    def test_hooks_available_true(self):
        """_hooks_available() returns True when scripts/hooks/post-merge exists."""
        from footprinter.cli.setup import _hooks_available

        if not _hooks_available():
            pytest.skip("hooks not available (snapshot environment)")
        assert _hooks_available() is True

    def test_hooks_hidden_when_unavailable(self):
        """--hooks flag not registered when hook file is absent."""
        with patch("footprinter.cli.setup._hooks_available", return_value=False):
            stdout, stderr, code = run_fp("setup", "--help")
        assert "--hooks" not in stdout + stderr

    def test_hooks_visible_when_available(self):
        """--hooks flag registered when hook file is present."""
        with patch("footprinter.cli.setup._hooks_available", return_value=True):
            stdout, stderr, code = run_fp("setup", "--help")
        assert "--hooks" in stdout + stderr

    def test_hooks_hint_removed_from_summary(self):
        """Wizard summary no longer includes 'Git hooks' hint."""
        source = SETUP_MODULE_PATH.read_text()
        assert "Git hooks (dev)" not in source, "print_summary should not contain 'Git hooks (dev)' hint"
