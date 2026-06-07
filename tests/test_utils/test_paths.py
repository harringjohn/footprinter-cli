"""Tests for footprinter.paths — single source-of-truth path resolution."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove all FOOTPRINTER_* env vars."""
    monkeypatch.delenv("FOOTPRINTER_HOME", raising=False)
    monkeypatch.delenv("FOOTPRINTER_DB_PATH", raising=False)
    monkeypatch.delenv("FOOTPRINTER_CONFIG", raising=False)


# ---------------------------------------------------------------------------
# Default paths (no env vars)
# ---------------------------------------------------------------------------


class TestDefaults:
    """Verify default paths when no env vars are set."""

    def test_footprinter_home_default(self):
        from footprinter.paths import get_home

        result = get_home()
        assert result == Path.home() / ".footprinter"

    def test_get_home_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path / ".footprinter"))
        from footprinter.paths import get_home

        result = get_home()
        assert result == tmp_path / ".footprinter"

    def test_get_home_creates_directory(self, tmp_path, monkeypatch):
        target = tmp_path / ".footprinter"
        monkeypatch.setenv("FOOTPRINTER_HOME", str(target))
        from footprinter.paths import get_home

        assert not target.exists()
        get_home()
        assert target.is_dir()

    def test_get_db_path_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path / ".footprinter"))
        from footprinter.paths import get_db_path

        assert get_db_path() == tmp_path / ".footprinter" / "footprinter.db"

    def test_get_config_path_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path / ".footprinter"))
        from footprinter.paths import get_config_path

        assert get_config_path() == tmp_path / ".footprinter" / "config.yaml"

    def test_get_chroma_path_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path / ".footprinter"))
        from footprinter.paths import get_chroma_path

        assert get_chroma_path() == tmp_path / ".footprinter" / "chroma"

    def test_get_log_path_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path / ".footprinter"))
        from footprinter.paths import get_log_path

        assert get_log_path() == tmp_path / ".footprinter" / "setup.log"


# ---------------------------------------------------------------------------
# Env var overrides
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    """Verify env var overrides for all path functions."""

    def test_footprinter_home_override(self, tmp_path, monkeypatch):
        override = tmp_path / "custom_home"
        monkeypatch.setenv("FOOTPRINTER_HOME", str(override))
        from footprinter.paths import get_home

        assert get_home() == override

    def test_footprinter_db_path_override(self, tmp_path, monkeypatch):
        override = tmp_path / "custom" / "my.db"
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(override))
        from footprinter.paths import get_db_path

        assert get_db_path() == override

    def test_footprinter_config_override(self, tmp_path, monkeypatch):
        override = tmp_path / "custom" / "config.yaml"
        monkeypatch.setenv("FOOTPRINTER_CONFIG", str(override))
        from footprinter.paths import get_config_path

        assert get_config_path() == override


# ---------------------------------------------------------------------------
# Side effects
# ---------------------------------------------------------------------------


class TestSideEffects:
    """Verify directory creation side effects."""

    def test_get_home_creates_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "new_home"
        monkeypatch.setenv("FOOTPRINTER_HOME", str(target))
        from footprinter.paths import get_home

        assert not target.exists()
        get_home()
        assert target.is_dir()

    def test_get_db_path_creates_parent(self, tmp_path, monkeypatch):
        target = tmp_path / "deep" / "nested" / "footprinter.db"
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(target))
        from footprinter.paths import get_db_path

        assert not target.parent.exists()
        get_db_path()
        assert target.parent.is_dir()


# ---------------------------------------------------------------------------
# Bundled paths
# ---------------------------------------------------------------------------


class TestBundledPaths:
    """Verify bundled resource path resolution."""

    def test_get_bundled_path(self):
        from footprinter.paths import get_bundled_path

        result = get_bundled_path("foo.yaml")
        assert str(result).endswith("foo.yaml")

