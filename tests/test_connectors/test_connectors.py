"""Tests for footprinter.connectors — connector registry and discovery."""

import dataclasses

import pytest

from footprinter.connectors import AuthType, ConnectorSpec


def _google_spec() -> ConnectorSpec:
    """Build the Google ConnectorSpec for testing.

    The spec is no longer a module-level constant.
    Tests that need a realistic spec construct it here.
    """
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
# RED 1 — ConnectorSpec and discover_connectors live in connectors package
# ---------------------------------------------------------------------------


class TestConnectorSpec:
    def test_connectors_package_importable(self):
        assert ConnectorSpec is not None

    def test_google_spec_fields(self):
        spec = _google_spec()
        assert spec.extra == "google"
        assert spec.probe_module == "google.auth"
        assert "drive_files" in spec.pipes
        assert "gmail" in spec.pipes

    def test_connector_spec_frozen(self):
        spec = _google_spec()
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "changed"

    def test_services_field_default(self):
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        assert spec.services == ()

    def test_google_services(self):
        assert _google_spec().services == ("drive", "gmail")

    def test_google_seed_prefix(self):
        assert _google_spec().seed_prefix == "gdrive"

    def test_schema_extensions_default_empty(self):
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        assert spec.schema_extensions == {}

    def test_auth_type_default(self):
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        assert spec.auth_type == AuthType.OAUTH2

    def test_google_auth_type(self):
        assert _google_spec().auth_type == AuthType.OAUTH2

    def test_google_has_schema_extensions(self):
        extensions = _google_spec().schema_extensions
        assert "folders" in extensions
        col_names = [name for name, _ in extensions["folders"]]
        assert "web_link" in col_names

    def test_check_auth_default_empty(self):
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        assert spec.check_auth == ""

    def test_check_auth_custom_value(self):
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            check_auth="my_module.check.verify",
        )
        assert spec.check_auth == "my_module.check.verify"

    def test_hook_fields_default_empty(self):
        """New hook fields (config_apply, health_check, read_file, seed_label_fn)
        must exist and default to empty string."""
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        assert spec.config_apply == ""
        assert spec.health_check == ""
        assert spec.read_file == ""
        assert spec.seed_label_fn == ""

    def test_hook_fields_accept_values(self):
        """Hook fields accept dotted path strings."""
        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            config_apply="my.module.apply",
            health_check="my.module.health",
            read_file="my.module.read",
            seed_label_fn="my.module.label",
        )
        assert spec.config_apply == "my.module.apply"
        assert spec.health_check == "my.module.health"
        assert spec.read_file == "my.module.read"
        assert spec.seed_label_fn == "my.module.label"


class TestResolveHook:
    """resolve_hook() must resolve dotted paths to callables."""

    def test_resolves_stdlib_function(self):
        from footprinter.connectors import resolve_hook

        fn = resolve_hook("os.path.join")
        import os.path

        assert fn is os.path.join

    def test_returns_none_for_empty_string(self):
        from footprinter.connectors import resolve_hook

        assert resolve_hook("") is None

    def test_raises_on_bad_path(self):
        from footprinter.connectors import resolve_hook

        with pytest.raises((ImportError, AttributeError)):
            resolve_hook("nonexistent.module.func")

    def test_raises_on_no_dot_path(self):
        from footprinter.connectors import resolve_hook

        with pytest.raises(ValueError, match="dotted path"):
            resolve_hook("nodots")


# ---------------------------------------------------------------------------
# RED — resolve_check_auth()
# ---------------------------------------------------------------------------


class TestResolveCheckAuth:
    def _make_spec(self, check_auth: str = "") -> ConnectorSpec:
        return ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            check_auth=check_auth,
        )

    def test_returns_none_when_empty(self):
        from footprinter.connectors import resolve_check_auth

        spec = self._make_spec(check_auth="")
        assert resolve_check_auth(spec, {}) is None

    def test_resolves_and_calls_callable(self):
        from unittest.mock import MagicMock, patch

        from footprinter.connectors import resolve_check_auth

        spec = self._make_spec(check_auth="footprinter.connectors._fake_check.verify")

        mock_mod = MagicMock()
        mock_mod.verify.return_value = "authenticated"
        with patch("footprinter.connectors.importlib.import_module", return_value=mock_mod) as mock_import:
            result = resolve_check_auth(spec, {"key": "val"})

        mock_import.assert_called_once_with("footprinter.connectors._fake_check")
        mock_mod.verify.assert_called_once_with({"key": "val"})
        assert result == "authenticated"

    def test_returns_error_on_exception(self):
        from unittest.mock import patch

        from footprinter.connectors import resolve_check_auth

        spec = self._make_spec(check_auth="bad_module.check")

        with patch("footprinter.connectors.importlib.import_module", side_effect=ImportError("no such module")):
            result = resolve_check_auth(spec, {})

        assert result == "error"

    def test_coerces_return_to_string(self):
        from unittest.mock import MagicMock, patch

        from footprinter.connectors import resolve_check_auth

        spec = self._make_spec(check_auth="some_module.check_fn")

        mock_mod = MagicMock()
        mock_mod.check_fn.return_value = True
        with patch("footprinter.connectors.importlib.import_module", return_value=mock_mod):
            result = resolve_check_auth(spec, {})

        assert result == "True"
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# RED — discover_connectors() entry-point discovery
# ---------------------------------------------------------------------------


class TestDiscoverConnectors:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from footprinter.connectors import discover_connectors

        discover_connectors.cache_clear()

    def test_discover_connectors_empty(self):
        """With no entry points registered, returns empty dict."""
        from unittest.mock import patch

        from footprinter.connectors import discover_connectors

        with patch("importlib.metadata.entry_points", return_value=[]):
            result = discover_connectors()

        assert isinstance(result, dict)
        assert result == {}

    def test_discover_connectors_finds_entry_point(self):
        """Finds and loads a valid entry point returning a ConnectorSpec."""
        from unittest.mock import MagicMock, patch

        from footprinter.connectors import ConnectorSpec, discover_connectors

        fake_spec = ConnectorSpec(
            name="fake",
            extra="fake",
            description="Fake connector",
            pipes=("fake_pipe",),
            probe_module="os",
            config_sections=("fake_section",),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        ep = MagicMock()
        ep.name = "fake"
        ep.load.return_value = fake_spec

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = discover_connectors()

        assert "fake" in result
        assert result["fake"] is fake_spec

    def test_discover_connectors_calls_callable(self):
        """If entry point loads a callable (factory), it's called to get the spec."""
        from unittest.mock import MagicMock, patch

        from footprinter.connectors import ConnectorSpec, discover_connectors

        fake_spec = ConnectorSpec(
            name="fake",
            extra="fake",
            description="Fake connector",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        factory = MagicMock(return_value=fake_spec)
        ep = MagicMock()
        ep.name = "fake"
        ep.load.return_value = factory

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = discover_connectors()

        factory.assert_called_once()
        assert result["fake"] is fake_spec

    def test_discover_connectors_skips_bad_entry_point(self):
        """Bad entry points are skipped gracefully with a warning."""
        from unittest.mock import MagicMock, patch

        from footprinter.connectors import discover_connectors

        ep = MagicMock()
        ep.name = "broken"
        ep.load.side_effect = Exception("kaboom")

        with patch("importlib.metadata.entry_points", return_value=[ep]):
            result = discover_connectors()

        assert result == {}

    def test_discover_connectors_caches_result(self):
        """Second call returns cached result without re-scanning entry points."""
        from unittest.mock import MagicMock, patch

        from footprinter.connectors import ConnectorSpec, discover_connectors

        spec = ConnectorSpec(
            name="cached",
            extra="cached",
            description="Cached connector",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
        )
        ep = MagicMock()
        ep.name = "cached"
        ep.load.return_value = spec

        with patch("importlib.metadata.entry_points", return_value=[ep]) as mock_eps:
            first = discover_connectors()
            second = discover_connectors()

        mock_eps.assert_called_once()
        assert first is second


class TestGetConnectorPipesParam:
    def test_accepts_connectors_param_empty(self):
        """Passing connectors={} returns empty dict without calling discovery."""
        from footprinter.connectors import get_connector_pipes

        result = get_connector_pipes(connectors={})
        assert result == {}

    def test_uses_provided_connectors(self):
        """Uses the provided connectors dict instead of calling discover_connectors."""
        from unittest.mock import patch

        from footprinter.connectors import ConnectorSpec, get_connector_pipes

        fake_spec = ConnectorSpec(
            name="fake",
            extra="fake",
            description="Fake",
            pipes=("fake_pipe",),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            adapter_entries={"fake_pipe": "os:getcwd"},
        )

        with patch("footprinter.connectors.is_installed", return_value=True):
            result = get_connector_pipes(connectors={"fake": fake_spec})

        assert "fake_pipe" in result


class TestGetSchemaSpecsParam:
    def test_accepts_connectors_param_empty(self):
        """Passing connectors={} returns empty list."""
        from footprinter.connectors import get_schema_specs

        result = get_schema_specs(connectors={})
        assert result == []


# ---------------------------------------------------------------------------
# RED — AuthType enum
# ---------------------------------------------------------------------------


class TestAuthType:
    def test_auth_type_importable(self):
        from footprinter.connectors import AuthType

        assert AuthType is not None

    def test_auth_type_values(self):
        from footprinter.connectors import AuthType

        assert AuthType.OAUTH2.value == "oauth2"
        assert AuthType.BEARER.value == "bearer"
        assert AuthType.IMPORT.value == "import"
        assert AuthType.FILESYSTEM.value == "filesystem"

    def test_auth_type_is_str(self):
        from footprinter.connectors import AuthType

        assert isinstance(AuthType.OAUTH2, str)


# ---------------------------------------------------------------------------
# RED 2 — get_connector_pipes() discovery
# ---------------------------------------------------------------------------


class TestGetConnectorSources:
    def test_returns_dict(self):
        from footprinter.connectors import get_connector_pipes

        result = get_connector_pipes(connectors={})
        assert isinstance(result, dict)


    def test_skips_uninstalled_connectors(self):
        from unittest.mock import patch

        from footprinter.connectors import get_connector_pipes

        connectors = {"google": _google_spec()}
        with patch("footprinter.connectors.is_installed", return_value=False):
            result = get_connector_pipes(connectors=connectors)

        assert result == {}

    def test_no_import_when_uninstalled(self):
        from unittest.mock import patch

        from footprinter.connectors import get_connector_pipes

        connectors = {"google": _google_spec()}
        with (
            patch("footprinter.connectors.is_installed", return_value=False),
            patch("importlib.import_module") as mock_import,
        ):
            get_connector_pipes(connectors=connectors)

        mock_import.assert_not_called()


# ---------------------------------------------------------------------------
# RED 3 — get_status() three-way status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_not_available_when_packages_missing(self):
        from unittest.mock import patch

        from footprinter.connectors import get_status

        spec = _google_spec()
        with patch("footprinter.connectors.is_installed", return_value=False):
            assert get_status(spec, {}) == "not available"

    def test_available_when_packages_present_but_unconfigured(self):
        from unittest.mock import patch

        from footprinter.connectors import get_status

        spec = _google_spec()
        config = {"google_drive": {"enabled": False}}
        with patch("footprinter.connectors.is_installed", return_value=True):
            assert get_status(spec, config) == "available"

    def test_available_when_config_section_missing(self):
        from unittest.mock import patch

        from footprinter.connectors import get_status

        spec = _google_spec()
        with patch("footprinter.connectors.is_installed", return_value=True):
            assert get_status(spec, {}) == "available"

    def test_installed_when_configured(self):
        from unittest.mock import patch

        from footprinter.connectors import get_status

        spec = _google_spec()
        config = {"google_drive": {"enabled": True}}
        with patch("footprinter.connectors.is_installed", return_value=True):
            assert get_status(spec, config) == "installed"


class TestCmdListJson:
    def test_json_output_uses_status_field(self):
        """JSON output should have 'status' string, not 'installed'/'configured' bools."""
        from io import StringIO
        from types import SimpleNamespace
        from unittest.mock import patch

        from footprinter.cli.connect import _cmd_list

        _buf = StringIO()  # noqa: F841
        args = SimpleNamespace(json=True)
        config = {"google_drive": {"enabled": False}}

        with (
            patch("footprinter.source_registry.get_config", return_value=config),
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.cli.connect.discover_connectors", return_value={"google": _google_spec()}),
            patch("footprinter.cli.connect.output_json") as mock_json,
        ):
            _cmd_list(args)

        rows = mock_json.call_args[0][0]
        assert len(rows) >= 1
        row = rows[0]
        assert "status" in row
        assert row["status"] == "available"
        assert "installed" not in row
        assert "configured" not in row


# ---------------------------------------------------------------------------
# RED 4 — _status_dict() / _print_status_panel() gate on configured
# ---------------------------------------------------------------------------


class TestStatusDictAccountVisibility:
    def _make_config(self, *, enabled: bool) -> dict:
        return {
            "google_drive": {
                "enabled": enabled,
                "accounts": [
                    {"name": "personal", "token_path": "/tmp/fake_token.json"},
                ],
            },
        }

    def test_hides_accounts_when_not_configured(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config(enabled=False)

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert result["accounts"] == []

    def test_shows_accounts_when_configured(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config(enabled=True)

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert len(result["accounts"]) == 1
        assert result["accounts"][0]["name"] == "personal"


class TestStatusPanelAccountVisibility:
    def test_no_token_lines_when_not_configured(self):
        from io import StringIO
        from unittest.mock import patch

        from rich.console import Console

        from footprinter.cli.connect import _print_status_panel

        spec = _google_spec()
        config = {
            "google_drive": {
                "enabled": False,
                "accounts": [
                    {"name": "personal", "token_path": "/tmp/fake_token.json"},
                ],
            },
        }

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=120)

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
            patch("footprinter.cli.connect.console", test_console),
        ):
            _print_status_panel(spec, config)

        output = buf.getvalue()
        assert "token" not in output.lower()
        assert "fp connect install" in output


# ---------------------------------------------------------------------------
# RED — _status_dict() and _print_status_panel() auth support
# ---------------------------------------------------------------------------


class TestStatusDictAuth:
    def _make_config(self, *, enabled: bool = True) -> dict:
        return {
            "google_drive": {
                "enabled": enabled,
                "accounts": [
                    {"name": "personal", "token_path": "/tmp/fake_token.json"},
                ],
            },
        }

    def test_auth_field_from_check_auth(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config()

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.cli.connect._resolve_auth_label", return_value="authenticated"),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert result["auth"] == "authenticated"

    def test_auth_fallback_credentials_found(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config()

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.cli.connect._resolve_auth_label", return_value="credentials found"),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert result["auth"] == "credentials found"

    def test_auth_fallback_no_credentials(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config()

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.cli.connect._resolve_auth_label", return_value="no credentials"),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert result["auth"] == "no credentials"

    def test_auth_uses_real_check_auth_callable(self):
        """Integration: _status_dict dispatches through resolve_check_auth with a real spec."""
        from unittest.mock import MagicMock, patch

        from footprinter.cli.connect import _status_dict

        spec = ConnectorSpec(
            name="test",
            extra="test",
            description="test",
            pipes=(),
            probe_module="os",
            config_sections=("test_section",),
            setup_hook="os.getcwd",
            remove_packages=(),
            check_auth="test_module.verify",
        )
        config = {"test_section": {"enabled": True}}

        mock_mod = MagicMock()
        mock_mod.verify.return_value = "authenticated"
        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.connectors.importlib.import_module", return_value=mock_mod),
        ):
            result = _status_dict(spec, config)

        assert result["auth"] == "authenticated"
        mock_mod.verify.assert_called_once_with(config)


class TestStatusPanelAuth:
    def test_shows_auth_line(self):
        from io import StringIO
        from unittest.mock import patch

        from rich.console import Console

        from footprinter.cli.connect import _print_status_panel

        spec = _google_spec()
        config = {
            "google_drive": {
                "enabled": True,
                "accounts": [
                    {"name": "personal", "token_path": "/tmp/fake_token.json"},
                ],
            },
        }

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=120)

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("footprinter.cli.connect._resolve_auth_label", return_value="authenticated"),
            patch("pathlib.Path.exists", return_value=True),
            patch("footprinter.cli.connect.console", test_console),
        ):
            _print_status_panel(spec, config)

        output = buf.getvalue()
        assert "authenticated" in output.lower()


# ---------------------------------------------------------------------------
# RED 5 — connectors/google/ placeholder package exists
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RED 6 — config_utils: credential_path and source_seed_entry
# ---------------------------------------------------------------------------


class TestConfigUtils:
    def test_credential_path(self):
        from pathlib import Path

        from footprinter.connectors.config_utils import credential_path

        result = credential_path("google", "personal")
        # Returns portable tilde-form; callers expand for filesystem ops
        assert result == Path("~/.config/footprinter/google_personal_token.json")
        assert result.expanduser().is_absolute()

    def test_source_seed_entry(self):
        from footprinter.connectors.config_utils import source_seed_entry

        result = source_seed_entry("remote", "personal")
        assert result["name"] == "remote_personal"
        assert result["source_type"] == "remote"
        assert result["account"] == "personal"
        assert result["label"] == "Remote (personal)"
        assert result["icon"] == "cloud"
        assert result["enabled"] is True

    def test_source_seed_entry_preserves_source_type(self):
        from footprinter.connectors.config_utils import source_seed_entry

        for source_type in ("drive", "gmail", "custom_type"):
            result = source_seed_entry(source_type, "work")
            assert result["source_type"] == source_type


class TestRegisterConnectorSchema:
    def _make_db(self, tables: dict[str, str]) -> "sqlite3.Connection":  # noqa: F821
        """Create an in-memory DB with the given tables (name → column SQL)."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        for name, cols in tables.items():
            conn.execute(f"CREATE TABLE {name} ({cols})")
        return conn

    def _get_columns(self, conn, table: str) -> set[str]:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def test_adds_column(self):
        from footprinter.ingest.db.connector_schema import register_connector_schema

        conn = self._make_db({"folders": "id INTEGER PRIMARY KEY"})
        register_connector_schema(conn, {"folders": [("web_link", "TEXT")]})
        assert "web_link" in self._get_columns(conn, "folders")

    def test_idempotent(self):
        from footprinter.ingest.db.connector_schema import register_connector_schema

        conn = self._make_db({"folders": "id INTEGER PRIMARY KEY"})
        register_connector_schema(conn, {"folders": [("web_link", "TEXT")]})
        register_connector_schema(conn, {"folders": [("web_link", "TEXT")]})
        assert "web_link" in self._get_columns(conn, "folders")

    def test_multiple_columns_same_table(self):
        from footprinter.ingest.db.connector_schema import register_connector_schema

        conn = self._make_db({"folders": "id INTEGER PRIMARY KEY"})
        register_connector_schema(
            conn,
            {
                "folders": [("web_link", "TEXT"), ("drive_id", "TEXT")],
            },
        )
        cols = self._get_columns(conn, "folders")
        assert "web_link" in cols
        assert "drive_id" in cols

    def test_multiple_tables(self):
        from footprinter.ingest.db.connector_schema import register_connector_schema

        conn = self._make_db(
            {
                "folders": "id INTEGER PRIMARY KEY",
                "files": "id INTEGER PRIMARY KEY",
            }
        )
        register_connector_schema(
            conn,
            {
                "folders": [("web_link", "TEXT")],
                "files": [("drive_url", "TEXT")],
            },
        )
        assert "web_link" in self._get_columns(conn, "folders")
        assert "drive_url" in self._get_columns(conn, "files")


# ---------------------------------------------------------------------------
# RED — init_connector_schemas() apply
# ---------------------------------------------------------------------------


class TestInitConnectorSchemas:
    def _make_db_with_folders(self) -> "sqlite3.Connection":  # noqa: F821
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY)")
        return conn

    def _get_columns(self, conn, table: str) -> set[str]:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}

    def test_adds_columns_for_specs_with_extensions(self):
        from footprinter.ingest.db.connector_schema import init_connector_schemas

        conn = self._make_db_with_folders()
        init_connector_schemas(conn, [_google_spec()])
        assert "web_link" in self._get_columns(conn, "folders")

    def test_empty_specs_adds_no_columns(self):
        from footprinter.ingest.db.connector_schema import init_connector_schemas

        conn = self._make_db_with_folders()
        init_connector_schemas(conn, [])
        assert "web_link" not in self._get_columns(conn, "folders")


# ---------------------------------------------------------------------------
# RED — Integration: fresh DB + connector schema produces expected result
# ---------------------------------------------------------------------------


class TestConnectorSchemaIntegration:
    def test_fresh_db_with_google_extension_has_web_link(self, tmp_path):
        """Database + Google connector schema produces web_link on folders."""
        from footprinter.ingest.database import Database
        from footprinter.ingest.db.connector_schema import register_connector_schema

        db = Database(str(tmp_path / "test.db"))
        # Simulate Google connector being installed
        register_connector_schema(db.conn, {"folders": [("web_link", "TEXT")]})
        cursor = db.conn.execute("PRAGMA table_info(folders)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "web_link" in columns
        db.close()

    def test_web_link_not_in_core_schema(self):
        """web_link is not in the core folders CREATE TABLE — it's connector-scope."""
        from tests.test_db.test_db_schema import EXPECTED_COLUMNS

        assert "web_link" not in EXPECTED_COLUMNS["folders"]


# ---------------------------------------------------------------------------
# RED — account_label() helper
# ---------------------------------------------------------------------------


class TestAccountLabel:
    def test_returns_label_when_present(self):
        from footprinter.connectors.config_utils import account_label

        assert account_label({"name": "work", "label": "Consulting"}) == "Consulting"

    def test_falls_back_to_name(self):
        from footprinter.connectors.config_utils import account_label

        assert account_label({"name": "work"}) == "work"

    def test_falls_back_when_label_empty(self):
        from footprinter.connectors.config_utils import account_label

        assert account_label({"name": "work", "label": ""}) == "work"


class TestStatusDictLabel:
    def _make_config(self, *, label: str | None = None) -> dict:
        acct: dict = {"name": "work", "token_path": "/tmp/fake_token.json"}
        if label is not None:
            acct["label"] = label
        return {"google_drive": {"enabled": True, "accounts": [acct]}}

    def test_includes_label(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config(label="Consulting")

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert result["accounts"][0]["label"] == "Consulting"

    def test_label_fallback(self):
        from unittest.mock import patch

        from footprinter.cli.connect import _status_dict

        spec = _google_spec()
        config = self._make_config()

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = _status_dict(spec, config)

        assert result["accounts"][0]["label"] == "work"


class TestStatusPanelLabel:
    def test_shows_label_instead_of_name(self):
        from io import StringIO
        from unittest.mock import patch

        from rich.console import Console

        from footprinter.cli.connect import _print_status_panel

        spec = _google_spec()
        config = {
            "google_drive": {
                "enabled": True,
                "accounts": [
                    {"name": "work", "label": "Consulting", "token_path": "/tmp/fake_token.json"},
                ],
            },
        }

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=120)

        with (
            patch("footprinter.connectors.is_installed", return_value=True),
            patch("pathlib.Path.exists", return_value=True),
            patch("footprinter.cli.connect.console", test_console),
        ):
            _print_status_panel(spec, config)

        output = buf.getvalue()
        assert "Consulting" in output


# ---------------------------------------------------------------------------
# RED — get_source_health() label support
# ---------------------------------------------------------------------------


class TestSourceHealthConnectorRows:
    """get_source_health returns connector_rows via health_check hooks."""

    def test_health_includes_connector_rows_key(self):
        from unittest.mock import patch

        from footprinter.cli.status import get_source_health

        with patch("footprinter.cli.status.discover_connectors", return_value={}):
            health = get_source_health({})

        assert "connector_rows" in health
        assert health["connector_rows"] == []

    def test_health_includes_remote_enabled_flag(self):
        from unittest.mock import patch

        from footprinter.cli.status import get_source_health

        with patch("footprinter.cli.status.discover_connectors", return_value={}):
            health = get_source_health({})

        assert health["remote_enabled"] is False


# ---------------------------------------------------------------------------
# RED — fp connect label CLI verb
# ---------------------------------------------------------------------------


class TestLabelCommand:
    def _make_config(self) -> dict:
        return {
            "google_drive": {
                "enabled": True,
                "accounts": [
                    {"name": "work", "token_path": "~/.config/footprinter/google_work_token.json"},
                ],
            },
            "gmail": {
                "enabled": True,
                "accounts": [
                    {"name": "work", "token_path": "~/.config/footprinter/google_work_token.json"},
                ],
            },
            "source_seeds": [
                {
                    "name": "gdrive_work",
                    "source_type": "remote",
                    "account": "work",
                    "label": "Drive (work)",
                    "icon": "cloud",
                    "enabled": True,
                },
            ],
        }


    def test_unknown_connector_exits(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from footprinter.cli.connect import _cmd_label

        args = SimpleNamespace(name="bogus", account="work", label="New")

        with (
            patch("footprinter.cli.connect.discover_connectors", return_value={"google": _google_spec()}),
            pytest.raises(SystemExit),
        ):
            _cmd_label(args)

    def test_unknown_account_exits(self, tmp_path):
        from unittest.mock import patch

        import yaml

        from footprinter.cli.connect import _cmd_label

        config = self._make_config()
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))

        args = type("Args", (), {"name": "google", "account": "nonexistent", "label": "New"})()

        with (
            patch("footprinter.cli.connect._require_config_for_label", return_value=(config, config_path)),
            patch("footprinter.cli.connect.discover_connectors", return_value={"google": _google_spec()}),
        ):
            with pytest.raises(SystemExit):
                _cmd_label(args)

    def test_registered_as_subcommand(self):
        """'label' should be a registered verb under 'connect'."""
        import argparse

        from footprinter.cli.connect import register

        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers()
        register(subs)

        # Parse a 'connect label' command to verify it's registered
        result = parser.parse_args(["connect", "label", "google", "work", "Consulting"])
        assert result.account == "work"
        assert result.label == "Consulting"


# ---------------------------------------------------------------------------
# RED — setup summary shows label
# ---------------------------------------------------------------------------


class TestContentServiceRemoteDispatch:
    """_read_remote_file_bytes must dispatch via connector's read_file hook."""

    def test_dispatches_via_read_file_hook(self):
        from unittest.mock import MagicMock, patch

        spec = ConnectorSpec(
            name="fake",
            extra="fake",
            description="Fake",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="remote",
            read_file="fake_module.read_bytes",
        )

        mock_read = MagicMock(return_value=b"file content")

        # discover_connectors, is_installed, resolve_hook are imported inside
        # _read_remote_file_bytes, so patch them at the source module
        with (
            patch("footprinter.connectors.discover_connectors", return_value={"fake": spec}),
            patch("footprinter.connectors.resolve_hook", return_value=mock_read),
            patch("footprinter.connectors.is_installed", return_value=True),
        ):
            from footprinter.services.content_service import _read_remote_file_bytes

            result = _read_remote_file_bytes("remote_source", "ext123", "work", "text/plain")

        mock_read.assert_called_once_with("ext123", "work", "text/plain")
        assert result == b"file content"

    def test_routes_to_correct_connector_by_source(self):
        from unittest.mock import MagicMock, patch

        spec_a = ConnectorSpec(
            name="alpha_connector",
            extra="alpha",
            description="Alpha",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="alpha",
            read_file="alpha_mod.read_bytes",
        )
        spec_b = ConnectorSpec(
            name="beta_connector",
            extra="beta",
            description="Beta",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="beta",
            read_file="beta_mod.read_bytes",
        )

        mock_read_a = MagicMock(return_value=b"alpha data")
        mock_read_b = MagicMock(return_value=b"beta data")

        def fake_resolve(path):
            if path == "alpha_mod.read_bytes":
                return mock_read_a
            if path == "beta_mod.read_bytes":
                return mock_read_b
            return None

        connectors = {"alpha": spec_a, "beta": spec_b}

        with (
            patch("footprinter.connectors.discover_connectors", return_value=connectors),
            patch("footprinter.connectors.resolve_hook", side_effect=fake_resolve),
            patch("footprinter.connectors.is_installed", return_value=True),
        ):
            from footprinter.services.content_service import _read_remote_file_bytes

            result = _read_remote_file_bytes("beta_work", "ext1", "work", "text/plain")

        mock_read_b.assert_called_once_with("ext1", "work", "text/plain")
        mock_read_a.assert_not_called()
        assert result == b"beta data"

    def test_returns_none_when_no_connector_matches_source(self):
        from unittest.mock import MagicMock, patch

        spec = ConnectorSpec(
            name="alpha_connector",
            extra="alpha",
            description="Alpha",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="alpha",
            read_file="alpha_mod.read_bytes",
        )

        mock_read = MagicMock(return_value=b"alpha data")

        with (
            patch("footprinter.connectors.discover_connectors", return_value={"alpha": spec}),
            patch("footprinter.connectors.resolve_hook", return_value=mock_read),
            patch("footprinter.connectors.is_installed", return_value=True),
        ):
            from footprinter.services.content_service import _read_remote_file_bytes

            result = _read_remote_file_bytes("unknown_work", "ext1", "work", "text/plain")

        assert result is None
        mock_read.assert_not_called()

    def test_returns_none_when_matched_connector_not_installed(self):
        from unittest.mock import MagicMock, patch

        spec = ConnectorSpec(
            name="alpha_connector",
            extra="alpha",
            description="Alpha",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="alpha",
            read_file="alpha_mod.read_bytes",
        )

        mock_read = MagicMock()

        with (
            patch("footprinter.connectors.discover_connectors", return_value={"alpha": spec}),
            patch("footprinter.connectors.resolve_hook", return_value=mock_read),
            patch("footprinter.connectors.is_installed", return_value=False),
        ):
            from footprinter.services.content_service import _read_remote_file_bytes

            result = _read_remote_file_bytes("alpha_work", "ext1", "work", "text/plain")

        assert result is None
        mock_read.assert_not_called()

    def test_prefix_collision_routes_to_longer_prefix(self):
        from unittest.mock import MagicMock, patch

        spec_short = ConnectorSpec(
            name="alpha_connector",
            extra="alpha",
            description="Alpha",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="alpha",
            read_file="alpha_mod.read_bytes",
        )
        spec_long = ConnectorSpec(
            name="alpha2_connector",
            extra="alpha2",
            description="Alpha2",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="alpha2",
            read_file="alpha2_mod.read_bytes",
        )

        mock_read_short = MagicMock(return_value=b"alpha data")
        mock_read_long = MagicMock(return_value=b"alpha2 data")

        def fake_resolve(path):
            if path == "alpha_mod.read_bytes":
                return mock_read_short
            if path == "alpha2_mod.read_bytes":
                return mock_read_long
            return None

        connectors = {"alpha": spec_short, "alpha2": spec_long}

        with (
            patch("footprinter.connectors.discover_connectors", return_value=connectors),
            patch("footprinter.connectors.resolve_hook", side_effect=fake_resolve),
            patch("footprinter.connectors.is_installed", return_value=True),
        ):
            from footprinter.services.content_service import _read_remote_file_bytes

            result = _read_remote_file_bytes("alpha2_work", "ext1", "work", "text/plain")

        mock_read_long.assert_called_once_with("ext1", "work", "text/plain")
        mock_read_short.assert_not_called()
        assert result == b"alpha2 data"

    def test_returns_none_when_matched_connector_has_no_read_file(self):
        from unittest.mock import MagicMock, patch

        spec = ConnectorSpec(
            name="alpha_connector",
            extra="alpha",
            description="Alpha",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            seed_prefix="alpha",
            read_file="",
        )

        mock_resolve = MagicMock()

        with (
            patch("footprinter.connectors.discover_connectors", return_value={"alpha": spec}),
            patch("footprinter.connectors.resolve_hook", mock_resolve),
            patch("footprinter.connectors.is_installed", return_value=True),
        ):
            from footprinter.services.content_service import _read_remote_file_bytes

            result = _read_remote_file_bytes("alpha_work", "ext1", "work", "text/plain")

        assert result is None
        mock_resolve.assert_not_called()
