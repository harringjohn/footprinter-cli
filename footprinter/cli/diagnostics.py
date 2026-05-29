"""Shared diagnostic functions for ``fp doctor`` and related health checks.

Extracted from setup.py to decouple diagnostics from the interactive wizard.
"""

import platform
import subprocess
from pathlib import Path


KNOWN_BROWSERS = ["safari", "chrome"]


def is_importable(module_name: str) -> bool:
    """Return True if *module_name* can be imported."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def validate_config(config: dict) -> tuple[list[str], list[str]]:
    """Validate a config dict and return errors and warnings.

    Args:
        config: Parsed YAML config dict.

    Returns:
        Tuple of (errors, warnings). Empty errors means valid.
    """
    errors = []

    if config is None:
        errors.append("Config is empty or invalid YAML")
        return errors, []

    dirs = config.get("directories")
    missing_dirs: list[str] = []
    if not dirs:
        errors.append("'directories' is missing or empty")
    elif not isinstance(dirs, list):
        errors.append("'directories' must be a list")
    else:
        for d in dirs:
            if not Path(d).expanduser().is_dir():
                missing_dirs.append(d)

    browsers = config.get("browsers")
    if browsers is None:
        errors.append("'browsers' key is missing")
    elif not isinstance(browsers, list):
        errors.append("'browsers' must be a list")
    else:
        for b in browsers:
            if b not in KNOWN_BROWSERS:
                errors.append(f"Unknown browser: {b}")

    # Missing dirs are warnings, not errors — the bundled example config lists
    # macOS-flavored defaults that a fresh Linux install won't have.
    warnings = []
    if missing_dirs:
        warnings.append(
            "Directories not found (will be skipped during indexing): "
            + ", ".join(missing_dirs)
        )
    if "exclusions" not in config:
        warnings.append("'exclusions' section missing — default exclusions will be used")
    if "indexing" not in config:
        warnings.append("'indexing' section missing — default settings will be used")

    return errors, warnings


def check_architecture() -> str | None:
    """Check for architecture mismatches. Returns warning string or None."""
    machine = platform.machine()
    # hw.optional.arm64 returns 1 on Apple Silicon even under Rosetta,
    # unlike hw.machine which reports x86_64 under Rosetta.
    if machine == "x86_64":
        try:
            hw = subprocess.run(
                ["sysctl", "-n", "hw.optional.arm64"],
                capture_output=True,
                text=True,
            )
            if hw.stdout.strip() == "1":
                return (
                    "Python is running as x86_64 on arm64 hardware (Rosetta). "
                    "Native dependencies may have compatibility issues. "
                    "Consider recreating venv with native arm64 Python."
                )
        except Exception:
            pass
    return None


def check_core_deps() -> list[tuple[str, bool]]:
    """Check core dependencies. Returns ``(name, available)`` pairs.

    Core deps are hard requirements — if any are missing the install is broken.
    """
    return [
        ("PyYAML", is_importable("yaml")),
        ("Rich", is_importable("rich")),
    ]


def check_optional_features(
    config: dict,
) -> list[tuple[str, bool, bool | None, str]]:
    """Check optional features against install state *and* config.

    Returns ``(name, installed, enabled, hint)`` for each feature.
    ``enabled`` is ``None`` when not applicable.
    """
    features: list[tuple[str, bool, bool | None, str]] = []

    sem_installed = is_importable("chromadb") and is_importable("onnxruntime")
    sem_cfg = config.get("semantic", {})
    sem_enabled = sem_cfg.get("file_vectorization", False) or sem_cfg.get(
        "chat_vectorization", False
    )
    features.append((
        "Semantic Search",
        sem_installed,
        sem_enabled,
        "pip install footprinter-cli[semantic]",
    ))

    parse_installed = all(
        is_importable(m) for m in ("pypdf", "docx", "openpyxl", "pptx")
    )
    features.append((
        "Document Parsing",
        parse_installed,
        parse_installed or None,
        "pip install footprinter-cli[parse]",
    ))

    from footprinter.connectors import discover_connectors

    for spec in discover_connectors().values():
        for feat_name, probe, cfg_section, hint in spec.features:
            installed = is_importable(probe)
            enabled = config.get(cfg_section, {}).get("enabled", False)
            features.append((feat_name, installed, enabled, hint))

    return features
