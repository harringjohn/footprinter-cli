"""Tests for mcp_cmd.py — help text correctness."""

from conftest import run_fp


class TestMcpResetDescription:
    """The reset parser description must describe inheritance fallback."""

    def test_reset_help_mentions_inheritance(self):
        stdout, stderr, code = run_fp("mcp", "reset", "--help")
        combined = stdout + stderr
        assert code == 0
        assert "inherit" in combined.lower(), f"Expected 'inherit' in reset help, got: {combined}"

    def test_reset_help_mentions_reseed(self):
        stdout, stderr, code = run_fp("mcp", "reset", "--help")
        combined = stdout + stderr
        assert "--all" in combined, f"Expected '--all' in reset help, got: {combined}"
