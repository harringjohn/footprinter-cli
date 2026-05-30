"""Tests for policy command help text correctness.

Originally tested fp mcp reset --help. After FPR-1850, server launchers
moved to fp-mcp/fp-api console_scripts and policy commands live under
fp permission. These tests now verify fp permission reset --help.
"""

from conftest import run_fp


class TestPermissionResetDescription:
    """The reset parser description must describe inheritance fallback."""

    def test_reset_help_mentions_inheritance(self):
        stdout, stderr, code = run_fp("permission", "reset", "--help")
        combined = stdout + stderr
        assert code == 0
        assert "inherit" in combined.lower(), f"Expected 'inherit' in reset help, got: {combined}"

    def test_reset_help_mentions_reseed(self):
        stdout, stderr, code = run_fp("permission", "reset", "--help")
        combined = stdout + stderr
        assert "--all" in combined, f"Expected '--all' in reset help, got: {combined}"
