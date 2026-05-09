"""Structural assertions for the v1.0.2 merge-stripping (FPR-1683)."""

import sqlite3

import pytest


def test_chat_dedup_module_removed():
    """footprinter.ingest.chat_dedup was deleted with the merge feature."""
    with pytest.raises(ModuleNotFoundError):
        import footprinter.ingest.chat_dedup  # noqa: F401


def test_valid_statuses_excludes_merged_cli_common():
    from footprinter.cli._common import VALID_STATUSES

    assert "merged" not in VALID_STATUSES


def test_valid_statuses_excludes_merged_db_projects():
    from footprinter.db.projects import VALID_STATUSES

    assert "merged" not in VALID_STATUSES


@pytest.fixture
def fresh_db(tmp_path):
    """Empty database with current schema applied."""
    from footprinter.ingest.database import Database

    db = Database(str(tmp_path / "schema.db"))
    yield db.conn
    db.close()


def test_projects_check_constraint_rejects_merged(fresh_db):
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO projects (project_name, status) VALUES (?, ?)",
            ("merge-test", "merged"),
        )


def test_chats_check_constraint_rejects_merged(fresh_db):
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO chats (external_id, account, status) VALUES (?, ?, ?)",
            ("ext-merge-test", "claude", "merged"),
        )


def test_merged_into_id_column_preserved(fresh_db):
    """Column stays for data integrity even after the feature is gone."""
    cols = {row[1] for row in fresh_db.execute("PRAGMA table_info(chats)").fetchall()}
    assert "merged_into_id" in cols
