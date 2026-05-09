"""Tests for FolderIndexer.save_folders() change-detection fast-path (FPR-1623)."""

import time

import pytest

from footprinter.ingest.database import Database
from footprinter.ingest.folder_indexer import FolderIndexer


@pytest.fixture
def folder_db(tmp_path):
    """Database with full schema for folder indexer tests."""
    db_path = tmp_path / "test_folders.db"
    db = Database(str(db_path))
    yield db
    db.close()


def _make_folder(path, name=None, parent_path=None, scanned_at="2026-01-01T00:00:00Z"):
    return {
        "path": path,
        "relative_path": path.replace("/Users/test", ""),
        "name": name if name is not None else path.rsplit("/", 1)[-1],
        "parent_path": parent_path if parent_path is not None else path.rsplit("/", 1)[0],
        "scanned_at": scanned_at,
    }


def _row(db, path):
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT relative_path, name, parent_path, updated_at FROM folders WHERE path = ?",
        (path,),
    )
    return cursor.fetchone()


class TestSaveFoldersChangeDetection:
    def test_new_folder_inserts(self, folder_db):
        """First save of a folder reports (1 inserted, 0 updated, 0 unchanged)."""
        indexer = FolderIndexer({}, folder_db)

        result = indexer.save_folders([_make_folder("/Users/test/Work/proj")])

        assert result == (1, 0, 0)

    def test_unchanged_folder_skips_update(self, folder_db):
        """Re-saving identical folder data must not issue an UPDATE."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/proj")
        indexer.save_folders([folder])

        before = _row(folder_db, folder["path"])
        # CURRENT_TIMESTAMP is second-resolution; ensure any UPDATE would be visible.
        time.sleep(1.1)

        result = indexer.save_folders([folder])

        after = _row(folder_db, folder["path"])
        assert result == (0, 0, 1)
        assert before["updated_at"] == after["updated_at"]

    def test_changed_folder_issues_update(self, folder_db):
        """A real field change still increments the updated counter and stamps updated_at."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/proj", name="proj")
        indexer.save_folders([folder])
        before = _row(folder_db, folder["path"])
        time.sleep(1.1)

        renamed = dict(folder, name="renamed")
        result = indexer.save_folders([renamed])

        after = _row(folder_db, folder["path"])
        assert result == (0, 1, 0)
        assert after["name"] == "renamed"
        assert before["updated_at"] != after["updated_at"]

    def test_mixed_batch_counts(self, folder_db):
        """A batch of [unchanged, changed, new] reports (1, 1, 1)."""
        indexer = FolderIndexer({}, folder_db)
        unchanged = _make_folder("/Users/test/Work/a", name="a")
        to_change = _make_folder("/Users/test/Work/b", name="b")
        indexer.save_folders([unchanged, to_change])

        changed = dict(to_change, name="b-renamed")
        new = _make_folder("/Users/test/Work/c", name="c")

        result = indexer.save_folders([unchanged, changed, new])

        assert result == (1, 1, 1)
