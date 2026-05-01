"""Tests for fp api CLI command."""

from tests.conftest import run_fp


class TestApiCliCommand:
    """Test the 'fp api' subcommand registration."""

    def test_api_registers_subcommand(self):
        """'api' appears in the CLI subparsers."""
        # Parse --help to verify 'api' is listed
        stdout, stderr, code = run_fp("api", "--help")
        assert code == 0
        assert "api" in stdout.lower() or "api" in stderr.lower()

    def test_api_default_host_port(self):
        """Default host is 127.0.0.1 and port is 8000."""
        import argparse

        from footprinter.cli import api_cmd

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers()
        api_cmd.register(subs)

        args = parser.parse_args(["api"])
        assert args.host == "127.0.0.1"
        assert args.port == 8000

    def test_api_help(self):
        """fp api --help exits 0."""
        stdout, stderr, code = run_fp("api", "--help")
        assert code == 0
