"""
Tests for the source registry module.
"""

import os
import sqlite3
import tempfile

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_sources_table(conn: sqlite3.Connection):
    """Create the sources table in an in-memory or temp database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            name TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            adapter TEXT,
            account TEXT,
            label TEXT,
            icon TEXT,
            enabled INTEGER DEFAULT 1,
            config TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _minimal_config(seeds=None):
    """Return a minimal config dict with source_seeds."""
    return {"source_seeds": seeds or []}


def _write_config(tmp_path, config_dict):
    """Write a config dict to a YAML file and return its path."""
    path = os.path.join(tmp_path, "config.yaml")
    with open(path, "w") as f:
        yaml.dump(config_dict, f)
    return path


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection with sources table."""
    conn = sqlite3.connect(":memory:")
    _create_sources_table(conn)
    yield conn
    conn.close()


@pytest.fixture
def registry(mem_conn):
    """SourceRegistry backed by an in-memory DB."""
    from footprinter.source_registry import SourceRegistry

    return SourceRegistry(mem_conn)


@pytest.fixture
def tmp_config_dir():
    """Temporary directory for config files."""
    with tempfile.TemporaryDirectory() as d:
        yield d


# ---------------------------------------------------------------------------
# Seeding tests
# ---------------------------------------------------------------------------


class TestSeeding:
    """Test seed_from_config behavior."""

    def test_seed_inserts_rows(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "local",
                "source_type": "file",
                "adapter": "local_fs",
                "label": "Local Files",
                "icon": "folder",
            },
            {
                "name": "browser",
                "source_type": "browser",
                "adapter": "browser_indexer",
                "label": "Browser History",
                "icon": "globe",
            },
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        inserted = registry.seed_from_config(config_path=path)
        assert inserted == 2
        assert set(registry.all_source_names()) == {"local", "browser"}

    def test_seed_is_idempotent(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "email",
                "source_type": "email",
                "adapter": "gmail",
                "label": "Email",
                "icon": "envelope",
            }
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)
        inserted = registry.seed_from_config(config_path=path)
        assert inserted == 0  # already exists

    def test_seed_preserves_user_edits(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "local",
                "source_type": "file",
                "adapter": "local_fs",
                "label": "Local Files",
                "icon": "folder",
            }
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)

        # User edits the label
        registry.update_label("local", "My Custom Label")

        # Re-seed should not overwrite
        registry.seed_from_config(config_path=path)
        assert registry.source_label("local") == "My Custom Label"

    def test_seed_empty_config(self, registry, tmp_config_dir):
        path = _write_config(tmp_config_dir, _minimal_config([]))
        inserted = registry.seed_from_config(config_path=path)
        assert inserted == 0
        assert registry.all_source_names() == []

    def test_seed_with_account(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "testdrive",
                "source_type": "remote",
                "adapter": "google_drive",
                "account": "testorg",
                "label": "TestDrive",
                "icon": "cloud",
            }
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)
        assert registry.source_account("testdrive") == "testorg"

    def test_seed_enabled_default(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "local",
                "source_type": "file",
                "adapter": "local_fs",
                "label": "Local",
                "icon": "folder",
            }
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)
        source = registry.get_source("local")
        assert source["enabled"] == 1


# ---------------------------------------------------------------------------
# Query API tests
# ---------------------------------------------------------------------------


class TestQueryAPI:
    """Test read-only query methods."""

    def _seed_standard(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "local",
                "source_type": "file",
                "adapter": "local_fs",
                "label": "Local Files",
                "icon": "folder",
            },
            {
                "name": "browser",
                "source_type": "browser",
                "adapter": "browser_indexer",
                "label": "Browser History",
                "icon": "globe",
            },
            {
                "name": "email",
                "source_type": "email",
                "adapter": "gmail",
                "label": "Email",
                "icon": "envelope",
            },
            {
                "name": "chat",
                "source_type": "chat",
                "adapter": "chat_export",
                "label": "Chat",
                "icon": "message",
            },
            {
                "name": "testdrive",
                "source_type": "remote",
                "adapter": "google_drive",
                "account": "testorg",
                "label": "TestDrive",
                "icon": "cloud",
            },
            {
                "name": "personaldrive",
                "source_type": "remote",
                "adapter": "google_drive",
                "account": "work",
                "label": "PersonalDrive",
                "icon": "cloud",
            },
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)

    def test_all_source_names(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        names = registry.all_source_names()
        assert len(names) == 6
        assert "local" in names
        assert "testdrive" in names

    def test_all_sources_returns_dicts(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        sources = registry.all_sources()
        assert len(sources) == 6
        assert all(isinstance(s, dict) for s in sources)
        assert all("name" in s and "source_type" in s for s in sources)

    def test_get_source_found(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        source = registry.get_source("email")
        assert source is not None
        assert source["source_type"] == "email"
        assert source["label"] == "Email"

    def test_get_source_not_found(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        assert registry.get_source("nonexistent") is None

    def test_remote_source_names(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        names = registry.remote_source_names()
        assert set(names) == {"personaldrive", "testdrive"}

    def test_file_source_names(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        names = registry.file_source_names()
        assert names == ["local"]

    def test_source_label(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        assert registry.source_label("testdrive") == "TestDrive"

    def test_source_label_not_found(self, registry, tmp_config_dir):
        assert registry.source_label("nonexistent") is None

    def test_source_account(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        assert registry.source_account("personaldrive") == "work"

    def test_is_remote_source_true(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        assert registry.is_remote_source("testdrive") is True

    def test_is_remote_source_false(self, registry, tmp_config_dir):
        self._seed_standard(registry, tmp_config_dir)
        assert registry.is_remote_source("local") is False

    def test_is_remote_source_nonexistent(self, registry, tmp_config_dir):
        assert registry.is_remote_source("nonexistent") is False


# ---------------------------------------------------------------------------
# Mutation API tests
# ---------------------------------------------------------------------------


class TestMutationAPI:
    """Test write methods."""

    def test_update_label(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "local",
                "source_type": "file",
                "adapter": "local_fs",
                "label": "Local",
                "icon": "folder",
            }
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)

        assert registry.update_label("local", "My Files") is True
        assert registry.source_label("local") == "My Files"

    def test_update_label_nonexistent(self, registry):
        assert registry.update_label("nonexistent", "Label") is False

    def test_set_enabled(self, registry, tmp_config_dir):
        seeds = [
            {
                "name": "browser",
                "source_type": "browser",
                "adapter": "browser_indexer",
                "label": "Browser",
                "icon": "globe",
            }
        ]
        path = _write_config(tmp_config_dir, _minimal_config(seeds))
        registry.seed_from_config(config_path=path)

        assert registry.set_enabled("browser", False) is True
        source = registry.get_source("browser")
        assert source["enabled"] == 0

        assert registry.set_enabled("browser", True) is True
        source = registry.get_source("browser")
        assert source["enabled"] == 1

    def test_register_source(self, registry):
        result = registry.register_source(
            "custom",
            "plugin",
            adapter="my_adapter",
            label="Custom Source",
            icon="star",
        )
        assert result is True
        source = registry.get_source("custom")
        assert source["source_type"] == "plugin"
        assert source["label"] == "Custom Source"

    def test_register_source_duplicate(self, registry):
        registry.register_source("custom", "plugin", label="First")
        result = registry.register_source("custom", "plugin", label="Second")
        assert result is False
        # Original label preserved
        assert registry.source_label("custom") == "First"


# ---------------------------------------------------------------------------
# Module-level helper tests
# ---------------------------------------------------------------------------


class TestModuleHelpers:
    """Test module-level helper functions."""

    def test_home_path(self):
        from footprinter.source_registry import home_path

        path = home_path()
        assert os.path.isabs(path)
        assert os.path.isdir(path)

    def test_get_config_default(self):
        from footprinter.source_registry import get_config

        config = get_config()
        assert "source_seeds" in config
        assert isinstance(config["source_seeds"], list)

    def test_get_config_explicit_path(self, tmp_config_dir):
        from footprinter.source_registry import get_config

        path = _write_config(tmp_config_dir, {"source_seeds": [{"name": "test", "source_type": "test"}]})
        config = get_config(config_path=path)
        assert len(config["source_seeds"]) == 1

    def test_get_config_env_var(self, tmp_config_dir, monkeypatch):
        from footprinter.source_registry import get_config

        path = _write_config(tmp_config_dir, {"source_seeds": [{"name": "envtest", "source_type": "env"}]})
        monkeypatch.setenv("FOOTPRINTER_CONFIG", path)
        config = get_config()
        assert config["source_seeds"][0]["name"] == "envtest"

    def test_remote_accounts(self):
        from footprinter.source_registry import remote_accounts

        accounts = remote_accounts()
        assert isinstance(accounts, list)
        # May be empty if google_drive.enabled is false (example config)


# ---------------------------------------------------------------------------
# Real config integration test
# ---------------------------------------------------------------------------


class TestConfigErrors:
    """Test friendly error handling for missing/corrupt config files."""

    def test_missing_config_friendly_error(self):
        from footprinter.source_registry import ConfigError, get_config

        with pytest.raises(ConfigError, match="fp setup"):
            get_config(config_path="/nonexistent/path.yaml")

    def test_corrupt_config_friendly_error(self, tmp_config_dir):
        from footprinter.source_registry import ConfigError, get_config

        path = os.path.join(tmp_config_dir, "bad.yaml")
        with open(path, "w") as f:
            f.write("{{{")
        with pytest.raises(ConfigError, match=path):
            get_config(config_path=path)

    def test_cli_missing_config_no_traceback(self, monkeypatch, capsys):
        monkeypatch.setenv("FOOTPRINTER_CONFIG", "/nonexistent/config.yaml")
        from footprinter.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["ingest"])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fp setup" in captured.err
        assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# Real config integration test
# ---------------------------------------------------------------------------


class TestRealConfigIntegration:
    """Integration test using the actual config.yaml."""

    def test_seed_from_real_config(self, mem_conn):
        from footprinter.source_registry import SourceRegistry

        reg = SourceRegistry(mem_conn)
        inserted = reg.seed_from_config()
        # Example config has 4 seeds (local, browser, email, chat)
        # Real config may have more (e.g., drive sources)
        assert inserted >= 4
        names = reg.all_source_names()
        assert "local" in names
        assert "browser" in names
        assert "email" in names
        assert "chat" in names
