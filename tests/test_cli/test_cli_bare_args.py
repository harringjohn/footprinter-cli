"""Tests for bare CLI invocations — no raw argparse errors.

Every ``fp <command>`` and ``fp <command> <subcommand>`` path that requires
arguments should show helpful output when invoked bare, not a raw
"the following arguments are required" message.
"""

import pytest
from conftest import run_fp

from footprinter.connectors import AuthType, ConnectorSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RAW_ERROR = "the following arguments are required"


def _google_spec() -> ConnectorSpec:
    """Minimal Google ConnectorSpec for testing."""
    return ConnectorSpec(
        name="google",
        extra="google",
        description="Google Drive and Gmail integration",
        pipes=("drive_folders", "drive_files", "gmail"),
        probe_module="google.auth",
        config_sections=("google_drive", "gmail"),
        setup_hook="footprinter.cli.google_setup.run_google_setup",
        remove_packages=(
            "google-api-python-client",
            "google-auth-oauthlib",
            "google-auth-httplib2",
        ),
        adapter_entries={},
        services=("drive", "gmail"),
        seed_prefix="gdrive",
        auth_type=AuthType.OAUTH2,
    )


@pytest.fixture(autouse=True)
def _mock_connectors(monkeypatch) -> None:
    """Ensure discover_connectors returns a google spec and is_installed returns True."""
    monkeypatch.setattr(
        "footprinter.cli.connect.discover_connectors",
        lambda: {"google": _google_spec()},
    )
    monkeypatch.setattr(
        "footprinter.cli.connect.is_installed",
        lambda spec: True,
    )


# ===========================================================================
# 1. Connect subcommands — bare invocation
# ===========================================================================


class TestBareConnectSubcommands:
    """fp connect install/remove/config/label without args should show guidance."""

    def test_connect_install_bare_lists_connectors(self):
        stdout, stderr, code = run_fp("connect", "install")
        output = stdout + stderr
        assert code == 0
        assert "google" in output.lower()
        assert _RAW_ERROR not in output

    def test_connect_remove_bare_lists_connectors(self):
        stdout, stderr, code = run_fp("connect", "remove")
        output = stdout + stderr
        assert code == 0
        assert "google" in output.lower()
        assert _RAW_ERROR not in output

    def test_connect_config_bare_lists_connectors(self):
        stdout, stderr, code = run_fp("connect", "config")
        output = stdout + stderr
        assert code == 0
        assert "google" in output.lower()
        assert _RAW_ERROR not in output

    def test_connect_label_bare_shows_usage(self):
        stdout, stderr, code = run_fp("connect", "label")
        output = stdout + stderr
        assert code == 0
        assert _RAW_ERROR not in output
        assert "google" in output.lower()
        assert "label" in output.lower()


# ===========================================================================
# 2. Data — bare invocation
# ===========================================================================


class TestBareDataCommand:
    """fp data (no action) should show help with the 3 actions."""

    def test_data_bare_shows_help(self):
        stdout, stderr, code = run_fp("data")
        output = stdout + stderr
        assert code == 0
        assert "import" in output.lower()
        assert _RAW_ERROR not in output


# ===========================================================================
# 3. Search — bare invocation
# ===========================================================================


class TestBareSearchCommand:
    """fp search (no query) should show help with examples."""

    def test_search_bare_shows_help(self):
        stdout, stderr, code = run_fp("search")
        output = stdout + stderr
        assert code == 0
        assert _RAW_ERROR not in output
        # Should contain example queries from epilog
        assert "search" in output.lower()


# ===========================================================================
# 4. Comprehensive sweep — no raw argparse errors anywhere
# ===========================================================================


# Commands that may start servers, run wizards, or need DB access are excluded
# from the sweep — they have side effects. We test only the bare-invocation
# error path, not full execution.
_BARE_INVOCATIONS = [
    # Top-level commands (fp <command>)
    ("data",),
    ("search",),
    # Connect subcommands (fp connect <verb>)
    ("connect", "install"),
    ("connect", "remove"),
    ("connect", "config"),
    ("connect", "label"),
]


class TestNoBareArgparseErrors:
    """No command path should produce a raw 'the following arguments are required' message."""

    @pytest.mark.parametrize("argv", _BARE_INVOCATIONS, ids=[" ".join(a) for a in _BARE_INVOCATIONS])
    def test_no_raw_argparse_error(self, argv):
        stdout, stderr, code = run_fp(*argv)
        output = stdout + stderr
        assert _RAW_ERROR not in output, f"fp {' '.join(argv)} produced raw argparse error"
