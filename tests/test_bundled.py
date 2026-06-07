"""Tests for bundled YAML files shipped inside the footprinter package.

`footprinter/bundled/` is the single source of truth for bundled data the
shipped CLI consumes: `config.example.yaml`. No parallel copies under
`config/` are permitted. Pattern YAMLs were moved to fpr-dev (they
are not consumed by fpr-cli).
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_DIR = REPO_ROOT / "footprinter" / "bundled"

BUNDLED_YAMLS = [
    "config.example.yaml",
]


class TestBundledFilesExist:
    """Assert every expected file is physically present in footprinter/bundled/."""

    @pytest.mark.parametrize("relpath", BUNDLED_YAMLS)
    def test_yaml_file_exists(self, relpath):
        assert (BUNDLED_DIR / relpath).is_file(), f"Missing: bundled/{relpath}"

    def test_bundled_init_exists(self):
        assert (BUNDLED_DIR / "__init__.py").is_file()


class TestBundledFilesLoadable:
    """Assert files are valid YAML and reachable via importlib.resources."""

    @pytest.mark.parametrize("relpath", BUNDLED_YAMLS)
    def test_yaml_parses_to_dict(self, relpath):
        path = BUNDLED_DIR / relpath
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict), f"{relpath} did not parse to a dict"

    def test_get_bundled_path_resolves(self):
        from footprinter.paths import get_bundled_path

        path = get_bundled_path("config.example.yaml")
        assert Path(path).is_file()

    def test_get_bundled_patterns_dir_removed(self):
        import footprinter.paths as paths_mod

        assert not hasattr(paths_mod, "get_bundled_patterns_dir"), (
            "get_bundled_patterns_dir should not exist — patterns belong in fpr-dev"
        )


class TestPyprojectPackageData:
    """Assert pyproject.toml declares package-data for bundled files."""

    @pytest.fixture()
    def pyproject(self):
        import tomllib

        with open(REPO_ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)

    def test_package_data_section_exists(self, pyproject):
        assert "tool" in pyproject
        assert "setuptools" in pyproject["tool"]
        assert "package-data" in pyproject["tool"]["setuptools"]

    def test_package_data_covers_bundled(self, pyproject):
        pd = pyproject["tool"]["setuptools"]["package-data"]
        globs = pd.get("footprinter.bundled", [])
        assert "*.yaml" in globs, "Missing *.yaml in package-data"

    def test_package_data_excludes_patterns(self, pyproject):
        pd = pyproject["tool"]["setuptools"]["package-data"]
        globs = pd.get("footprinter.bundled", [])
        assert "patterns/*.yaml" not in globs, (
            "patterns/*.yaml should not be in package-data — patterns belong in fpr-dev"
        )


class TestSingleSourceOfTruth:
    """Guard: patterns do not reappear in config/ or bundled/."""

    def test_config_patterns_dir_absent(self):
        assert not (REPO_ROOT / "config" / "patterns").exists(), (
            "config/patterns/ must not exist — patterns belong in fpr-dev, not fpr-cli"
        )

    def test_bundled_patterns_dir_absent(self):
        assert not (BUNDLED_DIR / "patterns").exists(), (
            "bundled/patterns/ must not exist — patterns belong in fpr-dev"
        )

    def test_config_example_yaml_absent(self):
        assert not (REPO_ROOT / "config" / "config.example.yaml").exists(), (
            "config/config.example.yaml must not exist — canonical copy is at footprinter/bundled/config.example.yaml"
        )
