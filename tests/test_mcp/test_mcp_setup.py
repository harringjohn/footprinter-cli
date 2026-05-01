"""Tests for the MCP Configuration Helper (src.cli.mcp_setup)."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console as RichConsole

from footprinter.cli.mcp_setup import (
    _is_dev_checkout,
    check_config,
    detect_config_path,
    generate_snippet,
    get_mcp_command,
    print_snippet,
    write_config,
)


# ---------------------------------------------------------------------------
# TestConfigPathDetection — 5 tests
# ---------------------------------------------------------------------------
class TestConfigPathDetection:
    """Tests for detect_config_path()."""

    def test_macos_path(self):
        with patch("footprinter.cli.mcp_setup.platform.system", return_value="Darwin"):
            path = detect_config_path()
        assert path is not None
        assert "Application Support" in str(path)
        assert path.name == "claude_desktop_config.json"

    def test_linux_path(self):
        with patch("footprinter.cli.mcp_setup.platform.system", return_value="Linux"):
            path = detect_config_path()
        assert path is not None
        assert ".config" in str(path)
        assert path.name == "claude_desktop_config.json"

    def test_windows_path(self):
        with (
            patch("footprinter.cli.mcp_setup.platform.system", return_value="Windows"),
            patch.dict("os.environ", {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}),
        ):
            path = detect_config_path()
        assert path is not None
        assert "Claude" in str(path)

    def test_windows_no_appdata(self):
        with (
            patch("footprinter.cli.mcp_setup.platform.system", return_value="Windows"),
            patch.dict("os.environ", {"APPDATA": ""}, clear=False),
        ):
            path = detect_config_path()
        assert path is not None
        assert "AppData" in str(path)

    def test_windows_appdata_fallback(self):
        with (
            patch("footprinter.cli.mcp_setup.platform.system", return_value="Windows"),
            patch.dict("os.environ", {}, clear=True),
            patch("footprinter.cli.mcp_setup.Path.home", return_value=Path("/Users/test")),
        ):
            path = detect_config_path()
        assert path is not None
        assert "AppData" in str(path)

    def test_unsupported_platform(self):
        with patch("footprinter.cli.mcp_setup.platform.system", return_value="FreeBSD"):
            path = detect_config_path()
        assert path is None


# ---------------------------------------------------------------------------
# TestSnippetGeneration — 9 tests
# ---------------------------------------------------------------------------
class TestSnippetGeneration:
    """Tests for generate_snippet()."""

    def test_returns_dict_with_mcp_servers(self):
        snippet = generate_snippet()
        assert "mcpServers" in snippet
        assert "footprinter" in snippet["mcpServers"]

    def test_server_has_command(self):
        snippet = generate_snippet()
        server = snippet["mcpServers"]["footprinter"]
        assert "command" in server

    def test_server_has_cwd(self, tmp_path):
        """Dev checkout (mocked): snippet includes cwd."""
        (tmp_path / "pyproject.toml").touch()
        with (
            patch("footprinter.cli.mcp_setup._repo_root", return_value=tmp_path),
            patch("footprinter.cli.mcp_setup.shutil.which", return_value="/usr/local/bin/fp"),
        ):
            snippet = generate_snippet()
        server = snippet["mcpServers"]["footprinter"]
        assert "cwd" in server
        assert server["cwd"] == str(tmp_path)

    def test_custom_project_root(self, tmp_path):
        snippet = generate_snippet(project_root=tmp_path)
        server = snippet["mcpServers"]["footprinter"]
        assert server["cwd"] == str(tmp_path)

    def test_produces_valid_json(self):
        snippet = generate_snippet()
        json_str = json.dumps(snippet)
        parsed = json.loads(json_str)
        assert parsed == snippet

    def test_cwd_omitted_for_pip_install(self, tmp_path):
        """Pip-install scenario: no pyproject.toml at repo root → cwd omitted."""
        # tmp_path has no pyproject.toml, simulating a site-packages parent
        with (
            patch("footprinter.cli.mcp_setup._repo_root", return_value=tmp_path),
            patch("footprinter.cli.mcp_setup.shutil.which", return_value="/usr/local/bin/fp"),
        ):
            snippet = generate_snippet()
        server = snippet["mcpServers"]["footprinter"]
        assert "cwd" not in server

    def test_cwd_set_for_dev_checkout(self, tmp_path):
        """Dev checkout scenario: pyproject.toml present → cwd set."""
        (tmp_path / "pyproject.toml").touch()
        with (
            patch("footprinter.cli.mcp_setup._repo_root", return_value=tmp_path),
            patch("footprinter.cli.mcp_setup.shutil.which", return_value="/usr/local/bin/fp"),
        ):
            snippet = generate_snippet()
        server = snippet["mcpServers"]["footprinter"]
        assert server["cwd"] == str(tmp_path)

    def test_pip_install_no_fp_falls_back_to_sys_executable(self, tmp_path):
        """Pip-install + no fp on PATH: falls back to sys.executable, no cwd."""
        with (
            patch("footprinter.cli.mcp_setup._repo_root", return_value=tmp_path),
            patch("footprinter.cli.mcp_setup.shutil.which", return_value=None),
        ):
            snippet = generate_snippet()
        server = snippet["mcpServers"]["footprinter"]
        assert server["command"] == sys.executable
        assert server["args"] == ["-m", "footprinter.mcp"]
        assert "cwd" not in server

    def test_fp_snippet_includes_mcp_arg(self, tmp_path):
        """When fp is on PATH, generated snippet must include args: ["mcp"]."""
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value="/usr/local/bin/fp"):
            snippet = generate_snippet(project_root=tmp_path)
        server = snippet["mcpServers"]["footprinter"]
        assert server.get("args") == ["mcp"]


# ---------------------------------------------------------------------------
# TestDevCheckoutDetection — 2 tests
# ---------------------------------------------------------------------------
class TestDevCheckoutDetection:
    """Tests for _is_dev_checkout()."""

    def test_returns_true_when_pyproject_exists(self, tmp_path):
        """_is_dev_checkout() returns True when pyproject.toml is present."""
        (tmp_path / "pyproject.toml").touch()
        with patch("footprinter.cli.mcp_setup._repo_root", return_value=tmp_path):
            assert _is_dev_checkout() is True

    def test_returns_false_when_pyproject_absent(self, tmp_path):
        """_is_dev_checkout() returns False when pyproject.toml is absent."""
        with patch("footprinter.cli.mcp_setup._repo_root", return_value=tmp_path):
            assert _is_dev_checkout() is False


# ---------------------------------------------------------------------------
# TestMCPCommand — 3 tests
# ---------------------------------------------------------------------------
class TestMCPCommand:
    """Tests for get_mcp_command()."""

    def test_prefers_run_script(self, tmp_path):
        script = tmp_path / "run_mcp.sh"
        script.touch()
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value=None):
            command, args = get_mcp_command(project_root=tmp_path)
        assert command == str(script)
        assert args == ["mcp"]

    def test_falls_back_to_sys_executable(self, tmp_path):
        # No run_mcp.sh present
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value=None):
            command, args = get_mcp_command(project_root=tmp_path)
        assert command == sys.executable
        assert args == ["-m", "footprinter.mcp"]

    def test_no_hardcoded_venv_in_fallback(self, tmp_path):
        # No run_mcp.sh present — should not construct a path from project_root
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value=None):
            command, args = get_mcp_command(project_root=tmp_path)
        assert not command.startswith(str(tmp_path))

    def test_returns_tuple(self):
        result = get_mcp_command()
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestMCPCommandFpMcp — 3 tests
# ---------------------------------------------------------------------------
class TestMCPCommandFpEntryPoint:
    """Tests for fp entry point priority (fp-mcp -> fp)."""

    def test_prefers_fp_when_on_path(self, tmp_path):
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value="/usr/local/bin/fp"):
            command, args = get_mcp_command(project_root=tmp_path)
        assert command == "/usr/local/bin/fp"
        assert args == ["mcp"]

    def test_fp_preferred_over_run_script(self, tmp_path):
        script = tmp_path / "run_mcp.sh"
        script.touch()
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value="/usr/local/bin/fp"):
            command, args = get_mcp_command(project_root=tmp_path)
        assert command == "/usr/local/bin/fp"
        assert args == ["mcp"]

    def test_run_script_when_no_fp(self, tmp_path):
        script = tmp_path / "run_mcp.sh"
        script.touch()
        with patch("footprinter.cli.mcp_setup.shutil.which", return_value=None):
            command, args = get_mcp_command(project_root=tmp_path)
        assert command == str(script)


# ---------------------------------------------------------------------------
# TestCheckConfig — 4 tests
# ---------------------------------------------------------------------------
class TestCheckConfig:
    """Tests for check_config()."""

    def test_returns_1_when_missing(self, tmp_path):
        path = tmp_path / "missing.json"
        assert check_config(config_path=path) == 1

    def test_returns_0_when_configured(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        config = {"mcpServers": {"footprinter": {"command": "/bin/true"}}}
        path.write_text(json.dumps(config))
        assert check_config(config_path=path) == 0

    def test_returns_2_when_not_configured(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        config = {"mcpServers": {"other-server": {"command": "/bin/other"}}}
        path.write_text(json.dumps(config))
        assert check_config(config_path=path) == 2

    def test_returns_1_for_invalid_json(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text("not valid json {{{")
        assert check_config(config_path=path) == 1

    # -- Multi-client check tests --

    def _capture_check(self, **kwargs) -> tuple[str, int]:
        """Run check_config() and capture Rich output."""
        buf = io.StringIO()
        fake_console = RichConsole(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.mcp_setup.console", fake_console),
            patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True),
        ):
            result = check_config(**kwargs)
        return buf.getvalue(), result

    def test_multi_client_reports_all_found(self, tmp_path):
        """Multi-client check reports all clients with footprinter configured."""
        cd_path = tmp_path / "claude_desktop_config.json"
        cd_path.write_text(json.dumps({"mcpServers": {"footprinter": {"command": "fp"}}}))
        cursor_path = tmp_path / "cursor_mcp.json"
        cursor_path.write_text(json.dumps({"mcpServers": {"footprinter": {"command": "fp"}}}))

        clients = [("Claude Desktop", cd_path), ("Cursor", cursor_path)]
        with patch("footprinter.cli.mcp_setup._get_checkable_clients", return_value=clients, create=True):
            output, result = self._capture_check()

        assert result == 0
        assert "Claude Desktop" in output
        assert "Cursor" in output

    def test_multi_client_returns_0_if_any_configured(self, tmp_path):
        """Returns 0 if footprinter configured in at least one client."""
        cd_path = tmp_path / "claude_desktop_config.json"
        cd_path.write_text(json.dumps({"mcpServers": {"footprinter": {"command": "fp"}}}))
        cursor_path = tmp_path / "cursor_mcp.json"
        cursor_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))

        clients = [("Claude Desktop", cd_path), ("Cursor", cursor_path)]
        with patch("footprinter.cli.mcp_setup._get_checkable_clients", return_value=clients, create=True):
            output, result = self._capture_check()

        assert result == 0
        assert "not configured" in output.lower()

    def test_multi_client_returns_2_if_none_configured(self, tmp_path):
        """Returns 2 if configs exist but footprinter not in any."""
        cd_path = tmp_path / "claude_desktop_config.json"
        cd_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        cursor_path = tmp_path / "cursor_mcp.json"
        cursor_path.write_text(json.dumps({"mcpServers": {"other2": {"command": "y"}}}))

        clients = [("Claude Desktop", cd_path), ("Cursor", cursor_path)]
        with patch("footprinter.cli.mcp_setup._get_checkable_clients", return_value=clients, create=True):
            _, result = self._capture_check()

        assert result == 2

    def test_multi_client_returns_1_if_all_missing(self, tmp_path):
        """Returns 1 if all config files are missing."""
        clients = [
            ("Claude Desktop", tmp_path / "nonexistent1.json"),
            ("Cursor", tmp_path / "nonexistent2.json"),
        ]
        with patch("footprinter.cli.mcp_setup._get_checkable_clients", return_value=clients, create=True):
            _, result = self._capture_check()

        assert result == 1

    def test_config_path_override_single_client(self, tmp_path):
        """config_path parameter checks only that single path (backward compat)."""
        path = tmp_path / "custom_config.json"
        path.write_text(json.dumps({"mcpServers": {"footprinter": {"command": "fp"}}}))

        _, result = self._capture_check(config_path=path)
        assert result == 0


# ---------------------------------------------------------------------------
# TestWriteConfig — 5 tests
# ---------------------------------------------------------------------------
class TestWriteConfig:
    """Tests for write_config()."""

    def test_creates_new_config(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        snippet = generate_snippet()
        assert write_config(snippet, config_path=path) is True
        assert path.exists()
        config = json.loads(path.read_text())
        assert "footprinter" in config["mcpServers"]

    def test_merges_with_existing(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        existing = {"mcpServers": {"other": {"command": "/bin/other"}}, "extra": True}
        path.write_text(json.dumps(existing))

        snippet = generate_snippet()
        assert write_config(snippet, config_path=path) is True

        config = json.loads(path.read_text())
        assert "other" in config["mcpServers"]
        assert "footprinter" in config["mcpServers"]
        assert config["extra"] is True

    def test_creates_backup(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({"mcpServers": {}}))

        snippet = generate_snippet()
        write_config(snippet, config_path=path)

        backups = list(tmp_path.glob("*.backup_*.json"))
        assert len(backups) == 1

    def test_dry_run_does_not_write(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        snippet = generate_snippet()
        assert write_config(snippet, config_path=path, dry_run=True) is True
        assert not path.exists()

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "Claude" / "claude_desktop_config.json"
        snippet = generate_snippet()
        assert write_config(snippet, config_path=path) is True
        assert path.exists()


# ---------------------------------------------------------------------------
# Bug 4: TestBackupCollision — rapid writes produce unique backups
# ---------------------------------------------------------------------------
class TestBackupCollision:
    """Test that rapid backup writes produce unique files."""

    def test_rapid_writes_produce_unique_backups(self, tmp_path):
        """Two rapid write_config() calls should produce 2 distinct backup files."""
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({"mcpServers": {}}))

        snippet = generate_snippet()
        write_config(snippet, config_path=path)
        write_config(snippet, config_path=path)

        backups = list(tmp_path.glob("*.backup_*.json"))
        assert len(backups) == 2, f"Expected 2 unique backups, got {len(backups)}: {[b.name for b in backups]}"


# ---------------------------------------------------------------------------
# Bug 5: TestSnippetValidation — warn on missing command
# ---------------------------------------------------------------------------
class TestSnippetValidation:
    """Test that generate_snippet validates the command exists."""

    def test_generate_snippet_warns_on_missing_command(self, tmp_path):
        """generate_snippet should warn if the command doesn't exist on disk or PATH."""
        import io

        from rich.console import Console as RichConsole

        buf = io.StringIO()
        fake_console = RichConsole(file=buf, force_terminal=False)

        with (
            patch("footprinter.cli.mcp_setup.sys.executable", "/nonexistent/python3"),
            patch("footprinter.cli.mcp_setup.shutil.which", return_value=None),
            patch("footprinter.cli.mcp_setup.console", fake_console),
        ):
            snippet = generate_snippet(project_root=tmp_path)

        assert "mcpServers" in snippet
        output = buf.getvalue()
        assert "warning" in output.lower() or "not found" in output.lower()


# ---------------------------------------------------------------------------
# TestPathEdgeCases — 2 tests
# ---------------------------------------------------------------------------
class TestPathEdgeCases:
    """Tests for generate_snippet() with unusual paths."""

    def test_project_root_with_spaces(self, tmp_path):
        """Path containing spaces works in generate_snippet()."""
        spaced = tmp_path / "my project"
        spaced.mkdir()
        snippet = generate_snippet(project_root=spaced)
        server = snippet["mcpServers"]["footprinter"]
        assert str(spaced) in server["cwd"]

    def test_project_root_with_unicode(self, tmp_path):
        """Unicode characters in path work."""
        uni = tmp_path / "proyecto_\u00e9l"
        uni.mkdir()
        snippet = generate_snippet(project_root=uni)
        server = snippet["mcpServers"]["footprinter"]
        assert str(uni) in server["cwd"]


# ---------------------------------------------------------------------------
# TestWriteConfigEdgeCases — 3 tests
# ---------------------------------------------------------------------------
class TestWriteConfigEdgeCases:
    """Tests for write_config() edge cases."""

    def test_handles_invalid_json_in_existing_file(self, tmp_path):
        """Corrupt JSON in existing file → returns False."""
        path = tmp_path / "claude_desktop_config.json"
        path.write_text("{invalid json content!!!")
        snippet = generate_snippet()
        result = write_config(snippet, config_path=path)
        assert result is False

    def test_merges_without_losing_non_mcp_keys(self, tmp_path):
        """Preserves globalShortcut, theme, etc. during merge."""
        path = tmp_path / "claude_desktop_config.json"
        existing = {
            "mcpServers": {"other": {"command": "/bin/other"}},
            "globalShortcut": "Ctrl+Space",
            "theme": "dark",
        }
        path.write_text(json.dumps(existing))
        snippet = generate_snippet()
        write_config(snippet, config_path=path)
        config = json.loads(path.read_text())
        assert config["globalShortcut"] == "Ctrl+Space"
        assert config["theme"] == "dark"
        assert "footprinter" in config["mcpServers"]
        assert "other" in config["mcpServers"]

    def test_write_to_readonly_directory_fails_gracefully(self, tmp_path):
        """Readonly dir → returns False or raises PermissionError."""
        import os
        import stat

        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        path = readonly_dir / "config.json"
        # Make directory read-only
        os.chmod(readonly_dir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            snippet = generate_snippet()
            result = write_config(snippet, config_path=path)
            # Either returns False or raises PermissionError — both acceptable
            assert result is False
        except PermissionError:
            pass  # Also acceptable
        finally:
            # Restore permissions for cleanup
            os.chmod(readonly_dir, stat.S_IRWXU)


# ---------------------------------------------------------------------------
# TestCheckConfigEdgeCases — 2 tests
# ---------------------------------------------------------------------------
class TestCheckConfigEdgeCases:
    """Tests for check_config() edge cases."""

    def test_returns_1_for_unsupported_platform(self):
        """No checkable clients → check_config() returns 1."""
        with patch("footprinter.cli.mcp_setup._get_checkable_clients", return_value=[], create=True):
            result = check_config()
        assert result == 1

    def test_returns_0_even_with_extra_servers(self, tmp_path):
        """Footprinter found among many servers → returns 0."""
        path = tmp_path / "claude_desktop_config.json"
        config = {
            "mcpServers": {
                "server1": {"command": "/bin/s1"},
                "footprinter": {"command": "/bin/fp"},
                "server2": {"command": "/bin/s2"},
                "server3": {"command": "/bin/s3"},
            }
        }
        path.write_text(json.dumps(config))
        assert check_config(config_path=path) == 0


# ---------------------------------------------------------------------------
# TestMultiClientPaths — 4 tests
# ---------------------------------------------------------------------------
class TestMultiClientPaths:
    """Tests for multi-client MCP configuration output."""

    def _capture_snippet(self) -> str:
        """Capture print_snippet() output via StringIO-backed Rich console."""
        buf = io.StringIO()
        fake_console = RichConsole(file=buf, force_terminal=False)
        snippet = generate_snippet()
        with patch("footprinter.cli.mcp_setup.console", fake_console):
            print_snippet(snippet)
        return buf.getvalue()

    def test_print_snippet_includes_client_paths(self):
        """print_snippet() output contains all five client names."""
        output = self._capture_snippet()
        for name in ["Claude Desktop", "Claude Code", "Cursor", "VS Code", "Gemini CLI"]:
            assert name in output, f"Missing client name: {name}"

    def test_print_snippet_shows_claude_code_mcp_add(self):
        """print_snippet() output contains the claude mcp add command."""
        output = self._capture_snippet()
        assert "claude mcp add footprinter -- fp mcp" in output

    def test_print_snippet_shows_config_paths(self):
        """print_snippet() output contains known config file paths."""
        output = self._capture_snippet()
        for path in [
            "claude_desktop_config.json",
            ".cursor/mcp.json",
            ".vscode/mcp.json",
            ".gemini/settings.json",
        ]:
            assert path in output, f"Missing config path: {path}"

    def test_print_snippet_still_shows_json_panel(self):
        """print_snippet() still renders the JSON panel (regression)."""
        output = self._capture_snippet()
        assert "mcpServers" in output
        assert "footprinter" in output


# ---------------------------------------------------------------------------
# TestCLIFlags — 3 tests
# ---------------------------------------------------------------------------
class TestCLIFlags:
    """Tests for MCP subcommand CLI flag naming."""

    def _build_mcp_parser(self):
        """Build the fp setup mcp argparser by calling register()."""
        import argparse

        from footprinter.cli.setup import register

        root = argparse.ArgumentParser()
        subs = root.add_subparsers(dest="command")
        register(subs)
        return root

    def test_claude_flag_accepted(self):
        """--claude flag is accepted and sets args attribute."""
        parser = self._build_mcp_parser()
        args = parser.parse_args(["setup", "mcp", "--claude"])
        assert getattr(args, "claude", False) is True

    def test_write_flag_removed(self):
        """--write flag is no longer accepted (exits with code 2)."""
        parser = self._build_mcp_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["setup", "mcp", "--write"])
        assert exc_info.value.code == 2

    def test_chatgpt_flag_rejected(self):
        """--chatgpt flag is no longer accepted (exits with code 2)."""
        parser = self._build_mcp_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["setup", "mcp", "--chatgpt"])
        assert exc_info.value.code == 2

    def test_dry_run_help_references_claude(self):
        """--dry-run help text references --claude, not --write."""
        import contextlib

        parser = self._build_mcp_parser()
        help_buf = io.StringIO()
        with contextlib.redirect_stdout(help_buf), pytest.raises(SystemExit):
            parser.parse_args(["setup", "mcp", "--help"])
        help_output = help_buf.getvalue()
        assert "--claude" in help_output
        assert "--write" not in help_output
