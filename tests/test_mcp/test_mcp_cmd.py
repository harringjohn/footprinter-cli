"""Tests for mcp_cmd.py — stale description fixes."""

from conftest import run_fp


class TestMcpReadResetDescription:
    """The read reset parser description must reflect the actual default (global=allow)."""

    def test_read_reset_help_says_global_allow(self):
        stdout, stderr, code = run_fp("mcp", "read", "reset", "--help")
        combined = stdout + stderr
        assert code == 0
        assert "global=allow" in combined, f"Expected 'global=allow' in reset help, got: {combined}"

    def test_read_reset_help_not_global_deny(self):
        stdout, stderr, code = run_fp("mcp", "read", "reset", "--help")
        combined = stdout + stderr
        assert "global=deny" not in combined, f"Found stale 'global=deny' in reset help: {combined}"
