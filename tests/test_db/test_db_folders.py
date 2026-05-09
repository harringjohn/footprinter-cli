"""Tests for footprinter.db.folders query functions.

Verifies that list_folders() and get_folder() include both
mcp_view and mcp_read in returned dicts.
"""

from footprinter.db.folders import get_folder, list_folders, mark_removed_folders


class TestFoldersAccessColumns:
    """Access control columns must appear in folder query results."""

    def _insert_folder(self, conn):
        conn.execute(
            """
            INSERT INTO folders
                (path, relative_path, name, source,
                 mcp_view, mcp_read)
            VALUES
                ('/Users/test/Work', '/Work', 'Work', 'local',
                 'visible', 'allow')
            """
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_list_folders_includes_access_columns(self, tool_db):
        self._insert_folder(tool_db)
        result = list_folders(tool_db, depth=None)
        folder = result["folders"][0]
        assert folder["mcp_view"] == "visible"
        assert folder["mcp_read"] == "allow"

    def test_get_folder_includes_access_columns(self, tool_db):
        folder_id = self._insert_folder(tool_db)
        folder = get_folder(tool_db, folder_id)
        assert folder is not None
        assert folder["mcp_view"] == "visible"
        assert folder["mcp_read"] == "allow"


class TestMarkRemovedFolders:
    """Test folders.mark_removed_folders() — phantom folder cleanup (FPR-1654)."""

    def _insert_local(self, conn, path: str) -> int:
        cursor = conn.execute(
            """INSERT INTO folders (path, relative_path, name, source)
               VALUES (?, ?, ?, 'local')""",
            (path, path, path.rsplit("/", 1)[-1] or path),
        )
        conn.commit()
        return cursor.lastrowid

    def test_marks_missing_paths_as_removed(self, tool_db):
        for p in ["/tmp/a", "/tmp/b", "/tmp/c"]:
            self._insert_local(tool_db, p)

        removed = mark_removed_folders(tool_db, {"/tmp/a", "/tmp/b"})
        assert len(removed) == 1

        cursor = tool_db.cursor()
        cursor.execute(
            "SELECT status, status_reason, status_changed_at FROM folders WHERE path = '/tmp/c'"
        )
        row = cursor.fetchone()
        assert row["status"] == "removed"
        assert row["status_reason"] == "folder_deleted"
        assert row["status_changed_at"] is not None
        for p in ["/tmp/a", "/tmp/b"]:
            cursor.execute("SELECT status FROM folders WHERE path = ?", (p,))
            assert cursor.fetchone()["status"] == "active"

    def test_returns_removed_ids(self, tool_db):
        ids = [self._insert_local(tool_db, p) for p in ["/tmp/x", "/tmp/y", "/tmp/z"]]

        removed = mark_removed_folders(tool_db, {"/tmp/x"})
        assert isinstance(removed, list)
        assert set(removed) == {ids[1], ids[2]}

    def test_empty_paths_noop(self, tool_db):
        fid = self._insert_local(tool_db, "/tmp/keep")
        result = mark_removed_folders(tool_db, set())
        assert result == []
        cursor = tool_db.cursor()
        cursor.execute("SELECT status FROM folders WHERE id = ?", (fid,))
        assert cursor.fetchone()["status"] == "active"

    def test_only_targets_local_source(self, tool_db):
        local_id = self._insert_local(tool_db, "/tmp/local-only")
        # Drive folder with the same path is permitted because the unique
        # index on path is conditional (source='local').
        tool_db.execute(
            """INSERT INTO folders (path, relative_path, name, source, external_id, account)
               VALUES ('/drive/shared', '/shared', 'shared', 'drive', 'd1', 'acct')"""
        )
        tool_db.commit()
        drive_id = tool_db.execute(
            "SELECT id FROM folders WHERE source = 'drive'"
        ).fetchone()["id"]

        # Scan reports a different local path — local-only should be removed,
        # drive folder must stay active.
        removed = mark_removed_folders(tool_db, {"/tmp/something-else"})
        assert local_id in removed
        assert drive_id not in removed

        cursor = tool_db.cursor()
        cursor.execute("SELECT status FROM folders WHERE id = ?", (drive_id,))
        assert cursor.fetchone()["status"] == "active"

    def test_skips_already_removed(self, tool_db):
        fid = self._insert_local(tool_db, "/tmp/old-phantom")
        tool_db.execute("UPDATE folders SET status = 'removed' WHERE id = ?", (fid,))
        tool_db.commit()

        # Non-overlapping scanned set — but the row is already removed,
        # so it should not appear in the returned list.
        removed = mark_removed_folders(tool_db, {"/tmp/something-else"})
        assert fid not in removed


class TestListFoldersDepthDefault:
    """Default depth must be None — full listing, not depth-1 subset (FPR-1631)."""

    def _insert(self, conn, path: str, relative_path: str) -> int:
        cursor = conn.execute(
            """INSERT INTO folders (path, relative_path, name, source)
               VALUES (?, ?, ?, 'local')""",
            (path, relative_path, path.rsplit("/", 1)[-1]),
        )
        conn.commit()
        return cursor.lastrowid

    def test_default_depth_returns_all_levels(self, tool_db):
        # depth 0: one segment under home
        self._insert(tool_db, "/Users/u/Work", "/Work")
        # depth 1: two segments under home
        self._insert(tool_db, "/Users/u/Work/alpha", "/Work/alpha")
        # depth 3: four segments under home
        self._insert(tool_db, "/Users/u/Work/alpha/src/lib/x", "/Work/alpha/src/lib/x")

        result = list_folders(tool_db)
        assert result["pagination"]["total"] == 3
        assert len(result["folders"]) == 3


class TestListFoldersUsesPrecomputedCounts:
    """When depth is None, use folders.direct_file_count and total_size_bytes
    instead of correlated subqueries against the files table (FPR-1631)."""

    def test_reads_precomputed_columns_without_files(self, tool_db):
        tool_db.execute(
            """INSERT INTO folders
                   (path, relative_path, name, source,
                    direct_file_count, total_size_bytes)
               VALUES
                   ('/Users/u/Work', '/Work', 'Work', 'local', 42, 999)"""
        )
        tool_db.commit()

        result = list_folders(tool_db, depth=None)
        folder = result["folders"][0]
        # No rows exist in files; the subquery path would return 0/0.
        # The fast path must read directly from the folders columns.
        assert folder["direct_files"] == 42
        assert folder["total_size_bytes"] == 999


class TestListFoldersDepthExplicitStillRollsUp:
    """When depth is explicitly set, the CTE+subquery rollup is preserved
    so callers asking for shallow listings still see descendant counts (FPR-1631)."""

    def test_depth_filter_rolls_up_descendant_files(self, tool_db):
        parent_cur = tool_db.execute(
            """INSERT INTO folders (path, relative_path, name, source)
               VALUES ('/Users/u/Work/alpha', '/Work/alpha', 'alpha', 'local')"""
        )
        parent_id = parent_cur.lastrowid

        deep_cur = tool_db.execute(
            """INSERT INTO folders (path, relative_path, name, source, parent_folder_id)
               VALUES ('/Users/u/Work/alpha/src/lib', '/Work/alpha/src/lib', 'lib', 'local', ?)""",
            (parent_id,),
        )
        deep_id = deep_cur.lastrowid

        tool_db.execute(
            """INSERT INTO files (name, path, source, status, size_bytes, folder_id)
               VALUES ('a.py', '/Users/u/Work/alpha/src/lib/a.py', 'local', 'active', 123, ?)""",
            (deep_id,),
        )
        tool_db.commit()

        result = list_folders(tool_db, depth=5)
        by_id = {f["id"]: f for f in result["folders"]}
        # Parent folder rolls up the descendant file count + size.
        assert by_id[parent_id]["direct_files"] == 1
        assert by_id[parent_id]["total_size_bytes"] == 123
