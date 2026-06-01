"""Tests for removal of obsolete CLI commands.

Validates:
  1. Shared symbols relocated to their new homes
  2. Old modules no longer importable
  3. Old commands no longer registered
"""

import importlib
import re
from pathlib import Path

import pytest
from conftest import run_fp


# ---------------------------------------------------------------------------
# 1. Relocated symbols — importable from new homes
# ---------------------------------------------------------------------------


class TestRelocatedToCommon:
    def test_valid_statuses_by_entity_importable(self):
        from footprinter.cli._common import VALID_STATUSES_BY_ENTITY

        assert isinstance(VALID_STATUSES_BY_ENTITY, dict)
        assert "client" in VALID_STATUSES_BY_ENTITY
        assert "project" in VALID_STATUSES_BY_ENTITY

    def test_valid_statuses_by_entity_values_are_frozensets(self):
        from footprinter.cli._common import VALID_STATUSES_BY_ENTITY

        for val in VALID_STATUSES_BY_ENTITY.values():
            assert isinstance(val, frozenset)


class TestRelocatedToAdd:
    def test_single_args_importable(self):
        from footprinter.cli.add import SINGLE_ARGS

        assert "client" in SINGLE_ARGS
        assert "project" in SINGLE_ARGS

    def test_csv_columns_importable(self):
        from footprinter.cli.add import CSV_COLUMNS

        assert "client" in CSV_COLUMNS
        assert "project" in CSV_COLUMNS

    def test_validate_and_read_csv_importable(self):
        from footprinter.cli.add import _validate_and_read_csv

        assert callable(_validate_and_read_csv)

    def test_check_exists_importable(self):
        from footprinter.cli.add import _check_exists

        assert callable(_check_exists)

    def test_process_csv_rows_importable(self):
        from footprinter.cli.add import _process_csv_rows

        assert callable(_process_csv_rows)


class TestRelocatedToUpdate:
    def test_data_source_spec_importable(self):
        from footprinter.cli.update import DataSourceSpec

        assert callable(DataSourceSpec)

    def test_data_source_specs_importable(self):
        from footprinter.cli.update import DATA_SOURCE_SPECS

        assert isinstance(DATA_SOURCE_SPECS, dict)
        assert "files" in DATA_SOURCE_SPECS


# ---------------------------------------------------------------------------
# 2. Old modules deleted
# ---------------------------------------------------------------------------


class TestOldModulesRemoved:
    def test_upsert_module_not_importable(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("footprinter.cli.upsert")

    def test_data_module_not_importable(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("footprinter.cli.data")


# ---------------------------------------------------------------------------
# 3. Old commands not registered
# ---------------------------------------------------------------------------


class TestOldCommandsGone:
    def test_upsert_not_registered(self):
        _stdout, _stderr, code = run_fp("upsert", "--help")
        assert code != 0

    def test_data_not_registered(self):
        _stdout, _stderr, code = run_fp("data", "--help")
        assert code != 0

    def test_ingest_import_not_in_help(self):
        stdout, stderr, code = run_fp("ingest", "--help")
        assert code == 0
        assert "import" not in (stdout + stderr).lower()


# ---------------------------------------------------------------------------
# 4. No stale references to removed commands in source or docs
# ---------------------------------------------------------------------------

_STALE_PATTERN = r"fp (upsert|data |vectorize|ingest status|ingest import)"
_ROOT = Path(__file__).resolve().parents[2]


class TestNoStaleReferences:
    """Ensure removed CLI commands are not referenced in runtime code or docs."""

    def test_no_stale_runtime_references(self):
        """No .py file under footprinter/ (excluding tests) mentions removed commands."""
        hits = []
        for py_file in sorted(_ROOT.joinpath("footprinter").rglob("*.py")):
            if "test" in py_file.parts:
                continue
            text = py_file.read_text()
            for m in re.finditer(_STALE_PATTERN, text):
                hits.append(f"{py_file.relative_to(_ROOT)}:{m.group()}")
        assert hits == [], f"Stale command references in runtime code:\n" + "\n".join(hits)

    def test_no_stale_doc_references(self):
        """No stale command references in README or reference/ docs."""
        hits = []
        doc_paths = [_ROOT / "README.md"] + sorted(
            _ROOT.joinpath("reference").rglob("*.md")
        )
        for doc in doc_paths:
            if not doc.exists():
                continue
            text = doc.read_text()
            for m in re.finditer(_STALE_PATTERN, text):
                hits.append(f"{doc.relative_to(_ROOT)}:{m.group()}")
        assert hits == [], f"Stale command references in docs:\n" + "\n".join(hits)

    def test_setup_wizard_uses_current_commands(self):
        """The setup wizard module has no references to fp upsert or fp ingest import."""
        setup_src = _ROOT.joinpath("footprinter", "cli", "setup.py").read_text()
        assert "fp upsert" not in setup_src, "setup.py still references 'fp upsert'"
        assert "fp ingest import" not in setup_src, "setup.py still references 'fp ingest import'"
