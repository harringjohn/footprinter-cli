"""Tests for fp connect — connector registry and CLI commands."""

import argparse
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from footprinter.connectors import AuthType, ConnectorSpec


@pytest.fixture(autouse=True)
def _mock_discover_connectors(monkeypatch):
    """All CLI handler tests need discover_connectors() to return the Google spec."""
    monkeypatch.setattr(
        "footprinter.cli.connect.discover_connectors",
        lambda: {"google": _google_spec()},
    )


def _google_spec() -> ConnectorSpec:
    """Build the Google ConnectorSpec for testing."""
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
        adapter_entries={
            "drive_folders": "footprinter.connectors.google.adapters.drive_folders:DriveFoldersAdapter",
            "drive_files": "footprinter.connectors.google.adapters.drive_files:DriveFilesAdapter",
            "gmail": "footprinter.connectors.google.adapters.gmail:GmailAdapter",
        },
        services=("drive", "gmail"),
        seed_prefix="gdrive",
        schema_extensions={
            "folders": [("web_link", "TEXT")],
        },
        auth_type=AuthType.OAUTH2,
        config_apply="footprinter.connectors.google.config.apply_google_config",
        health_check="footprinter.connectors.google.health.get_health_rows",
        read_file="footprinter.connectors.google.drive.read_file_bytes",
        seed_label_fn="footprinter.connectors.google.config.drive_seed_label",
    )


# ---------------------------------------------------------------------------
# RED 1 — Registry pure functions
# ---------------------------------------------------------------------------


class TestConnectorRegistry:
    def test_google_spec_fields(self):
        spec = _google_spec()
        assert spec.extra == "google"
        assert spec.probe_module == "google.auth"
        assert "drive_files" in spec.pipes
        assert "gmail" in spec.pipes
        assert spec.config_sections == ("google_drive", "gmail")

    @patch("importlib.util.find_spec", return_value=None)
    def test_is_installed_when_missing(self, mock_find):
        from footprinter.connectors import is_installed

        assert is_installed(_google_spec()) is False

    @patch("importlib.util.find_spec", return_value=MagicMock())
    def test_is_installed_when_present(self, mock_find):
        from footprinter.connectors import is_installed

        assert is_installed(_google_spec()) is True

    @patch("importlib.util.find_spec", side_effect=ValueError("namespace"))
    def test_is_installed_handles_value_error(self, mock_find):
        from footprinter.connectors import is_installed

        assert is_installed(_google_spec()) is False

    def test_is_configured_enabled(self):
        from footprinter.connectors import is_configured

        config = {"google_drive": {"enabled": True}}
        assert is_configured(_google_spec(), config) is True

    def test_is_configured_disabled(self):
        from footprinter.connectors import is_configured

        config = {"google_drive": {"enabled": False}, "gmail": {"enabled": False}}
        assert is_configured(_google_spec(), config) is False

    def test_has_credentials_exists(self, tmp_path):
        from footprinter.connectors import has_credentials

        creds_file = tmp_path / "creds.json"
        creds_file.touch()
        config = {"google_drive": {"credentials_path": str(creds_file)}}
        assert has_credentials(_google_spec(), config) is True

    def test_has_credentials_missing(self):
        from footprinter.connectors import has_credentials

        config = {"google_drive": {"credentials_path": "/nonexistent/creds.json"}}
        assert has_credentials(_google_spec(), config) is False


# ---------------------------------------------------------------------------
# RED 2 — CLI register() + list
# ---------------------------------------------------------------------------


class TestConnectRegister:
    def test_connect_registers_parser(self):
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        assert "connect" in subs.choices

    def test_config_subcommand_registered(self):
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        connect_parser = subs.choices["connect"]
        for action in connect_parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                assert "config" in action.choices, f"'config' not in connect subcommands: {list(action.choices)}"
                break
        else:
            pytest.fail("connect has no sub-subparsers")

    def test_connect_uses_verb_dest(self):
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        connect_parser = subs.choices["connect"]
        for action in connect_parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                assert action.dest == "verb"
                break
        else:
            pytest.fail("connect has no sub-subparsers")


class TestCmdList:
    @patch("footprinter.cli.connect.get_status", return_value="available")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_list_shows_connectors(self, mock_inst, mock_status, capsys):
        from footprinter.cli.connect import _cmd_list

        _cmd_list(SimpleNamespace(json=False))
        out = capsys.readouterr().out
        assert "google" in out.lower()

    @patch("footprinter.cli.connect.get_status", return_value="available")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_list_json(self, mock_inst, mock_status, capsys):
        from footprinter.cli.connect import _cmd_list

        _cmd_list(SimpleNamespace(json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        names = [c["name"] for c in data]
        assert "google" in names


# ---------------------------------------------------------------------------
# RED 3 — CLI install
# ---------------------------------------------------------------------------


class TestCmdInstall:
    def test_cmd_install_unknown_connector(self, capsys):
        from footprinter.cli.connect import _cmd_install

        with pytest.raises(SystemExit):
            _cmd_install(SimpleNamespace(name="bogus"))
        out = capsys.readouterr().out
        assert "unknown" in out.lower() or "not found" in out.lower()

    @patch("footprinter.cli.connect._resolve_setup_hook")
    @patch("footprinter.cli.connect.is_configured", return_value=False)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_install_already_installed_runs_hook(self, mock_inst, mock_configured, mock_hook, capsys):
        from footprinter.cli.connect import _cmd_install

        mock_hook.return_value = {}
        _cmd_install(SimpleNamespace(name="google"))
        mock_hook.assert_called_once_with(_google_spec(), reconfigure=True)
        out = capsys.readouterr().out
        assert "already installed" in out.lower()

    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={})
    @patch("subprocess.check_call")
    @patch("footprinter.cli.connect.is_installed", side_effect=[False, True])
    def test_cmd_install_runs_pip(self, mock_inst, mock_pip, mock_hook, capsys):
        import sys

        from footprinter.cli.connect import _cmd_install

        _cmd_install(SimpleNamespace(name="google"))
        mock_pip.assert_called_once()
        pip_args = mock_pip.call_args[0][0]
        assert pip_args[0] == sys.executable
        assert "pip" in pip_args
        assert "footprinter-cli[google]" in pip_args

    @patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip"))
    @patch("footprinter.cli.connect.is_installed", return_value=False)
    def test_cmd_install_pip_failure(self, mock_inst, mock_pip, capsys):
        from footprinter.cli.connect import _cmd_install

        with pytest.raises(SystemExit):
            _cmd_install(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "failed" in out.lower()

    @patch("footprinter.cli.connect._update_config_enabled")
    @patch("footprinter.cli.connect._resolve_setup_hook")
    @patch("footprinter.cli.connect.is_configured", return_value=False)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_install_runs_setup_hook(self, mock_inst, mock_configured, mock_hook, mock_update, capsys):
        from footprinter.cli.connect import _cmd_install

        mock_hook.return_value = {"personal": ["drive"]}
        _cmd_install(SimpleNamespace(name="google"))
        mock_hook.assert_called_once()

    @patch("footprinter.cli.connect._update_config_enabled")
    @patch("footprinter.cli.connect._resolve_setup_hook")
    @patch("footprinter.cli.connect.is_configured", return_value=False)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_install_updates_config(self, mock_inst, mock_configured, mock_hook, mock_update, capsys):
        from footprinter.cli.connect import _cmd_install

        mock_hook.return_value = {"personal": ["drive", "gmail"]}
        _cmd_install(SimpleNamespace(name="google"))
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        result = call_args[0][0]
        assert "drive" in result["personal"]

    @patch("footprinter.cli.connect._resolve_setup_hook", side_effect=RuntimeError("OAuth token expired"))
    @patch("footprinter.cli.connect.is_configured", return_value=False)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_install_hook_failure_prints_error(self, mock_inst, mock_configured, mock_hook, capsys):
        from footprinter.cli.connect import _cmd_install

        with pytest.raises(SystemExit):
            _cmd_install(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "setup failed" in out.lower()
        assert "OAuth token expired" in out


# ---------------------------------------------------------------------------
# RED — config subcommand
# ---------------------------------------------------------------------------


class TestCmdConfig:
    def test_cmd_config_unknown_connector(self, capsys):
        from footprinter.cli.connect import _cmd_config

        with pytest.raises(SystemExit):
            _cmd_config(SimpleNamespace(name="bogus"))
        out = capsys.readouterr().out
        assert "unknown" in out.lower()

    @patch("footprinter.cli.connect.is_installed", return_value=False)
    def test_cmd_config_not_installed_exits_with_guidance(self, mock_inst, capsys):
        from footprinter.cli.connect import _cmd_config

        with pytest.raises(SystemExit):
            _cmd_config(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "fp connect install" in out

    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={})
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_config_calls_hook_with_reconfigure(self, mock_inst, mock_hook, capsys):
        from footprinter.cli.connect import _cmd_config

        _cmd_config(SimpleNamespace(name="google"))
        mock_hook.assert_called_once_with(_google_spec(), reconfigure=True)

    @patch("footprinter.cli.connect._update_config_enabled")
    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={"personal": ["drive"]})
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_config_updates_config_on_result(self, mock_inst, mock_hook, mock_update, capsys):
        from footprinter.cli.connect import _cmd_config

        _cmd_config(SimpleNamespace(name="google"))
        mock_update.assert_called_once()
        args = mock_update.call_args[0]
        assert args[0] == {"personal": ["drive"]}
        assert args[1].name == "google"  # spec


# ---------------------------------------------------------------------------
# RED — _resolve_setup_hook error paths
# ---------------------------------------------------------------------------


class TestResolveSetupHook:
    """Direct tests for _resolve_setup_hook failure modes."""

    def _make_spec(self):
        return SimpleNamespace(setup_hook="some.module.setup_fn")

    @patch("importlib.import_module", side_effect=ImportError("No module named 'some.module'"))
    def test_resolve_setup_hook_import_error(self, mock_import):
        from footprinter.cli.connect import _resolve_setup_hook

        with pytest.raises(ImportError, match="No module named"):
            _resolve_setup_hook(self._make_spec())

    @patch("importlib.import_module")
    def test_resolve_setup_hook_missing_function(self, mock_import):
        from footprinter.cli.connect import _resolve_setup_hook

        fake_mod = SimpleNamespace()  # no setup_fn attribute
        mock_import.return_value = fake_mod
        with pytest.raises(AttributeError):
            _resolve_setup_hook(self._make_spec())

    @patch("importlib.import_module")
    def test_resolve_setup_hook_hook_raises(self, mock_import):
        from footprinter.cli.connect import _resolve_setup_hook

        fake_mod = SimpleNamespace(setup_fn=MagicMock(side_effect=RuntimeError("hook crashed")))
        mock_import.return_value = fake_mod
        with pytest.raises(RuntimeError, match="hook crashed"):
            _resolve_setup_hook(self._make_spec())

    def test_resolve_setup_hook_malformed_path(self):
        from footprinter.cli.connect import _resolve_setup_hook

        spec = SimpleNamespace(setup_hook="nodots")
        with pytest.raises(ValueError, match="dotted path"):
            _resolve_setup_hook(spec)


# ---------------------------------------------------------------------------
# RED 4 — CLI remove
# ---------------------------------------------------------------------------


class TestCmdRemove:
    def test_cmd_remove_unknown_connector(self, capsys):
        from footprinter.cli.connect import _cmd_remove

        with pytest.raises(SystemExit):
            _cmd_remove(SimpleNamespace(name="bogus"))
        out = capsys.readouterr().out
        assert "unknown" in out.lower()

    @patch("footprinter.cli.connect.is_installed", return_value=False)
    def test_cmd_remove_not_installed(self, mock_inst, capsys):
        from footprinter.cli.connect import _cmd_remove

        _cmd_remove(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "not installed" in out.lower()

    @patch("footprinter.cli.connect._disable_config_sections")
    @patch("subprocess.check_call")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_remove_runs_pip_uninstall(self, mock_inst, mock_pip, mock_cfg, capsys):
        from footprinter.cli.connect import _cmd_remove

        _cmd_remove(SimpleNamespace(name="google"))
        mock_pip.assert_called_once()
        pip_args = mock_pip.call_args[0][0]
        assert "uninstall" in pip_args
        # Verify the correct packages are uninstalled
        for pkg in ("google-api-python-client", "google-auth-oauthlib", "google-auth-httplib2"):
            assert pkg in pip_args

    @patch("footprinter.cli.connect._disable_config_sections")
    @patch("subprocess.check_call")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_remove_disables_config(self, mock_inst, mock_pip, mock_cfg, capsys):
        from footprinter.cli.connect import _cmd_remove

        _cmd_remove(SimpleNamespace(name="google"))
        mock_cfg.assert_called_once_with(_google_spec())


# ---------------------------------------------------------------------------
# RED 5 — CLI status
# ---------------------------------------------------------------------------


class TestCmdStatus:
    @patch("footprinter.cli.connect.get_status", return_value="available")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_status_single(self, mock_inst, mock_status, capsys):
        from footprinter.cli.connect import _cmd_status

        _cmd_status(SimpleNamespace(name="google", json=False))
        out = capsys.readouterr().out
        assert "google" in out.lower()

    @patch("footprinter.cli.connect.get_status", return_value="available")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_status_all(self, mock_inst, mock_status, capsys):
        from footprinter.cli.connect import _cmd_status

        _cmd_status(SimpleNamespace(name=None, json=False))
        out = capsys.readouterr().out
        assert "google" in out.lower()

    @patch("footprinter.cli.connect.get_status", return_value="installed")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_status_json(self, mock_inst, mock_status, capsys):
        from footprinter.cli.connect import _cmd_status

        _cmd_status(SimpleNamespace(name="google", json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["name"] == "google"
        assert data["status"] == "installed"
        assert "pipes" in data

    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_cmd_status_checks_tokens(self, mock_inst, capsys, tmp_path):
        from footprinter.cli.connect import _cmd_status

        token_file = tmp_path / "token.json"
        token_file.touch()
        config = {
            "google_drive": {
                "enabled": True,
                "credentials_path": str(tmp_path / "creds.json"),
                "accounts": [{"name": "test", "token_path": str(token_file)}],
            },
        }
        with patch("footprinter.cli.connect._load_config", return_value=config):
            _cmd_status(SimpleNamespace(name="google", json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert any(a["token_exists"] for a in data.get("accounts", []))


# ---------------------------------------------------------------------------
# Exception handler narrowing
# ---------------------------------------------------------------------------


class TestConfigExceptionHandling:
    def test_cmd_list_handles_config_error(self, capsys):
        """ConfigError when loading config should fall back to empty dict."""
        from footprinter.cli.connect import _cmd_list
        from footprinter.source_registry import ConfigError

        with patch("footprinter.source_registry.get_config", side_effect=ConfigError("no config")):
            _cmd_list(SimpleNamespace(json=True))

        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_load_config_returns_empty_on_config_error(self):
        """_load_config returns {} when get_config raises ConfigError."""
        from footprinter.cli.connect import _load_config
        from footprinter.source_registry import ConfigError

        with patch("footprinter.source_registry.get_config", side_effect=ConfigError("no config")):
            result = _load_config()

        assert result == {}

    def test_load_config_propagates_unexpected_error(self):
        """_load_config should not swallow non-ConfigError exceptions."""
        from footprinter.cli.connect import _load_config

        with patch("footprinter.source_registry.get_config", side_effect=RuntimeError("bug")):
            with pytest.raises(RuntimeError, match="bug"):
                _load_config()


class TestSubprocessExceptionHandling:
    @patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip"))
    @patch("footprinter.cli.connect.is_installed", return_value=False)
    def test_install_catches_subprocess_error(self, mock_inst, mock_pip, capsys):
        """CalledProcessError during pip install should print error and exit."""
        from footprinter.cli.connect import _cmd_install

        with pytest.raises(SystemExit):
            _cmd_install(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "failed" in out.lower()

    @patch("subprocess.check_call", side_effect=FileNotFoundError("pip not found"))
    @patch("footprinter.cli.connect.is_installed", return_value=False)
    def test_install_catches_os_error(self, mock_inst, mock_pip, capsys):
        """FileNotFoundError (pip missing) should print error and exit."""
        from footprinter.cli.connect import _cmd_install

        with pytest.raises(SystemExit):
            _cmd_install(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "failed" in out.lower()

    @patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip"))
    @patch("footprinter.cli.connect._disable_config_sections")
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_uninstall_catches_subprocess_error(self, mock_inst, mock_cfg, mock_pip, capsys):
        """CalledProcessError during pip uninstall should print error and exit."""
        from footprinter.cli.connect import _cmd_remove

        with pytest.raises(SystemExit):
            _cmd_remove(SimpleNamespace(name="google"))
        out = capsys.readouterr().out
        assert "failed" in out.lower()


# ---------------------------------------------------------------------------
# RED 6 — Integration
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_fp_connect_list_exits_zero(self):
        from conftest import run_fp

        stdout, stderr, code = run_fp("connect", "list")
        assert code == 0

    def test_fp_connect_help_shows_verbs(self):
        from conftest import run_fp

        stdout, stderr, code = run_fp("connect", "--help")
        assert code == 0
        for verb in ("list", "install", "remove", "status", "config"):
            assert verb in stdout

    def test_fp_connect_config_help_exits_zero(self):
        from conftest import run_fp

        stdout, stderr, code = run_fp("connect", "config", "--help")
        assert code == 0


# ---------------------------------------------------------------------------
# Install already-configured prompt tests
# ---------------------------------------------------------------------------


class TestInstallAlreadyConfiguredPrompt:
    """_cmd_install should prompt before reconfiguring an already-configured connector."""

    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={})
    @patch("footprinter.cli.connect.is_configured", return_value=True)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_already_configured_prompts_user(self, mock_inst, mock_configured, mock_hook, capsys):
        """When installed + configured, a confirmation prompt is shown."""
        from footprinter.cli.connect import _cmd_install

        with patch("footprinter.cli.connect.Confirm.ask", return_value=True) as mock_confirm:
            _cmd_install(SimpleNamespace(name="google"))
        mock_confirm.assert_called_once()
        prompt_text = str(mock_confirm.call_args)
        assert "already configured" in prompt_text.lower() or "reconfigure" in prompt_text.lower()

    @patch("footprinter.cli.connect._resolve_setup_hook")
    @patch("footprinter.cli.connect.is_configured", return_value=True)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_already_configured_default_no_keeps_config(self, mock_inst, mock_configured, mock_hook, capsys):
        """Declining keeps config, hook NOT called."""
        from footprinter.cli.connect import _cmd_install

        with patch("footprinter.cli.connect.Confirm.ask", return_value=False):
            _cmd_install(SimpleNamespace(name="google"))
        mock_hook.assert_not_called()

    @patch("footprinter.cli.connect._update_config_enabled")
    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={})
    @patch("footprinter.cli.connect.is_configured", return_value=True)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_already_configured_yes_runs_hook(self, mock_inst, mock_configured, mock_hook, mock_update, capsys):
        """Accepting runs hook as before."""
        from footprinter.cli.connect import _cmd_install

        with patch("footprinter.cli.connect.Confirm.ask", return_value=True):
            _cmd_install(SimpleNamespace(name="google"))
        mock_hook.assert_called_once()

    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={})
    @patch("footprinter.cli.connect.is_configured", return_value=False)
    @patch("footprinter.cli.connect.is_installed", return_value=True)
    def test_installed_not_configured_no_prompt(self, mock_inst, mock_configured, mock_hook, capsys):
        """Installed but not configured -> runs hook directly, no prompt."""
        from footprinter.cli.connect import _cmd_install

        with patch("footprinter.cli.connect.Confirm.ask") as mock_confirm:
            _cmd_install(SimpleNamespace(name="google"))
        mock_confirm.assert_not_called()
        mock_hook.assert_called_once()

    @patch("footprinter.cli.connect._resolve_setup_hook", return_value={})
    @patch("subprocess.check_call")
    @patch("footprinter.cli.connect.is_installed", side_effect=[False, True])
    def test_fresh_install_no_prompt(self, mock_inst, mock_pip, mock_hook, capsys):
        """Not installed -> installs and runs hook, no prompt."""
        from footprinter.cli.connect import _cmd_install

        with patch("footprinter.cli.connect.Confirm.ask") as mock_confirm:
            _cmd_install(SimpleNamespace(name="google"))
        mock_confirm.assert_not_called()
        mock_hook.assert_called_once()


# ---------------------------------------------------------------------------
# Empty-state messaging
# ---------------------------------------------------------------------------


class TestCmdListEmptyState:
    """_cmd_list should show an inviting message when no connectors are discovered."""

    def test_cmd_list_empty_shows_message(self, monkeypatch, capsys):
        monkeypatch.setattr("footprinter.cli.connect.discover_connectors", lambda: {})
        from footprinter.cli.connect import _cmd_list

        _cmd_list(SimpleNamespace(json=False))
        out = capsys.readouterr().out
        assert "No connectors" in out
        assert "https://github.com/harringjohn/footprinter" in out

    def test_cmd_list_empty_json(self, monkeypatch, capsys):
        monkeypatch.setattr("footprinter.cli.connect.discover_connectors", lambda: {})
        from footprinter.cli.connect import _cmd_list

        _cmd_list(SimpleNamespace(json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data == []


class TestConnectBaseEmptyState:
    """Base `fp connect` (no verb) should show inviting message when no connectors."""

    def test_connect_base_empty_shows_message(self, monkeypatch, capsys):
        monkeypatch.setattr("footprinter.cli.connect.discover_connectors", lambda: {})
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        connect_parser = subs.choices["connect"]
        # Parse with no verb → triggers default func
        args = connect_parser.parse_args([])
        args.func(args)
        out = capsys.readouterr().out
        assert "Connectors add support" in out
        assert "No connectors" in out
        assert "https://github.com/harringjohn/footprinter" in out

    def test_connect_base_with_connectors_shows_help(self, capsys):
        """With connectors present (autouse fixture), base handler shows help."""
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        connect_parser = subs.choices["connect"]
        args = connect_parser.parse_args([])
        args.func(args)
        out = capsys.readouterr().out
        for verb in ("list", "install", "remove", "status", "config"):
            assert verb in out


# ---------------------------------------------------------------------------
# _update_config_enabled dispatches via spec.config_apply
# ---------------------------------------------------------------------------


class TestUpdateConfigEnabledHookDispatch:
    """_update_config_enabled must call the spec's config_apply hook."""

    @patch("footprinter.cli.setup._require_config", return_value=({}, "/tmp/fake.yaml"))
    @patch("footprinter.cli.setup.write_config")
    def test_calls_config_apply_hook(self, mock_write, mock_config):
        from footprinter.cli.connect import _update_config_enabled

        hook_called = []
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            config_apply="test_module.apply_fn",
        )
        result = {"personal": {"services": ["drive"]}}

        with patch("footprinter.cli.connect.resolve_hook") as mock_resolve:
            mock_resolve.return_value = lambda config, res: hook_called.append((config, res))
            _update_config_enabled(result, spec)

        assert len(hook_called) == 1, "config_apply hook should have been called once"


# ---------------------------------------------------------------------------
# RED: No Google/Dropbox/dead-URL references in help text
# ---------------------------------------------------------------------------

BANNED_STRINGS = ("Google", "Gmail", "Dropbox", "footprinter.dev")


class TestNoGoogleInHelpText:
    """Help text for fp connect and its subcommands must not reference Google/Dropbox/dead URLs."""

    def test_connect_help_no_banned_strings(self):
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        connect_parser = subs.choices["connect"]
        help_text = connect_parser.format_help()
        for banned in BANNED_STRINGS:
            assert banned not in help_text, f"fp connect --help should not contain '{banned}'"

    def test_subcommand_help_no_banned_strings(self):
        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="subcommand")
        register(subs)
        connect_parser = subs.choices["connect"]
        # Iterate subcommands (install, remove, status, config, label, list)
        for action in connect_parser._subparsers._actions:
            if hasattr(action, "choices") and action.choices:
                for name, sub_parser in action.choices.items():
                    help_text = sub_parser.format_help()
                    for banned in BANNED_STRINGS:
                        assert banned not in help_text, f"fp connect {name} --help should not contain '{banned}'"
