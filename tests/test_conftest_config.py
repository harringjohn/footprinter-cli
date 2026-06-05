"""Tests for conftest.py bundled-config path validation."""

import pytest


def test_resolve_bundled_config_raises_on_missing(tmp_path, monkeypatch):
    """_resolve_bundled_config must raise FileNotFoundError when config is absent."""
    import tests.conftest as conftest_mod

    monkeypatch.setattr(conftest_mod, "REPO_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="config.example.yaml"):
        conftest_mod._resolve_bundled_config()


def test_resolve_bundled_config_returns_path_when_present(tmp_path, monkeypatch):
    """_resolve_bundled_config returns the config path when the file exists."""
    import tests.conftest as conftest_mod

    config_dir = tmp_path / "footprinter" / "bundled"
    config_dir.mkdir(parents=True)
    (config_dir / "config.example.yaml").write_text("# test")

    monkeypatch.setattr(conftest_mod, "REPO_ROOT", tmp_path)
    result = conftest_mod._resolve_bundled_config()
    assert result == config_dir / "config.example.yaml"
