"""Boundary enforcement tests for the connector extraction.

Ensures no Google-specific imports remain in ingest/ and that
ingest/ never imports from connectors/ (dependency direction).
"""

import re
from pathlib import Path

INGEST_DIR = Path(__file__).resolve().parent.parent.parent / "footprinter" / "ingest"

# Patterns that must not appear in any ingest/ .py file
GOOGLE_IMPORT_PATTERNS = [
    re.compile(r"from footprinter\.ingest\.google"),
    re.compile(r"from footprinter\.ingest\.gmail"),
    re.compile(r"from footprinter\.ingest\.adapters\.drive_"),
    re.compile(r"from footprinter\.ingest\.adapters\.gmail"),
]

CONNECTOR_IMPORT_PATTERN = re.compile(r"from footprinter\.connectors")

# orchestrator.py is the composition root — calls discover_connectors()
# and merges core + connector registries.
CONNECTOR_IMPORT_EXEMPTIONS = {
    "orchestrator.py",
}


def _scan_py_files(
    directory: Path,
    patterns: list[re.Pattern],
    exempt_filenames: set[str] | None = None,
) -> list[str]:
    """Scan .py files for forbidden import patterns. Returns violations."""
    exempt = exempt_filenames or set()
    violations = []
    for py_file in sorted(directory.rglob("*.py")):
        if py_file.name in exempt:
            continue
        for i, line in enumerate(py_file.read_text().splitlines(), 1):
            for pat in patterns:
                if pat.search(line):
                    rel = py_file.relative_to(directory.parent.parent)
                    violations.append(f"{rel}:{i}: {line.strip()}")
    return violations


class TestNoGoogleInIngest:
    """No Google-specific imports should remain in ingest/."""

    def test_no_google_imports_in_ingest(self):
        violations = _scan_py_files(INGEST_DIR, GOOGLE_IMPORT_PATTERNS)
        assert violations == [], f"Found {len(violations)} Google import(s) in ingest/:\n" + "\n".join(violations)


class TestDependencyDirection:
    """ingest/ must never import from connectors/."""

    def test_ingest_does_not_import_connectors(self):
        violations = _scan_py_files(
            INGEST_DIR,
            [CONNECTOR_IMPORT_PATTERN],
            exempt_filenames=CONNECTOR_IMPORT_EXEMPTIONS,
        )
        assert violations == [], f"Found {len(violations)} connectors import(s) in ingest/:\n" + "\n".join(violations)


class TestCoreOnlyExports:
    """ingest/adapters/__init__.py should export only core adapters."""

    CORE_EXPORTS = {
        "BrowserAdapter",
        "ChatAdapter",
        "PipeAdapter",
        "PipeContext",
        "ErrorType",
        "LocalFilesAdapter",
        "LocalFoldersAdapter",
        "PipeResult",
        "PipeStatus",
    }

    GOOGLE_EXPORTS = {
        "DriveFilesAdapter",
        "DriveFoldersAdapter",
        "GmailAdapter",
    }

    def test_all_contains_only_core(self):
        from footprinter.ingest import adapters

        exported = set(adapters.__all__)
        assert exported == self.CORE_EXPORTS, f"Expected only core exports, got: {exported}"

    def test_no_google_adapters_exported(self):
        from footprinter.ingest import adapters

        exported = set(adapters.__all__)
        leaked = exported & self.GOOGLE_EXPORTS
        assert leaked == set(), f"Google adapters leaked into ingest exports: {leaked}"
