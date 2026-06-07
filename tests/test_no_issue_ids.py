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
    PROJECT_ROOT / "scripts",
]

ISSUE_ID_PATTERN = re.compile(r"FPR-\d+")

EXEMPT_FILES = {"test_no_issue_ids.py"}

SKIP_DIRS = {"__pycache__"}


def _scan_files(
    directories: list[Path],
    pattern: re.Pattern,
    exempt_filenames: set[str],
    file_globs: tuple[str, ...] = ("*.py",),
) -> list[str]:
    """Scan files for a forbidden pattern. Returns violation strings."""
    violations: list[str] = []
    for directory in directories:
        for glob in file_globs:
            for path in sorted(directory.rglob(glob)):
                if path.name in exempt_filenames:
                    continue
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                for i, line in enumerate(path.read_text().splitlines(), 1):
                    if pattern.search(line):
                        rel = path.relative_to(PROJECT_ROOT)
                        violations.append(f"{rel}:{i}: {line.strip()}")
    return violations


class TestNoIssueIds:
    """No Linear issue IDs (FPR-\\d+) should appear in source or tests."""

    def test_no_issue_ids_in_codebase(self):
        violations = _scan_files(
            SCAN_DIRS,
            ISSUE_ID_PATTERN,
            EXEMPT_FILES,
            file_globs=("*.py", "*.md", "*.sh"),
        )
        assert violations == [], (
            f"Found {len(violations)} issue-ID reference(s) — replace with "
            f"plain-English descriptions:\n" + "\n".join(violations)
        )
