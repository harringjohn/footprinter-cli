"""Tests for FolderIndexer.save_folders() change-detection fast-path."""

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

    def test_insert_populates_timestamps_via_schema_default(self, folder_db):
        """indexed_at + updated_at must come from the schema
        DEFAULT after the hardcoded CURRENT_TIMESTAMP literals are removed."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/defaults")
        indexer.save_folders([folder])

        row = folder_db.conn.execute(
            "SELECT indexed_at, updated_at FROM folders WHERE path = ?",
            (folder["path"],),
        ).fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None


class TestSaveFoldersReactivation:
    """save_folders() must reactivate folders previously marked status='removed'.

    Mirrors the file indexer's CASE-based reactivation in db/files.py:575-588.
    """

    def _mark_removed(self, db, path, reason="scan:missing"):
        cursor = db.conn.cursor()
        cursor.execute(
            """
            UPDATE folders
            SET status = 'removed',
                status_reason = ?,
                status_changed_at = '2020-01-01T00:00:00Z'
            WHERE path = ?
            """,
            (reason, path),
        )
        db.conn.commit()

    def _status_row(self, db, path):
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT status, status_reason, status_changed_at FROM folders WHERE path = ?",
            (path,),
        )
        return cursor.fetchone()

    def test_removed_folder_reactivates_on_resave(self, folder_db):
        """A status='removed' folder rescanned with identical fields flips back to active."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/proj")
        indexer.save_folders([folder])
        self._mark_removed(folder_db, folder["path"])
        before = self._status_row(folder_db, folder["path"])
        assert before["status"] == "removed"

        indexer.save_folders([folder])

        after = self._status_row(folder_db, folder["path"])
        assert after["status"] == "listed"
        assert after["status_reason"] is None
        assert after["status_changed_at"] != before["status_changed_at"]

    def test_reactivated_folder_counted_as_inserted(self, folder_db):
        """Reactivation buckets as 'inserted' (matches files.py:609-612 convention)."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/proj")
        indexer.save_folders([folder])
        self._mark_removed(folder_db, folder["path"])

        result = indexer.save_folders([folder])

        assert result == (1, 0, 0)

    def test_active_folder_unaffected_by_reactivation_logic(self, folder_db):
        """An already-active folder retains its status_reason; CASE only fires on removed rows."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/proj")
        indexer.save_folders([folder])
        cursor = folder_db.conn.cursor()
        cursor.execute(
            "UPDATE folders SET status_reason = 'scan:hidden' WHERE path = ?",
            (folder["path"],),
        )
        folder_db.conn.commit()

        result = indexer.save_folders([folder])

        after = self._status_row(folder_db, folder["path"])
        assert result == (0, 0, 1)
        assert after["status"] == "listed"
        assert after["status_reason"] == "scan:hidden"

    def test_removed_folder_with_field_change_also_reactivates(self, folder_db):
        """Reactivation + rename in the same UPDATE: rename lands and counter is 'inserted'."""
        indexer = FolderIndexer({}, folder_db)
        folder = _make_folder("/Users/test/Work/proj", name="proj")
        indexer.save_folders([folder])
        self._mark_removed(folder_db, folder["path"])

        renamed = dict(folder, name="renamed")
        result = indexer.save_folders([renamed])

        after_status = self._status_row(folder_db, folder["path"])
        after_name = _row(folder_db, folder["path"])["name"]
        assert result == (1, 0, 0)
        assert after_status["status"] == "listed"
        assert after_status["status_reason"] is None
        assert after_name == "renamed"


class TestExclusionPatterns:
    """FolderIndexer must apply exclusions.always like FileScanner does."""

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
        """Excluded subtree beneath a configured root is pruned."""
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


class TestLinkLocalFolderParents:
    """link_local_folder_parents must resolve parent_path → parent_folder_id."""

    def _get_parent_folder_id(self, db, path):
        row = db.conn.execute(
            "SELECT parent_folder_id FROM folders WHERE path = ?", (path,)
        ).fetchone()
        return row["parent_folder_id"] if row else None

    def _get_folder_id(self, db, path):
        row = db.conn.execute(
            "SELECT id FROM folders WHERE path = ?", (path,)
        ).fetchone()
        return row["id"] if row else None

    def test_links_parent_folder_ids(self, folder_db):
        """save_folders leaves parent_folder_id NULL; link resolves it from parent_path."""
        indexer = FolderIndexer({}, folder_db)
        folders = [
            _make_folder("/Users/test/Work", name="Work", parent_path="/Users/test"),
            _make_folder("/Users/test/Work/child1", name="child1"),
            _make_folder("/Users/test/Work/child1/grand", name="grand"),
            _make_folder("/Users/test/Work/child2", name="child2"),
        ]
        indexer.save_folders(folders)

        for f in folders:
            assert self._get_parent_folder_id(folder_db, f["path"]) is None

        from footprinter.db.folders import link_local_folder_parents

        updated = link_local_folder_parents(folder_db.conn)

        assert updated == 3
        assert self._get_parent_folder_id(folder_db, "/Users/test/Work") is None
        root_id = self._get_folder_id(folder_db, "/Users/test/Work")
        child1_id = self._get_folder_id(folder_db, "/Users/test/Work/child1")
        assert self._get_parent_folder_id(folder_db, "/Users/test/Work/child1") == root_id
        assert self._get_parent_folder_id(folder_db, "/Users/test/Work/child2") == root_id
        assert self._get_parent_folder_id(folder_db, "/Users/test/Work/child1/grand") == child1_id

    def test_link_is_idempotent(self, folder_db):
        """Second call returns 0 — all already linked."""
        indexer = FolderIndexer({}, folder_db)
        folders = [
            _make_folder("/Users/test/Work", name="Work", parent_path="/Users/test"),
            _make_folder("/Users/test/Work/child1", name="child1"),
        ]
        indexer.save_folders(folders)

        from footprinter.db.folders import link_local_folder_parents

        first = link_local_folder_parents(folder_db.conn)
        second = link_local_folder_parents(folder_db.conn)

        assert first == 1
        assert second == 0
