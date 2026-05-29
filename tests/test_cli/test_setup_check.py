"""Tests for diagnostic feature reporting (formerly fp setup --check)."""

from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. Core deps should NOT appear in the optional features list
# ---------------------------------------------------------------------------
def test_core_deps_not_in_optional_table():
    """Core packages (yaml, rich) must not appear in optional features."""
    from footprinter.cli.diagnostics import check_optional_features

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
    from footprinter.cli.diagnostics import check_optional_features

    config = {
        "semantic": {"file_vectorization": False, "chat_vectorization": False},
    }

    with patch(
        "footprinter.cli.diagnostics.is_importable", side_effect=lambda m: m in ("chromadb", "onnxruntime", "google.auth")
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
    from footprinter.cli.diagnostics import check_optional_features

    config = {
        "semantic": {"file_vectorization": True},
    }

    with patch("footprinter.cli.diagnostics.is_importable", return_value=True):
        features = check_optional_features(config)

    semantic = next(f for f in features if f[0] == "Semantic Search")
    assert semantic[1] is True, "installed should be True"
    assert semantic[2] is True, "enabled should be True"


# ---------------------------------------------------------------------------
# 4. Semantic Search not installed
# ---------------------------------------------------------------------------
def test_semantic_not_installed():
    """Semantic Search shows not installed with inline hint when packages missing."""
    from footprinter.cli.diagnostics import check_optional_features

    config = {"semantic": {"file_vectorization": True}}

    def importable(mod):
        if mod == "chromadb":
            return False
        return True

    with patch("footprinter.cli.diagnostics.is_importable", side_effect=importable):
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
    from footprinter.cli.diagnostics import check_optional_features

    config = {
        "google_drive": {"enabled": True},
        "gmail": {"enabled": False},
        "semantic": {"file_vectorization": False},
    }

    with patch("footprinter.cli.diagnostics.is_importable", return_value=True):
        features = check_optional_features(config)

    feature_names = [f[0] for f in features]
    assert "Google Drive" not in feature_names, "Google Drive should not appear without connector"
    assert "Gmail" not in feature_names, "Gmail should not appear without connector"
    # Core features should still be present
    assert "Semantic Search" in feature_names


def test_connector_features_appear_when_discovered():
    """When a connector is discovered, its features appear in the table."""
    from footprinter.cli.diagnostics import check_optional_features
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
        patch("footprinter.cli.diagnostics.is_importable", return_value=True),
        patch("footprinter.connectors.discover_connectors", return_value={"google": spec}),
    ):
        features = check_optional_features(config)

    feature_names = [f[0] for f in features]
    assert "Google Drive" in feature_names, "Connector feature should appear"
    assert "Gmail" in feature_names, "Connector feature should appear"


# ---------------------------------------------------------------------------
# 6. Document Parsing — installed
# ---------------------------------------------------------------------------
def test_doc_parsing_installed():
    """Document Parsing shows 'enabled' when all four parse deps are present."""
    from footprinter.cli.diagnostics import check_optional_features

    parse_mods = {"pypdf", "docx", "openpyxl", "pptx"}

    all_mods = parse_mods | {"chromadb", "onnxruntime"}
    with patch("footprinter.cli.diagnostics.is_importable", side_effect=lambda m: m in all_mods):
        features = check_optional_features({})

    doc = next(f for f in features if f[0] == "Document Parsing")
    assert doc[1] is True, "installed should be True"
    assert doc[2] is True, "enabled should be True (always active when installed)"
    assert "footprinter-cli[parse]" in doc[3], "hint should mention extras group"


# ---------------------------------------------------------------------------
# 7. Document Parsing — not installed
# ---------------------------------------------------------------------------
def test_doc_parsing_not_installed():
    """Document Parsing shows 'not installed' when parse deps are missing."""
    from footprinter.cli.diagnostics import check_optional_features

    def importable(mod):
        return mod in ("chromadb", "onnxruntime")

    with patch("footprinter.cli.diagnostics.is_importable", side_effect=importable):
        features = check_optional_features({"semantic": {"file_vectorization": False}})

    doc = next(f for f in features if f[0] == "Document Parsing")
    assert doc[1] is False, "installed should be False"
    assert "footprinter-cli[parse]" in doc[3], "hint should mention extras group"


# ---------------------------------------------------------------------------
# 8. Document Parsing — partial install still counts as not installed
# ---------------------------------------------------------------------------
def test_doc_parsing_partial_install():
    """All four parse modules must be present; partial = not installed."""
    from footprinter.cli.diagnostics import check_optional_features

    partial = {"pypdf", "docx"}

    with patch("footprinter.cli.diagnostics.is_importable", side_effect=lambda m: m in partial):
        features = check_optional_features({})

    doc = next(f for f in features if f[0] == "Document Parsing")
    assert doc[1] is False, "installed should be False when only some deps present"


# ---------------------------------------------------------------------------
# 9. Adding Document Parsing doesn't change Semantic Search
# ---------------------------------------------------------------------------
def test_semantic_unchanged_with_parsing():
    """Semantic Search row is independent of Document Parsing state."""
    from footprinter.cli.diagnostics import check_optional_features

    config = {"semantic": {"file_vectorization": True}}

    with patch("footprinter.cli.diagnostics.is_importable", side_effect=lambda m: m in ("chromadb", "onnxruntime")):
        features = check_optional_features(config)

    semantic = next(f for f in features if f[0] == "Semantic Search")
    assert semantic[1] is True, "Semantic installed should be True"
    assert semantic[2] is True, "Semantic enabled should be True"

    doc = next(f for f in features if f[0] == "Document Parsing")
    assert doc[1] is False, "Parse deps not mocked as available"
