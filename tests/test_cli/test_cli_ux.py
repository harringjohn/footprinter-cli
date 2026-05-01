"""Tests for CLI UX — version flag, Python version check, first-run detection.

Covers:
  1. Regressions — run help unaffected
  2. Version flag — fp --version prints version and exits
  3. Python version check — _check_python_version() guard
  4. First-run detection — setup hint when no config/db
"""

import pytest
from conftest import run_fp

# ===========================================================================
# 1. Regressions
# ===========================================================================


class TestRegressions:
    """Ensure existing patterns aren't broken."""

    def test_ingest_help_unaffected(self):
        _stdout, _stderr, code = run_fp("ingest", "--help")
        assert code == 0


# ===========================================================================
# 2. Version flag
# ===========================================================================


class TestVersionFlag:
    """``fp --version`` prints version and exits."""

    def test_version_flag_prints_version(self):
        from footprinter import __version__

        stdout, _stderr, code = run_fp("--version")
        assert code == 0
        assert __version__ in stdout


# ===========================================================================
# 3. Python version check
# ===========================================================================


class TestPythonVersionCheck:
    """_check_python_version() exits early on unsupported Python."""

    def test_old_python_exits_with_message(self, capsys):
        from unittest.mock import patch

        from footprinter.cli import _check_python_version

        with patch("footprinter.cli.sys.version_info", (3, 10, 0)):
            with pytest.raises(SystemExit) as exc_info:
                _check_python_version()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "3.11" in captured.err

    def test_supported_python_passes(self):
        from unittest.mock import patch

        from footprinter.cli import _check_python_version

        with patch("footprinter.cli.sys.version_info", (3, 11, 0)):
            _check_python_version()  # should not raise


# ===========================================================================
# 4. First-run detection
# ===========================================================================


class TestFirstRunDetection:
    """First-run detection shows a setup hint when no config/db exists."""

    @pytest.fixture(autouse=True)
    def _isolate_home(self, tmp_path, monkeypatch):
        """Point FOOTPRINTER_HOME at a clean tmp dir, unset path overrides."""
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
        monkeypatch.delenv("FOOTPRINTER_CONFIG", raising=False)
        monkeypatch.delenv("FOOTPRINTER_DB_PATH", raising=False)

    def test_no_config_no_db_shows_setup_hint(self):
        stdout, stderr, code = run_fp()
        output = stdout + stderr
        assert "first time" in output.lower()
        assert "fp setup" in output
        assert code == 0

    def test_config_exists_no_hint(self, tmp_path):
        (tmp_path / "config.yaml").write_text("directories: []")
        stdout, stderr, code = run_fp()
        output = stdout + stderr
        # The epilog mentions "fp setup" as a command — check for the
        # distinctive first-run phrasing, not just "fp setup".
        assert "first time" not in output.lower()

    def test_first_run_does_not_block_subcommands(self):
        from footprinter import __version__

        stdout, stderr, code = run_fp("--version")
        assert code == 0
        assert __version__ in stdout
        assert "first time" not in (stdout + stderr).lower()

    def test_is_first_run_unit(self, tmp_path):
        from footprinter.cli import _is_first_run

        # Neither config nor db exists → first run
        assert _is_first_run() is True

        # Config exists → not first run
        (tmp_path / "config.yaml").write_text("directories: []")
        assert _is_first_run() is False

        # Remove config, add db → still not first run
        (tmp_path / "config.yaml").unlink()
        (tmp_path / "footprinter.db").write_bytes(b"")
        assert _is_first_run() is False
