"""Tests for footprinter package init — version and docstring metadata."""

import tomllib


def test_version_exists():
    """footprinter.__version__ should exist and be a string."""
    import footprinter

    assert hasattr(footprinter, "__version__")
    assert isinstance(footprinter.__version__, str)


def test_version_matches_pyproject():
    """__version__ should match the version declared in pyproject.toml."""
    from pathlib import Path

    import footprinter

    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        pyproject = tomllib.load(f)

    expected = pyproject["project"]["version"]
    assert footprinter.__version__ == expected


def test_version_is_nonempty():
    """__version__ should not be an empty string."""
    import footprinter

    assert footprinter.__version__ != ""


def test_docstring_exists():
    """The footprinter package should have a module docstring."""
    import footprinter

    assert footprinter.__doc__
