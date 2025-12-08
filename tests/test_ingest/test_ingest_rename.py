"""Smoke tests for footprinter.ingest module (renamed from footprinter.ingest)."""


def test_import_orchestrator():
    from footprinter.ingest.orchestrator import DataPipelineOrchestrator

    assert DataPipelineOrchestrator is not None


def test_import_database():
    from footprinter.ingest.database import Database

    assert Database is not None


def test_import_adapters():
    from footprinter.ingest.adapters import LocalFilesAdapter, PipeAdapter

    assert PipeAdapter is not None
    assert LocalFilesAdapter is not None


def test_import_db_schema():
    from footprinter.ingest.db.schema import SchemaMixin

    assert SchemaMixin is not None
