"""
End-to-end subprocess tests for pip-installed entry points.

Verifies that entry points work when invoked as subprocesses (not just
imported) and that FOOTPRINTER_HOME is created on first use.

Run:
    ./venv/bin/python3 -m pytest tests/test_pip_install_e2e.py -v --tb=short
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    """Return env dict pointing all Footprinter paths at tmp_path.

    Ensures PYTHONPATH includes the project root so subprocesses can
    import footprinter even when cwd is changed.
    """
    env = os.environ.copy()
    env["FOOTPRINTER_HOME"] = str(tmp_path / "home")
    env["FOOTPRINTER_DB_PATH"] = str(tmp_path / "home" / "footprinter.db")
    env["FOOTPRINTER_CONFIG"] = str(tmp_path / "home" / "config.yaml")
    # Ensure footprinter is importable in subprocesses
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
    return env


def _run(args: list[str], env: dict[str, str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess with the given args and env."""
    return subprocess.run(
        [sys.executable] + args,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd,
    )


# ═══════════════════════════════════════════════════════════════════════
# TestEntryPointSubprocess — verify entry points run without traceback
# ═══════════════════════════════════════════════════════════════════════


class TestEntryPointSubprocess:
    """Verify entry points work as real subprocess invocations."""

    @pytest.mark.parametrize(
        "module,flag",
        [
            ("footprinter.cli.status", "--json"),
            ("footprinter.cli.search", "--help"),
            # orchestrator --help removed — main() no longer exists
        ],
    )
    def test_entry_point_no_traceback(self, tmp_path, module, flag):
        """Entry point should not produce a traceback on stderr."""
        env = _isolated_env(tmp_path)
        result = _run(["-m", module, flag], env=env)
        assert "Traceback" not in result.stderr, f"{module} {flag} produced a traceback:\n{result.stderr}"

    def test_search_help_shows_usage(self, tmp_path):
        """fp search --help should print usage text."""
        env = _isolated_env(tmp_path)
        result = _run(["-m", "footprinter.cli.search", "--help"], env=env)
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower() or "search" in result.stdout.lower()

    # test_orchestrator_help_lists_stages removed — main() no longer exists
    # in indexer.orchestrator. The `fp run` entry point is tested via cli/router.


# ═══════════════════════════════════════════════════════════════════════
# TestHomeDirectoryCreation — FOOTPRINTER_HOME created on first use
# ═══════════════════════════════════════════════════════════════════════


class TestHomeDirectoryCreation:
    """Verify FOOTPRINTER_HOME is created when entry points run."""

    def test_status_creates_footprinter_home(self, tmp_path):
        """fp status --json should create FOOTPRINTER_HOME dir."""
        home = tmp_path / "fp_home"
        env = _isolated_env(tmp_path)
        env["FOOTPRINTER_HOME"] = str(home)
        # Unset DB path so get_db_path() falls through to get_home()
        env.pop("FOOTPRINTER_DB_PATH", None)

        assert not home.exists()
        _run(["-m", "footprinter.cli.status", "--json"], env=env)
        assert home.exists(), "FOOTPRINTER_HOME was not created by fp status"

    def test_doctor_creates_footprinter_home(self, tmp_path):
        """fp doctor should create FOOTPRINTER_HOME dir."""
        home = tmp_path / "fp_home"
        env = _isolated_env(tmp_path)
        env["FOOTPRINTER_HOME"] = str(home)
        env.pop("FOOTPRINTER_DB_PATH", None)
        env.pop("FOOTPRINTER_CONFIG", None)

        assert not home.exists()
        _run(["-m", "footprinter.cli", "doctor"], env=env)
        assert home.exists(), "FOOTPRINTER_HOME was not created by fp doctor"
