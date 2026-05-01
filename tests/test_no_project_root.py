"""
Acceptance test: no ad-hoc PROJECT_ROOT or get_db_path() in production code.

All path resolution should go through footprinter.paths.
This test scans footprinter/**/*.py (excluding paths.py and archive/) for
patterns that indicate ad-hoc path resolution.

Modelled after test_no_hardcoded_personal_data.py.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "footprinter"

# Directories to skip
SKIP_DIRS = {"archive", "__pycache__"}

# Files to skip (paths.py is the canonical source)
SKIP_FILES = {"paths.py"}


def _scan_py_files():
    """Yield all .py files under footprinter/, excluding archive/ and paths.py."""
    for py_file in SRC_DIR.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        if py_file.name in SKIP_FILES:
            continue
        yield py_file


def _scan_for_pattern(pattern: re.Pattern) -> list[tuple[Path, int, str]]:
    """Return list of (file, line_number, line_text) for all matches."""
    hits = []
    for py_file in _scan_py_files():
        for i, line in enumerate(py_file.read_text().splitlines(), start=1):
            if pattern.search(line):
                hits.append((py_file, i, line.strip()))
    return hits


class TestNoProjectRoot:
    """No module should define its own PROJECT_ROOT from __file__."""

    def test_no_project_root_assignment(self):
        """No `PROJECT_ROOT = ...Path(__file__)...` outside paths.py."""
        pattern = re.compile(r"^PROJECT_ROOT\s*=.*Path\(__file__\)")
        hits = _scan_for_pattern(pattern)
        assert hits == [], f"Found {len(hits)} PROJECT_ROOT assignment(s):\n" + "\n".join(
            f"  {f}:{n}: {t}" for f, n, t in hits
        )

    def test_no_underscore_project_root_assignment(self):
        """No `_PROJECT_ROOT = ...Path(__file__)...` outside paths.py."""
        pattern = re.compile(r"^_PROJECT_ROOT\s*=.*Path\(__file__\)")
        hits = _scan_for_pattern(pattern)
        assert hits == [], f"Found {len(hits)} _PROJECT_ROOT assignment(s):\n" + "\n".join(
            f"  {f}:{n}: {t}" for f, n, t in hits
        )

    def test_no_local_get_db_path(self):
        """No `def get_db_path(` outside paths.py."""
        pattern = re.compile(r"^def get_db_path\(")
        hits = _scan_for_pattern(pattern)
        assert hits == [], f"Found {len(hits)} local get_db_path() definition(s):\n" + "\n".join(
            f"  {f}:{n}: {t}" for f, n, t in hits
        )

    def test_no_local_get_config_path(self):
        """No `def get_config_path(` outside paths.py."""
        pattern = re.compile(r"^def get_config_path\(")
        hits = _scan_for_pattern(pattern)
        assert hits == [], f"Found {len(hits)} local get_config_path() definition(s):\n" + "\n".join(
            f"  {f}:{n}: {t}" for f, n, t in hits
        )

    def test_no_patterns_dir_file_hack(self):
        """No `PATTERNS_DIR = ...Path(__file__)...` outside paths.py."""
        pattern = re.compile(r"^PATTERNS_DIR\s*=.*Path\(__file__\)")
        hits = _scan_for_pattern(pattern)
        assert hits == [], f"Found {len(hits)} PATTERNS_DIR hack(s):\n" + "\n".join(
            f"  {f}:{n}: {t}" for f, n, t in hits
        )

    def test_no_default_db_path_hack(self):
        """No `DEFAULT_DB_PATH = ...Path(__file__)...` outside paths.py."""
        pattern = re.compile(r"^DEFAULT_DB_PATH\s*=.*Path\(__file__\)")
        hits = _scan_for_pattern(pattern)
        assert hits == [], f"Found {len(hits)} DEFAULT_DB_PATH hack(s):\n" + "\n".join(
            f"  {f}:{n}: {t}" for f, n, t in hits
        )
