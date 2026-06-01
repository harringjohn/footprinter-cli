"""Guard test: no Linear issue IDs in source or test code.

Issue IDs (FPR-\\d+) couple the code to the ticket tracker and become
meaningless once the issue is closed.  Describe *what* the code does
in plain English instead.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = [
    PROJECT_ROOT / "footprinter",
    PROJECT_ROOT / "tests",
]

ISSUE_ID_PATTERN = re.compile(r"FPR-\d+")

EXEMPT_FILES = {"test_no_issue_ids.py"}

SKIP_DIRS = {"__pycache__"}


def _scan_py_files(
    directories: list[Path],
    pattern: re.Pattern,
    exempt_filenames: set[str],
) -> list[str]:
    """Scan .py files for a forbidden pattern. Returns violation strings."""
    violations: list[str] = []
    for directory in directories:
        for py_file in sorted(directory.rglob("*.py")):
            if py_file.name in exempt_filenames:
                continue
            if any(part in SKIP_DIRS for part in py_file.parts):
                continue
            for i, line in enumerate(py_file.read_text().splitlines(), 1):
                if pattern.search(line):
                    rel = py_file.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel}:{i}: {line.strip()}")
    return violations


class TestNoIssueIds:
    """No Linear issue IDs (FPR-\\d+) should appear in source or tests."""

    def test_no_issue_ids_in_codebase(self):
        violations = _scan_py_files(SCAN_DIRS, ISSUE_ID_PATTERN, EXEMPT_FILES)
        assert violations == [], (
            f"Found {len(violations)} issue-ID reference(s) — replace with "
            f"plain-English descriptions:\n" + "\n".join(violations)
        )
