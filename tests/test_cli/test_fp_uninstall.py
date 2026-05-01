"""Tests for fp uninstall.

Covers:
  unregister_mcp_server() in mcp_setup
  fp uninstall router registration
  Full uninstall flow (interactive prompts, idempotency, partial state)
  pip vs pipx detection with manual-command fallback
  Unsupported platform handling
  Ctrl-C / PromptCancelled handling
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import run_fp


# ---------------------------------------------------------------------------
# unregister_mcp_server() — pure function in mcp_setup.py
# ---------------------------------------------------------------------------


class TestUnregisterMcpServer:
    """mcp_setup.unregister_mcp_server() removes the footprinter entry."""

    def _write_config(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def test_removes_footprinter_entry(self, tmp_path):
        """Removes only the footprinter entry, preserving other servers."""
        from footprinter.cli.mcp_setup import unregister_mcp_server

        cfg_path = tmp_path / "claude_desktop_config.json"
        self._write_config(
            cfg_path,
            {"mcpServers": {"footprinter": {"command": "fp"}, "other": {"command": "x"}}},
        )

        ok = unregister_mcp_server(config_path=cfg_path)

        assert ok is True
        result = json.loads(cfg_path.read_text())
        assert "footprinter" not in result["mcpServers"]
        assert result["mcpServers"]["other"] == {"command": "x"}

        # Backup file written
        backups = list(cfg_path.parent.glob("claude_desktop_config.backup_*.json"))
        assert len(backups) == 1

    def test_missing_entry_is_noop(self, tmp_path):
        """Config exists but has no footprinter key → success, file unchanged."""
        from footprinter.cli.mcp_setup import unregister_mcp_server

        cfg_path = tmp_path / "claude_desktop_config.json"
        self._write_config(cfg_path, {"mcpServers": {"other": {"command": "x"}}})
        original = cfg_path.read_text()

        ok = unregister_mcp_server(config_path=cfg_path)

        assert ok is True
        assert cfg_path.read_text() == original
        assert not list(cfg_path.parent.glob("*.backup_*.json"))

    def test_missing_file_is_noop(self, tmp_path):
        """Config file does not exist → success, no exception."""
        from footprinter.cli.mcp_setup import unregister_mcp_server

        cfg_path = tmp_path / "does_not_exist.json"
        ok = unregister_mcp_server(config_path=cfg_path)
        assert ok is True
        assert not cfg_path.exists()

    def test_dry_run_does_not_write(self, tmp_path):
        """dry_run=True leaves the file untouched."""
        from footprinter.cli.mcp_setup import unregister_mcp_server

        cfg_path = tmp_path / "claude_desktop_config.json"
        self._write_config(cfg_path, {"mcpServers": {"footprinter": {"command": "fp"}}})
        original = cfg_path.read_text()

        ok = unregister_mcp_server(config_path=cfg_path, dry_run=True)

        assert ok is True
        assert cfg_path.read_text() == original

    def test_mcp_servers_null_is_handled(self, tmp_path):
        """``"mcpServers": null`` (hand-edited config) is treated as no entries."""
        from footprinter.cli.mcp_setup import unregister_mcp_server

        cfg_path = tmp_path / "claude_desktop_config.json"
        self._write_config(cfg_path, {"mcpServers": None})

        ok = unregister_mcp_server(config_path=cfg_path)

        assert ok is True  # no-op, no TypeError


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------


class TestUninstallRouter:
    """fp uninstall must be a registered subcommand."""

    def test_uninstall_help_exits_zero(self):
        stdout, stderr, code = run_fp("uninstall", "--help")
        assert code == 0, f"fp uninstall --help exited {code}: {stderr}"

    def test_uninstall_module_has_register(self):
        from footprinter.cli import uninstall

        assert callable(getattr(uninstall, "register", None))


# ---------------------------------------------------------------------------
# fp uninstall — interactive flow
# ---------------------------------------------------------------------------


def _confirm_yes(*args, **kwargs):
    """SafeConfirm.ask stub that always returns True."""
    return True


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Override FOOTPRINTER_HOME to a populated tmp dir."""
    home = tmp_path / "fp_home"
    home.mkdir()
    (home / "config.yaml").write_text("data: {}\n")
    (home / "footprinter.db").write_text("")
    monkeypatch.setenv("FOOTPRINTER_HOME", str(home))
    return home


@pytest.fixture
def fake_mcp_config(tmp_path):
    """Write a fake claude_desktop_config.json with a footprinter entry."""
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"footprinter": {"command": "fp"}}}))
    return cfg


class TestUninstallFlow:
    """fp uninstall — end-to-end paths."""

    def test_full_flow_removes_everything(self, fake_home, fake_mcp_config):
        """Happy path: y to all prompts → MCP removed, data dir gone, pipx invoked."""
        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=fake_mcp_config),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_confirm_yes),
            patch("footprinter.cli.uninstall.shutil.which", side_effect=lambda x: "/usr/local/bin/pipx" if x == "pipx" else None),
            patch(
                "footprinter.cli.uninstall.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            _, stderr, code = run_fp("uninstall")

        assert code == 0, f"exit code {code}: {stderr}"
        # MCP entry removed
        cfg = json.loads(fake_mcp_config.read_text())
        assert "footprinter" not in cfg.get("mcpServers", {})
        # Data dir removed
        assert not fake_home.exists()
        # pipx uninstall invoked
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "/usr/local/bin/pipx"
        assert "uninstall" in call_args
        assert "footprinter-cli" in call_args

    def test_skips_when_user_declines_data(self, fake_home, fake_mcp_config):
        """Decline data prompt → data dir survives, other phases still run."""
        # Yes to MCP, No to data, Yes to package
        ask_returns = iter([True, False, True])

        def _ask(*a, **kw):
            return next(ask_returns)

        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=fake_mcp_config),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_ask),
            patch("footprinter.cli.uninstall.shutil.which", side_effect=lambda x: "/usr/local/bin/pipx" if x == "pipx" else None),
            patch(
                "footprinter.cli.uninstall.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ),
        ):
            _, _, code = run_fp("uninstall")

        assert code == 0
        assert fake_home.exists(), "data dir should remain when user declined"
        cfg = json.loads(fake_mcp_config.read_text())
        assert "footprinter" not in cfg.get("mcpServers", {})

    def test_idempotent_second_run(self, tmp_path, monkeypatch):
        """Second run with no state present → still exits 0, prints skipped."""
        # No FOOTPRINTER_HOME — get_home() will create an empty dir we can clean
        empty_home = tmp_path / "empty_home"
        monkeypatch.setenv("FOOTPRINTER_HOME", str(empty_home))
        empty_home.mkdir()
        # Drop a sentinel so get_home() doesn't auto-recreate as empty dir we
        # then can't delete; the phase only deletes after y prompt anyway.

        no_cfg = tmp_path / "missing.json"

        # Already-removed state: empty data dir, missing MCP config, no installer
        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=no_cfg),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_confirm_yes),
            patch("footprinter.cli.uninstall.shutil.which", return_value=None),
        ):
            _, _, code1 = run_fp("uninstall")
            _, _, code2 = run_fp("uninstall")

        assert code1 == 0
        assert code2 == 0

    def test_pip_fallback_when_pipx_missing(self, fake_home, fake_mcp_config):
        """No pipx, pip available → pip uninstall -y is called."""
        which_map = {"pipx": None, "pip": "/usr/bin/pip", "pip3": "/usr/bin/pip3"}

        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=fake_mcp_config),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_confirm_yes),
            patch("footprinter.cli.uninstall.shutil.which", side_effect=lambda x: which_map.get(x)),
            patch(
                "footprinter.cli.uninstall.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ) as mock_run,
        ):
            _, _, code = run_fp("uninstall")

        assert code == 0
        call_args = mock_run.call_args[0][0]
        assert call_args[0] in ("/usr/bin/pip", "/usr/bin/pip3")
        assert "uninstall" in call_args
        assert "-y" in call_args
        assert "footprinter-cli" in call_args

    def test_prints_command_when_subprocess_fails(self, fake_home, fake_mcp_config):
        """Installer found but call errors → print manual command, exit 0."""
        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=fake_mcp_config),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_confirm_yes),
            patch("footprinter.cli.uninstall.shutil.which", side_effect=lambda x: "/usr/local/bin/pipx" if x == "pipx" else None),
            patch(
                "footprinter.cli.uninstall.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["pipx", "uninstall", "footprinter-cli"]),
            ),
        ):
            stdout, stderr, code = run_fp("uninstall")

        assert code == 0
        combined = stdout + stderr
        assert "pipx uninstall footprinter-cli" in combined or "pip uninstall" in combined

    def test_prints_command_when_no_installer(self, fake_home, fake_mcp_config):
        """No pip or pipx → print manual command, exit 0."""
        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=fake_mcp_config),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_confirm_yes),
            patch("footprinter.cli.uninstall.shutil.which", return_value=None),
        ):
            stdout, stderr, code = run_fp("uninstall")

        assert code == 0
        assert "footprinter-cli" in (stdout + stderr)

    def test_unsupported_platform_skips_mcp(self, fake_home):
        """detect_config_path returns None → MCP phase warns and proceeds."""
        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=None),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_confirm_yes),
            patch("footprinter.cli.uninstall.shutil.which", return_value=None),
        ):
            _, _, code = run_fp("uninstall")

        assert code == 0
        # data dir still removed
        assert not fake_home.exists()

    def test_cancel_via_prompt_exits_130(self, fake_home, fake_mcp_config):
        """SafeConfirm raises PromptCancelled → router exits 130."""
        from footprinter.cli._prompt import PromptCancelled

        def _raise(*a, **kw):
            raise PromptCancelled("Ctrl+C")

        with (
            patch("footprinter.cli.uninstall.detect_config_path", return_value=fake_mcp_config),
            patch("footprinter.cli.uninstall.SafeConfirm.ask", side_effect=_raise),
        ):
            _, _, code = run_fp("uninstall")

        assert code == 130
        # Nothing was deleted
        assert fake_home.exists()
        cfg = json.loads(fake_mcp_config.read_text())
        assert "footprinter" in cfg.get("mcpServers", {})
