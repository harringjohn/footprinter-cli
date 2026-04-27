"""Tests for the fp CLI router and subcommand registration.

Validates:
  1. footprinter.cli.main is importable and callable
  2. fp --help exits 0 and lists subcommands
  3. fp (no args) shows help and exits 0
  4. All 12 verb-first subcommands register and respond to --help
"""

import pytest
from conftest import run_fp

ALL_SUBCOMMANDS = [
    "ingest",
    "mcp",
    "api",
    "status",
    "search",
    "setup",
    "connect",
    "view",
    "upsert",
    "data",
    "delete",
    "vectorize",
    "uninstall",
]


class TestFpRouterImport:
    """footprinter.cli.main must be importable and callable."""

    def test_main_is_callable(self):
        from footprinter.cli import main

        assert callable(main)

    def test_main_accepts_argv(self):
        """main() signature accepts an argv parameter."""
        import inspect

        from footprinter.cli import main

        sig = inspect.signature(main)
        assert "argv" in sig.parameters


class TestFpRouterHelp:
    """fp --help and fp (no args) behaviour."""

    def test_help_exits_zero(self):
        stdout, stderr, code = run_fp("--help")
        assert code == 0

    def test_help_lists_subcommands(self):
        stdout, stderr, code = run_fp("--help")
        output = stdout + stderr
        for sub in ALL_SUBCOMMANDS:
            assert sub in output, f"'{sub}' not found in fp --help output"

    def test_no_args_shows_help(self):
        stdout, stderr, code = run_fp()
        assert code == 0
        output = stdout + stderr
        assert "Footprinter" in output
        assert "COMMAND" in output


class TestStubSubcommands:
    """Each verb module has register() and responds to fp <sub> --help."""

    @pytest.mark.parametrize("subcmd", ALL_SUBCOMMANDS)
    def test_subcommand_help_exits_zero(self, subcmd):
        stdout, stderr, code = run_fp(subcmd, "--help")
        assert code == 0, f"fp {subcmd} --help exited {code}"

    @pytest.mark.parametrize("subcmd", ALL_SUBCOMMANDS)
    def test_module_has_register(self, subcmd):
        """Each subcommand module exposes a callable register()."""
        module_map = {
            "ingest": "ingest",
            "mcp": "mcp_cmd",
            "api": "api_cmd",
            "status": "status_cmd",
            "search": "search_cmd",
            "setup": "setup",
            "connect": "connect",
            "view": "view",
            "upsert": "upsert",
            "data": "data",
            "delete": "delete",
            "vectorize": "vectorize_cmd",
        }
        module_name = module_map.get(subcmd, subcmd)
        mod = __import__(f"footprinter.cli.{module_name}", fromlist=["register"])
        assert callable(getattr(mod, "register", None)), f"footprinter.cli.{module_name} missing callable register()"
