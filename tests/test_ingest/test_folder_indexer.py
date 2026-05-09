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


class TestExclusionPatterns:
    """FPR-1641: FolderIndexer must apply exclusions.always like FileScanner does."""

    def test_compile_always_exclusions_from_config(self, folder_db):
        """Indexer compiles exclusions.always patterns at construction."""
        config = {
            "exclusions": {
                "always": [".*/node_modules/.*", "^~/\\.claude/.*"],
            }
        }
        indexer = FolderIndexer(config, folder_db)

        assert len(indexer.always_exclusions) == 2

    def test_scan_prunes_excluded_subdirectory(self, folder_db, tmp_path):
        """A directory matching exclusions.always is neither walked nor emitted."""
        work = tmp_path / "Work"
        (work / "proj" / "src").mkdir(parents=True)
        (work / "proj" / "excluded_data" / "child").mkdir(parents=True)

        config = {"exclusions": {"always": [".*/excluded_data/.*"]}}
        indexer = FolderIndexer(config, folder_db)

        folders = indexer.scan_folders([str(work)])
        paths = [f["path"] for f in folders]

        assert not any("excluded_data" in p for p in paths)
        assert str(work / "proj" / "src") in paths

    def test_scan_prunes_excluded_descendant_under_configured_root(self, folder_db, tmp_path):
        """Excluded subtree beneath a configured root is pruned (the FPR-1641 case)."""
        claude = tmp_path / ".claude"
        (claude / "session-env" / "snap").mkdir(parents=True)
        (claude / "projects" / "keep").mkdir(parents=True)

        config = {"exclusions": {"always": [".*/\\.claude/session-env/.*"]}}
        indexer = FolderIndexer(config, folder_db)

        folders = indexer.scan_folders([str(claude)])
        paths = [f["path"] for f in folders]

        assert all("session-env" not in p for p in paths)
        assert str(claude / "projects" / "keep") in paths

    def test_scan_without_config_exclusions_still_works(self, folder_db, tmp_path):
        """Empty/missing exclusions config doesn't break scan; SKIP_DIRS still applies."""
        (tmp_path / "Work" / "proj").mkdir(parents=True)

        indexer = FolderIndexer({}, folder_db)

        assert indexer.always_exclusions == []
        assert indexer.sensitive_exclusions == []

        folders = indexer.scan_folders([str(tmp_path / "Work")])
        paths = [f["path"] for f in folders]
        assert str(tmp_path / "Work" / "proj") in paths

    def test_sensitive_pattern_excludes_folder(self, folder_db, tmp_path):
        """Folders matching exclusions.sensitive (e.g. ~/.ssh) are pruned."""
        (tmp_path / ".ssh" / "keys").mkdir(parents=True)
        (tmp_path / "Work" / "proj").mkdir(parents=True)

        config = {"exclusions": {"sensitive": [".*/\\.ssh/.*"]}}
        indexer = FolderIndexer(config, folder_db)

        folders = indexer.scan_folders([str(tmp_path)])
        paths = [f["path"] for f in folders]

        assert all(".ssh" not in p for p in paths)
        assert str(tmp_path / "Work" / "proj") in paths

    def test_sensitive_not_overridable_by_configured_root(self, folder_db, tmp_path):
        """Even when a sensitive-matching path is configured as a root, it stays excluded.

        Mirrors FileScanner intent at file_scanner.py:99-100: sensitive
        patterns must never be dropped by the opt-in mechanism.
        """
        (tmp_path / ".aws" / "creds").mkdir(parents=True)

        config = {"exclusions": {"sensitive": [".*/\\.aws/.*"]}}
        indexer = FolderIndexer(config, folder_db)

        folders = indexer.scan_folders([str(tmp_path / ".aws")])
        paths = [f["path"] for f in folders]

        assert paths == []

    def test_configured_root_opts_past_always_pattern(self, folder_db, tmp_path):
        """Explicitly configuring a root drops always-patterns that would zero it out.

        Mirrors FileScanner.scan_directory at file_scanner.py:182-196.
        """
        sample = tmp_path / "Downloads" / "sample-data"
        (sample / "src").mkdir(parents=True)

        config = {"exclusions": {"always": [".*/Downloads/.*"]}}
        indexer = FolderIndexer(config, folder_db)

        folders = indexer.scan_folders([str(sample)])
        paths = [f["path"] for f in folders]

        assert str(sample) in paths
        assert str(sample / "src") in paths
