"""Tests for ``fp setup`` — slim-down refactor.

Validates:
  1. setup.py exposes register(subparsers) for the fp CLI router
  2. fp setup subcommands (mcp, google, folders) respond to --help
  3. CLI routing rejects removed subcommands (access, clients, projects, folders list)
  4. Kept functions survive the refactor
  5. Removed flags (--hooks, --reset, --check) are rejected
"""

import inspect

import pytest

from tests.conftest import run_fp

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

    def test_setup_help_omits_hooks(self):
        stdout, stderr, code = run_fp("setup", "--help")
        assert "--hooks" not in stdout + stderr

    def test_setup_help_omits_reset(self):
        stdout, stderr, code = run_fp("setup", "--help")
        assert "--reset" not in stdout + stderr

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
# 4. Removed flags are rejected
# ---------------------------------------------------------------------------


class TestSetupRemovedFlags:
    """Removed flags (--hooks, --reset) are rejected by the parser."""

    def test_hooks_flag_rejected(self):
        _stdout, _stderr, code = run_fp("setup", "--hooks")
        assert code != 0, "fp setup --hooks should fail (removed)"

    def test_reset_flag_rejected(self):
        _stdout, _stderr, code = run_fp("setup", "--reset")
        assert code != 0, "fp setup --reset should fail (removed)"
