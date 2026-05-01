"""Regression tests: the .test-active marker / is_test_mode contract is gone.

These tests pin the cleanup so the marker branch can never be reintroduced
without a deliberate revert.
"""

from pathlib import Path
from unittest.mock import patch


def test_get_home_ignores_dot_test_active(tmp_path, monkeypatch):
    """get_home() must not honor a .test-active marker file under HOME."""
    monkeypatch.delenv("FOOTPRINTER_HOME", raising=False)

    # Stage a fake home with a marker pointing at a sandbox we should NOT visit.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fp_dir = fake_home / ".footprinter"
    fp_dir.mkdir()
    sandbox = tmp_path / "should-not-be-used"
    sandbox.mkdir()
    (fp_dir / ".test-active").write_text(str(sandbox))

    with patch.object(Path, "home", return_value=fake_home):
        from footprinter.paths import get_home

        result = get_home()

    assert result == fp_dir, (
        f"get_home() honored the legacy .test-active marker; got {result}"
    )


def test_no_is_test_mode_export():
    """is_test_mode() must not exist as a public API on footprinter.paths."""
    from footprinter import paths

    assert not hasattr(paths, "is_test_mode"), (
        "is_test_mode is still exported — the marker contract was meant to be deleted"
    )


def test_no_test_marker_constants():
    """The _TEST_MARKER_NAME / _TEST_ENV_NAME private constants must be gone."""
    from footprinter import paths

    assert not hasattr(paths, "_TEST_MARKER_NAME")
    assert not hasattr(paths, "_TEST_ENV_NAME")


def test_get_home_env_var_takes_precedence(tmp_path, monkeypatch):
    """$FOOTPRINTER_HOME continues to override the default."""
    target = tmp_path / "custom"
    monkeypatch.setenv("FOOTPRINTER_HOME", str(target))

    from footprinter.paths import get_home

    assert get_home() == target
    assert target.is_dir()
