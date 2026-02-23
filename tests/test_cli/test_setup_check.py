"""Tests for fp setup --check dependency/feature reporting."""

from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. Core deps should NOT appear in the optional features list
# ---------------------------------------------------------------------------
def test_core_deps_not_in_optional_table():
    """Core packages (yaml, rich) must not appear in optional features."""
    from footprinter.cli.setup import check_optional_features

    config = {"semantic": {"file_vectorization": False}}
    features = check_optional_features(config)
    feature_names = [f[0] for f in features]

    for core_name in ("Core (YAML)", "CLI"):
        assert core_name not in feature_names, f"{core_name} should not be in optional features"


# ---------------------------------------------------------------------------
# 2. Semantic Search installed but not enabled
# ---------------------------------------------------------------------------
def test_semantic_installed_not_enabled():
    """Semantic Search shows 'installed, not enabled' when packages present but config off."""
    from footprinter.cli.setup import check_optional_features

    config = {
        "semantic": {"file_vectorization": False, "chat_vectorization": False},
    }

    with patch(
        "footprinter.cli.setup._is_importable", side_effect=lambda m: m in ("chromadb", "onnxruntime", "google.auth")
    ):
        features = check_optional_features(config)

    semantic = next(f for f in features if f[0] == "Semantic Search")
    assert semantic[1] is True, "installed should be True"
    assert semantic[2] is False, "enabled should be False"


# ---------------------------------------------------------------------------
# 3. Semantic Search installed and enabled
# ---------------------------------------------------------------------------
def test_semantic_installed_enabled():
    """Semantic Search shows enabled when packages present and config on."""
    from footprinter.cli.setup import check_optional_features

    config = {
        "semantic": {"file_vectorization": True},
    }

    with patch("footprinter.cli.setup._is_importable", return_value=True):
        features = check_optional_features(config)

    semantic = next(f for f in features if f[0] == "Semantic Search")
    assert semantic[1] is True, "installed should be True"
    assert semantic[2] is True, "enabled should be True"


# ---------------------------------------------------------------------------
# 4. Semantic Search not installed
# ---------------------------------------------------------------------------
def test_semantic_not_installed():
    """Semantic Search shows not installed with inline hint when packages missing."""
    from footprinter.cli.setup import check_optional_features

    config = {"semantic": {"file_vectorization": True}}

    def importable(mod):
        if mod == "chromadb":
            return False
        return True

    with patch("footprinter.cli.setup._is_importable", side_effect=importable):
        features = check_optional_features(config)

    semantic = next(f for f in features if f[0] == "Semantic Search")
    assert semantic[1] is False, "installed should be False"
    assert "footprinter-cli[semantic]" in semantic[3], "hint should mention extras group"


# ---------------------------------------------------------------------------
# 5. No hardcoded Google rows — connector features are dynamic
# ---------------------------------------------------------------------------
def test_no_google_rows_without_connector():
    """check_optional_features() should NOT return Google Drive or Gmail rows
    when no connector is discovered — even if google.auth is importable."""
    from footprinter.cli.setup import check_optional_features

    config = {
        "google_drive": {"enabled": True},
        "gmail": {"enabled": False},
        "semantic": {"file_vectorization": False},
    }

    with patch("footprinter.cli.setup._is_importable", return_value=True):
        features = check_optional_features(config)

    feature_names = [f[0] for f in features]
    assert "Google Drive" not in feature_names, "Google Drive should not appear without connector"
    assert "Gmail" not in feature_names, "Gmail should not appear without connector"
    # Core features should still be present
    assert "Semantic Search" in feature_names


def test_connector_features_appear_when_discovered():
    """When a connector is discovered, its features appear in the table."""
    from footprinter.cli.setup import check_optional_features
    from footprinter.connectors import ConnectorSpec

    spec = ConnectorSpec(
        name="google",
        extra="google",
        description="Google Drive and Gmail",
        pipes=("drive_folders", "drive_files", "gmail"),
        probe_module="google.auth",
        config_sections=("google_drive", "gmail"),
        setup_hook="footprinter.cli.google_setup.run_google_setup",
        remove_packages=(),
        features=(
            ("Google Drive", "google.auth", "google_drive", "pip install footprinter-google"),
            ("Gmail", "google.auth", "gmail", "pip install footprinter-google"),
        ),
    )

    config = {
        "google_drive": {"enabled": True},
        "gmail": {"enabled": False},
        "semantic": {"file_vectorization": False},
    }

    with (
        patch("footprinter.cli.setup._is_importable", return_value=True),
        patch("footprinter.connectors.discover_connectors", return_value={"google": spec}),
    ):
        features = check_optional_features(config)

    feature_names = [f[0] for f in features]
    assert "Google Drive" in feature_names, "Connector feature should appear"
    assert "Gmail" in feature_names, "Connector feature should appear"


# ---------------------------------------------------------------------------
# 6. --check output excludes git hooks status
# ---------------------------------------------------------------------------
def test_check_output_excludes_git_hooks(tmp_path, monkeypatch):
    """Git hooks status must not appear in --check output, even when hooks are available."""
    from io import StringIO

    from rich.console import Console

    from footprinter.cli import setup as setup_mod

    minimal_config = {
        "sources": {"local_files": {"roots": [str(tmp_path)]}},
        "semantic": {"file_vectorization": False},
    }
    monkeypatch.setattr(setup_mod, "get_config", lambda: minimal_config)
    monkeypatch.setattr(setup_mod, "validate_config", lambda c: ([], []))
    monkeypatch.setattr(setup_mod, "check_architecture", lambda: None)

    buf = StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)
    monkeypatch.setattr(setup_mod, "console", test_console)

    setup_mod.check_existing_config()
    output = buf.getvalue()

    assert "Git hooks" not in output, f"'Git hooks' should not appear in --check output. Got:\n{output}"


# ---------------------------------------------------------------------------
# 7. Table rendering has no "Install Hint" column
# ---------------------------------------------------------------------------
def test_no_install_hint_column(tmp_path, monkeypatch):
    """The rendered table must not have an 'Install Hint' column header."""
    from io import StringIO

    from rich.console import Console

    from footprinter.cli import setup as setup_mod

    # Provide a minimal valid config so check_existing_config() proceeds
    minimal_config = {
        "sources": {"local_files": {"roots": [str(tmp_path)]}},
        "semantic": {"file_vectorization": False},
    }
    monkeypatch.setattr(setup_mod, "get_config", lambda: minimal_config)
    monkeypatch.setattr(setup_mod, "validate_config", lambda c: ([], []))
    monkeypatch.setattr(setup_mod, "check_architecture", lambda: None)
    monkeypatch.setattr(setup_mod, "_hooks_available", lambda: False)

    buf = StringIO()
    test_console = Console(file=buf, force_terminal=False, width=120)
    monkeypatch.setattr(setup_mod, "console", test_console)

    setup_mod.check_existing_config()
    output = buf.getvalue()

    assert "Install Hint" not in output, f"'Install Hint' column should be removed. Got:\n{output}"
