"""Tests for fp setup mcp subcommand.

Covers:
  Absorb fp-setup-claude into fp setup mcp subcommand
  fp setup mcp should check for mcp dependency before configuring

Verifies:
  1. fp setup mcp routes to mcp_setup functions
  2. fp setup mcp --check / --claude / --dry-run flags work
  3. Bare fp setup still runs interactive wizard
  4. fp setup --check / --hooks still work
  5. offer_setup_claude() is wired into the wizard flow
  6. User-facing strings reference 'fp setup mcp' not 'fp-setup-claude'
  7. MCP subcommand gated on mcp dependency availability
"""

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console


class TestMcpSubcommandRouting:
    """fp setup mcp should delegate to mcp_setup functions."""

    def test_mcp_bare_prints_snippet(self):
        """fp setup mcp (no flags) → generate_snippet() + print_snippet()."""
        with (
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp"]),
        ):
            mock_mcp.generate_snippet.return_value = {"mcpServers": {}}
            from footprinter.cli.setup import main

            main()

            mock_mcp.generate_snippet.assert_called_once()
            mock_mcp.print_snippet.assert_called_once()

    def test_mcp_check_calls_check_config(self):
        """fp setup mcp --check → mcp_setup.check_config()."""
        with (
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp", "--check"]),
        ):
            mock_mcp.check_config.return_value = 0
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_mcp.check_config.assert_called_once()

    def test_mcp_claude_calls_write_config(self):
        """fp setup mcp --claude → generate_snippet() + write_config(snippet)."""
        with (
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp", "--claude"]),
        ):
            mock_snippet = {"mcpServers": {"footprinter": {}}}
            mock_mcp.generate_snippet.return_value = mock_snippet
            mock_mcp.write_config.return_value = True
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_mcp.generate_snippet.assert_called_once()
            mock_mcp.write_config.assert_called_once_with(mock_snippet, dry_run=False)

    def test_mcp_dry_run_calls_write_config_dry(self):
        """fp setup mcp --dry-run → generate_snippet() + write_config(snippet, dry_run=True)."""
        with (
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp", "--dry-run"]),
        ):
            mock_snippet = {"mcpServers": {"footprinter": {}}}
            mock_mcp.generate_snippet.return_value = mock_snippet
            mock_mcp.write_config.return_value = True
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_mcp.write_config.assert_called_once_with(mock_snippet, dry_run=True)

    def test_mcp_claude_failure_exits_1(self):
        """fp setup mcp --claude → exit 1 when write_config returns False."""
        with (
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp", "--claude"]),
        ):
            mock_mcp.generate_snippet.return_value = {"mcpServers": {}}
            mock_mcp.write_config.return_value = False
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestBackwardCompatibility:
    """Bare fp setup and top-level flags still work after subparser addition."""

    def test_bare_fp_setup_runs_wizard(self):
        """fp setup (no args) → run_interactive_wizard()."""
        with (
            patch("footprinter.cli.setup.run_interactive_wizard") as mock_wizard,
            patch("sys.argv", ["fp"]),
        ):
            from footprinter.cli.setup import main

            main()
            mock_wizard.assert_called_once()

    def test_fp_setup_check_validates_config(self):
        """fp setup --check → check_existing_config()."""
        with (
            patch("footprinter.cli.setup.check_existing_config", return_value=0) as mock_check,
            patch("sys.argv", ["fp", "--check"]),
        ):
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_check.assert_called_once()


class TestOfferSetupClaudeWiring:
    """offer_setup_claude() should be called during wizard flow."""

    def test_wizard_calls_offer_setup_claude(self):
        """run_interactive_wizard should call offer_setup_claude() in Phase 6 (Connect)."""
        from unittest.mock import MagicMock

        from tests.conftest import run_wizard_mocked

        mock_offer_claude = MagicMock()
        run_wizard_mocked(offer_setup_claude=mock_offer_claude)
        mock_offer_claude.assert_called_once()

    def test_wizard_passes_mcp_result_to_summary(self):
        """run_interactive_wizard should pass mcp_configured=True to print_summary."""
        from unittest.mock import MagicMock

        from tests.conftest import run_wizard_mocked

        mocks = run_wizard_mocked(offer_setup_claude=MagicMock(return_value=True))
        mocks["print_summary"].assert_called_once_with(
            chat_result={},
            mcp_configured=True,
            connector_results={},
        )


class TestUserFacingStrings:
    """User-facing strings should reference 'fp setup mcp', not 'fp-setup-claude'."""

    def test_mcp_subparser_help_generic(self):
        """MCP subparser help text says 'MCP' generically, not only 'Claude Desktop'."""
        import re
        from pathlib import Path

        setup_path = Path(__file__).parent.parent.parent / "footprinter" / "cli" / "setup.py"
        content = setup_path.read_text()

        # Extract the help= value from the mcp add_parser call in register()
        # The pattern spans multiple lines, so use re.DOTALL
        mcp_block = re.search(r'add_parser\(\s*"mcp".*?\)', content, re.DOTALL)
        assert mcp_block, "Could not find mcp add_parser call"
        block_text = mcp_block.group()

        # The help= kwarg in this block should not mention "Claude Desktop"
        help_match = re.search(r'help="([^"]*)"', block_text)
        assert help_match, "Could not find help= in mcp add_parser"
        assert "Claude Desktop" not in help_match.group(1), (
            f"MCP subparser help still references 'Claude Desktop': {help_match.group(1)}"
        )

    def test_setup_print_summary_no_mcp_hint(self):
        """print_summary should not include MCP hint (removed in summary overhaul)."""
        import io

        from rich.console import Console

        from footprinter.cli.setup import print_summary

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            with patch("footprinter.cli.setup._get_indexing_counts", return_value={}):
                print_summary(chat_result=None)

        output = buf.getvalue()
        assert "fp setup mcp" not in output
        assert "fp-setup-claude" not in output

    def test_offer_setup_claude_error_msg_uses_fp_setup_mcp(self):
        """offer_setup_claude error fallback should say 'fp setup mcp --claude'."""
        import io

        from rich.console import Console

        from footprinter.cli.setup import offer_setup_claude

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True),
            patch("footprinter.cli.mcp_setup.generate_snippet", side_effect=Exception("test")),
        ):
            mock_confirm.ask.return_value = True
            offer_setup_claude()

        output = buf.getvalue()
        assert "fp setup mcp" in output

    def test_mcp_setup_strings_reference_fp_setup_mcp(self):
        """mcp_setup.py user-facing strings should say 'fp setup mcp'."""
        from pathlib import Path

        mcp_setup_path = Path(__file__).parent.parent.parent / "footprinter" / "cli" / "mcp_setup.py"
        content = mcp_setup_path.read_text()

        # The docstring and print_snippet should reference fp setup mcp
        assert "fp setup mcp" in content

        # check_config messages should reference fp setup mcp
        # Count remaining fp-setup-claude references (only prog= should remain)
        lines_with_old_ref = [
            line
            for line in content.splitlines()
            if "fp-setup-claude" in line and "prog=" not in line and "deprecated" not in line
        ]
        assert lines_with_old_ref == [], f"Found non-prog fp-setup-claude references: {lines_with_old_ref}"

    def test_check_help_in_register_no_claude_desktop(self):
        """--check help string for MCP subparser in register() should not reference 'Claude Desktop'."""
        import re

        setup_path = Path(__file__).parent.parent.parent / "footprinter" / "cli" / "setup.py"
        content = setup_path.read_text()

        # Extract register() function body
        register_match = re.search(r"^def register\b.*?(?=^def |\Z)", content, re.MULTILINE | re.DOTALL)
        assert register_match, "Could not find register() function"
        register_body = register_match.group()

        # Find --check help for the MCP subparser (identified by dest="mcp_check")
        check_help = re.search(
            r'add_argument\(\s*"--check".*?dest="mcp_check".*?help="([^"]*)"',
            register_body,
            re.DOTALL,
        )
        assert check_help, "Could not find MCP --check help in register()"
        assert "Claude Desktop" not in check_help.group(1), (
            f"MCP --check help in register() still references 'Claude Desktop': {check_help.group(1)}"
        )

    def test_check_help_in_main_no_claude_desktop(self):
        """--check help string for MCP subparser in main() should not reference 'Claude Desktop'."""
        import re

        setup_path = Path(__file__).parent.parent.parent / "footprinter" / "cli" / "setup.py"
        content = setup_path.read_text()

        # Extract main() function body
        main_match = re.search(r"^def main\b.*?(?=^def |\Z)", content, re.MULTILINE | re.DOTALL)
        assert main_match, "Could not find main() function"
        main_body = main_match.group()

        # Find --check help for the MCP subparser (identified by dest="mcp_check")
        check_help = re.search(
            r'add_argument\(\s*"--check".*?dest="mcp_check".*?help="([^"]*)"',
            main_body,
            re.DOTALL,
        )
        assert check_help, "Could not find MCP --check help in main()"
        assert "Claude Desktop" not in check_help.group(1), (
            f"MCP --check help in main() still references 'Claude Desktop': {check_help.group(1)}"
        )

    def test_mcp_setup_docstring_no_claude_desktop(self):
        """mcp_setup.py module docstring description should not reference 'Claude Desktop'."""
        import re

        mcp_setup_path = Path(__file__).parent.parent.parent / "footprinter" / "cli" / "mcp_setup.py"
        content = mcp_setup_path.read_text()

        # Extract module docstring (first triple-quoted string)
        docstring_match = re.search(r'^"""(.*?)"""', content, re.DOTALL)
        assert docstring_match, "Could not find module docstring"
        docstring = docstring_match.group(1)

        # Check the description portion (before "Usage:") — the --claude usage
        # example legitimately references Claude Desktop since that flag targets it
        description = docstring.split("Usage:")[0] if "Usage:" in docstring else docstring
        assert "Claude Desktop" not in description, (
            "mcp_setup.py module docstring description still references 'Claude Desktop'"
        )

    def test_orchestrator_hint_uses_fp_mcp(self):
        """Status module help hint should say 'fp mcp'."""
        from pathlib import Path

        status_path = Path(__file__).parent.parent.parent / "footprinter" / "ingest" / "status.py"
        content = status_path.read_text()

        assert "fp mcp" in content
        assert "fp-setup-claude" not in content


class TestMcpSnippetDisplay:
    """offer_setup_claude() should offer to display MCP snippet before auto-config."""

    def test_snippet_shown_when_accepted(self):
        """Accepting snippet prompt should call print_snippet."""
        with (
            patch("footprinter.cli.setup.console"),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
        ):
            mock_mcp.is_mcp_available.return_value = True
            mock_confirm.ask.side_effect = [True, False]  # view snippet=yes, auto-config=no
            mock_mcp.generate_snippet.return_value = {"mcpServers": {}}
            from footprinter.cli.setup import offer_setup_claude

            offer_setup_claude()
            mock_mcp.print_snippet.assert_called_once()

    def test_snippet_skipped_when_declined(self):
        """Declining snippet prompt should not call print_snippet."""
        with (
            patch("footprinter.cli.setup.console"),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
        ):
            mock_mcp.is_mcp_available.return_value = True
            mock_confirm.ask.side_effect = [False, False]  # view snippet=no, auto-config=no
            mock_mcp.generate_snippet.return_value = {"mcpServers": {}}
            from footprinter.cli.setup import offer_setup_claude

            offer_setup_claude()
            mock_mcp.print_snippet.assert_not_called()

    def test_snippet_not_offered_when_mcp_unavailable(self):
        """When MCP is unavailable, no prompts should be shown."""
        with (
            patch("footprinter.cli.setup.console"),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
        ):
            mock_mcp.is_mcp_available.return_value = False
            from footprinter.cli.setup import offer_setup_claude

            offer_setup_claude()
            mock_confirm.ask.assert_not_called()


class TestEntryPointRemoved:
    """fp-setup-claude entry point should be removed from pyproject.toml."""

    def test_fp_setup_claude_not_in_pyproject(self):
        """fp-setup-claude should no longer be in [project.scripts]."""
        import tomllib
        from pathlib import Path

        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text())
        scripts = data.get("project", {}).get("scripts", {})
        assert "fp-setup-claude" not in scripts

    def test_fp_in_pyproject(self):
        """fp should be in [project.scripts]."""
        import tomllib
        from pathlib import Path

        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text())
        scripts = data.get("project", {}).get("scripts", {})
        assert "fp" in scripts


# ---------------------------------------------------------------------------
# MCP dependency gating
# ---------------------------------------------------------------------------
class TestMcpDependencyGating:
    """fp setup mcp should be gated on the mcp package being installed."""

    def test_mcp_claude_blocked_when_mcp_missing(self):
        """fp setup mcp --claude exits 1 and does NOT call write_config() when mcp missing."""
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=False),
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp", "--claude"]),
        ):
            mock_mcp.is_mcp_available.return_value = False
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
            mock_mcp.write_config.assert_not_called()

        output = buf.getvalue()
        assert "pip install mcp" in output

    def test_mcp_bare_blocked_when_mcp_missing(self):
        """fp setup mcp (bare) does NOT call print_snippet() when mcp missing."""
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=False),
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("sys.argv", ["fp", "mcp"]),
        ):
            mock_mcp.is_mcp_available.return_value = False
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit):
                main()
            mock_mcp.print_snippet.assert_not_called()

        output = buf.getvalue()
        assert "pip install mcp" in output

    def test_mcp_check_reports_dependency_missing(self):
        """check_config() reports mcp package not installed when missing."""
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=False),
            patch("footprinter.cli.mcp_setup.console", test_console),
        ):
            import json
            import tempfile
            from pathlib import Path

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump({"mcpServers": {"footprinter": {"command": "fp-mcp"}}}, f)
                tmp_path = Path(f.name)

            try:
                from footprinter.cli.mcp_setup import check_config

                check_config(config_path=tmp_path)
            finally:
                tmp_path.unlink()

        output = buf.getvalue()
        assert "mcp package: not installed" in output

    def test_mcp_check_reports_dependency_installed(self):
        """fp setup mcp --check reports mcp package installed."""
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True),
            patch("footprinter.cli.mcp_setup.console", test_console),
            patch("footprinter.cli.mcp_setup.detect_config_path") as mock_detect,  # noqa: F841
        ):
            import json
            import tempfile
            from pathlib import Path

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump({"mcpServers": {"footprinter": {"command": "fp-mcp"}}}, f)
                tmp_path = Path(f.name)

            try:
                from footprinter.cli.mcp_setup import check_config

                check_config(config_path=tmp_path)
            finally:
                tmp_path.unlink()

        output = buf.getvalue()
        assert "mcp package: installed" in output

    def test_offer_setup_claude_skips_when_mcp_missing(self):
        """offer_setup_claude() skips MCP config when mcp package missing."""
        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
        ):
            mock_mcp.is_mcp_available.return_value = False
            from footprinter.cli.setup import offer_setup_claude

            offer_setup_claude()
            mock_mcp.generate_snippet.assert_not_called()

        output = buf.getvalue()
        assert "pip install mcp" in output


# ---------------------------------------------------------------------------
# _dispatch_mcp shared function
# ---------------------------------------------------------------------------
class TestDispatchMcp:
    """_dispatch_mcp() should be used by both entry points."""

    def test_dispatch_mcp_routes_check(self):
        """_dispatch_mcp(args) with mcp_check=True → mcp_setup.check_config()."""
        from types import SimpleNamespace

        with patch("footprinter.cli.setup.mcp_setup") as mock_mcp:
            mock_mcp.check_config.return_value = 0
            from footprinter.cli.setup import _dispatch_mcp

            with pytest.raises(SystemExit) as exc_info:
                _dispatch_mcp(SimpleNamespace(mcp_check=True, claude=False, dry_run=False))
            assert exc_info.value.code == 0
            mock_mcp.check_config.assert_called_once()

    def test_dispatch_mcp_routes_bare_to_print_snippet(self):
        """_dispatch_mcp(args) with no flags → generate_snippet() + print_snippet()."""
        from types import SimpleNamespace

        with patch("footprinter.cli.setup.mcp_setup") as mock_mcp:
            mock_mcp.is_mcp_available.return_value = True
            mock_mcp.generate_snippet.return_value = {"mcpServers": {}}
            from footprinter.cli.setup import _dispatch_mcp

            _dispatch_mcp(SimpleNamespace(mcp_check=False, claude=False, dry_run=False))
            mock_mcp.generate_snippet.assert_called_once()
            mock_mcp.print_snippet.assert_called_once()

    def test_dispatch_mcp_routes_claude_to_write_config(self):
        """_dispatch_mcp(args) with claude=True → write_config(snippet, dry_run=False)."""
        from types import SimpleNamespace

        with patch("footprinter.cli.setup.mcp_setup") as mock_mcp:
            mock_mcp.is_mcp_available.return_value = True
            mock_snippet = {"mcpServers": {"footprinter": {}}}
            mock_mcp.generate_snippet.return_value = mock_snippet
            mock_mcp.write_config.return_value = True
            from footprinter.cli.setup import _dispatch_mcp

            with pytest.raises(SystemExit) as exc_info:
                _dispatch_mcp(SimpleNamespace(mcp_check=False, claude=True, dry_run=False))
            assert exc_info.value.code == 0
            mock_mcp.write_config.assert_called_once_with(mock_snippet, dry_run=False)

    def test_dispatch_mcp_exits_when_mcp_unavailable(self):
        """_dispatch_mcp(args) exits 1 when MCP package is not installed."""
        from types import SimpleNamespace

        with (
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
            patch("footprinter.cli.setup.console"),
        ):
            mock_mcp.is_mcp_available.return_value = False
            from footprinter.cli.setup import _dispatch_mcp

            with pytest.raises(SystemExit) as exc_info:
                _dispatch_mcp(SimpleNamespace(mcp_check=False, claude=True, dry_run=False))
            assert exc_info.value.code == 1
            mock_mcp.write_config.assert_not_called()


class TestMcpDependencyGatingAvailability:
    """MCP dependency gating — available path."""

    def test_offer_setup_claude_proceeds_when_mcp_available(self):
        """offer_setup_claude() proceeds normally when mcp package is available."""
        with (
            patch("footprinter.cli.setup.console"),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup.mcp_setup") as mock_mcp,
        ):
            mock_mcp.is_mcp_available.return_value = True
            # True for both prompts: view snippet + auto-config
            mock_confirm.ask.return_value = True
            snippet = {"mcpServers": {"footprinter": {}}}
            mock_mcp.generate_snippet.return_value = snippet
            mock_mcp.write_config.return_value = True
            from footprinter.cli.setup import offer_setup_claude

            offer_setup_claude()
            mock_mcp.generate_snippet.assert_called_once()
            mock_mcp.print_snippet.assert_called_once_with(snippet)
            mock_mcp.write_config.assert_called_once()
