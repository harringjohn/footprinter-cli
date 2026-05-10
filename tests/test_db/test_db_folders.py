"""Tests for footprinter.db.folders query functions.

Verifies that list_folders() and get_folder() include both
mcp_view and mcp_read in returned dicts.
"""

from footprinter.db.folders import (
    get_folder,
    get_folder_navigation,
    list_folders,
    mark_removed_folders,
)


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


class TestListFoldersDefaultExclude:
    """Regression guard: default filter excludes ``removed`` only."""

    def _insert_mixed_status(self, conn):
        conn.execute(
            """
            INSERT INTO folders (id, path, relative_path, name, source, status)
            VALUES
                (1, '/Users/test/Listed',   '/Listed',   'Listed',   'local', 'listed'),
                (2, '/Users/test/Unlisted', '/Unlisted', 'Unlisted', 'local', 'unlisted'),
                (3, '/Users/test/Removed',  '/Removed',  'Removed',  'local', 'removed')
            """
        )
        conn.commit()

    def test_default_returns_listed_and_unlisted(self, tool_db):
        self._insert_mixed_status(tool_db)
        result = list_folders(tool_db, depth=None)
        names = {f["name"] for f in result["folders"]}
        assert names == {"Listed", "Unlisted"}

    def test_default_excludes_removed(self, tool_db):
        self._insert_mixed_status(tool_db)
        result = list_folders(tool_db, depth=None)
        names = {f["name"] for f in result["folders"]}
        assert "Removed" not in names

    def test_status_all_returns_everything(self, tool_db):
        self._insert_mixed_status(tool_db)
        result = list_folders(tool_db, depth=None, status="all")
        names = {f["name"] for f in result["folders"]}
        assert names == {"Listed", "Unlisted", "Removed"}


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
            assert cursor.fetchone()["status"] == "listed"

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
        assert cursor.fetchone()["status"] == "listed"

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
        assert cursor.fetchone()["status"] == "listed"

    def test_skips_already_removed(self, tool_db):
        fid = self._insert_local(tool_db, "/tmp/old-phantom")
        tool_db.execute("UPDATE folders SET status = 'removed' WHERE id = ?", (fid,))
        tool_db.commit()

        # Non-overlapping scanned set — but the row is already removed,
        # so it should not appear in the returned list.
        removed = mark_removed_folders(tool_db, {"/tmp/something-else"})
        assert fid not in removed


class TestListFoldersStatusFilter:
    """list_folders() must exclude status='removed' by default and accept overrides (FPR-1707)."""

    def _insert(self, conn, path: str, status: str = "listed") -> int:
        cursor = conn.execute(
            """INSERT INTO folders (path, relative_path, name, source, status)
               VALUES (?, ?, ?, 'local', ?)""",
            (path, path, path.rsplit("/", 1)[-1], status),
        )
        conn.commit()
        return cursor.lastrowid

    def test_default_excludes_removed(self, tool_db):
        active_id = self._insert(tool_db, "/tmp/keep", status="listed")
        self._insert(tool_db, "/tmp/gone", status="removed")

        result = list_folders(tool_db)
        assert result["pagination"]["total"] == 1
        ids = [f["id"] for f in result["folders"]]
        assert ids == [active_id]

    def test_status_all_includes_removed(self, tool_db):
        self._insert(tool_db, "/tmp/keep", status="listed")
        self._insert(tool_db, "/tmp/gone", status="removed")

        result = list_folders(tool_db, status="all")
        assert result["pagination"]["total"] == 2

    def test_status_exact_match(self, tool_db):
        self._insert(tool_db, "/tmp/keep", status="listed")
        removed_id = self._insert(tool_db, "/tmp/gone", status="removed")
        self._insert(tool_db, "/tmp/quiet", status="unlisted")

        result = list_folders(tool_db, status="removed")
        assert result["pagination"]["total"] == 1
        assert result["folders"][0]["id"] == removed_id

    def test_status_list(self, tool_db):
        active_id = self._insert(tool_db, "/tmp/keep", status="listed")
        self._insert(tool_db, "/tmp/gone", status="removed")
        hidden_id = self._insert(tool_db, "/tmp/quiet", status="unlisted")

        result = list_folders(tool_db, status=["listed", "unlisted"])
        assert result["pagination"]["total"] == 2
        assert {f["id"] for f in result["folders"]} == {active_id, hidden_id}


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
               VALUES ('a.py', '/Users/u/Work/alpha/src/lib/a.py', 'local', 'listed', 123, ?)""",
            (deep_id,),
        )
        tool_db.commit()

        result = list_folders(tool_db, depth=5)
        by_id = {f["id"]: f for f in result["folders"]}
        # Parent folder rolls up the descendant file count + size.
        assert by_id[parent_id]["direct_files"] == 1
        assert by_id[parent_id]["total_size_bytes"] == 123


class TestGetFolderNavigationStatusFilter:
    """get_folder_navigation respects the status kwarg (FPR-1678).

    Default returns only listed children. Widening lets ADMIN-flagged callers
    see unlisted/removed files; recursive count widens to match.
    """

    def _setup(self, conn) -> int:
        cur = conn.execute(
            """INSERT INTO folders (path, relative_path, name, source, mcp_view)
               VALUES ('/Users/u/proj', '/proj', 'proj', 'local', 'visible')"""
        )
        folder_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO files
                   (name, path, source, status, status_reason,
                    folder_id, mcp_view, mcp_read)
               VALUES (?, ?, 'local', ?, ?, ?, 'visible', 'allow')""",
            [
                ("listed.py", "/Users/u/proj/listed.py", "listed", None, folder_id),
                ("unlisted.py", "/Users/u/proj/unlisted.py", "unlisted", "user_hidden", folder_id),
                ("removed.py", "/Users/u/proj/removed.py", "removed", "deleted_by_user", folder_id),
            ],
        )
        conn.commit()
        return folder_id

    def test_default_returns_only_listed_files(self, tool_db):
        folder_id = self._setup(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj")
        names = sorted(f["name"] for f in result["files"])
        assert names == ["listed.py"]

    def test_status_all_returns_all_files(self, tool_db):
        folder_id = self._setup(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj", status="all")
        names = sorted(f["name"] for f in result["files"])
        assert names == ["listed.py", "removed.py", "unlisted.py"]

    def test_status_listed_unlisted(self, tool_db):
        folder_id = self._setup(tool_db)
        result = get_folder_navigation(
            tool_db, folder_id, "/Users/u/proj", status=["listed", "unlisted"]
        )
        names = sorted(f["name"] for f in result["files"])
        assert names == ["listed.py", "unlisted.py"]

    def test_files_include_status_reason(self, tool_db):
        folder_id = self._setup(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj", status="all")
        by_name = {f["name"]: f for f in result["files"]}
        assert by_name["unlisted.py"]["status"] == "unlisted"
        assert by_name["unlisted.py"]["status_reason"] == "user_hidden"
        assert by_name["removed.py"]["status"] == "removed"
        assert by_name["removed.py"]["status_reason"] == "deleted_by_user"

    def test_recursive_count_default_excludes_unlisted_and_removed(self, tool_db):
        folder_id = self._setup(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj")
        assert result["recursive_file_count"] == 1

    def test_recursive_count_widens_with_status_all(self, tool_db):
        folder_id = self._setup(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj", status="all")
        assert result["recursive_file_count"] == 3

    def _setup_with_subfolders(self, conn) -> int:
        """Mirror _setup but with mixed-status subfolders instead of files."""
        cur = conn.execute(
            """INSERT INTO folders (path, relative_path, name, source, status, mcp_view)
               VALUES ('/Users/u/proj', '/proj', 'proj', 'local', 'listed', 'visible')"""
        )
        folder_id = cur.lastrowid
        conn.executemany(
            """INSERT INTO folders
                   (path, relative_path, name, source, status, status_reason,
                    parent_folder_id, mcp_view, mcp_read)
               VALUES (?, ?, ?, 'local', ?, ?, ?, 'visible', 'allow')""",
            [
                ("/Users/u/proj/listed_sub", "/proj/listed_sub", "listed_sub",
                 "listed", None, folder_id),
                ("/Users/u/proj/unlisted_sub", "/proj/unlisted_sub", "unlisted_sub",
                 "unlisted", "user_hidden", folder_id),
                ("/Users/u/proj/removed_sub", "/proj/removed_sub", "removed_sub",
                 "removed", "deleted_by_user", folder_id),
            ],
        )
        conn.commit()
        return folder_id

    def test_default_returns_only_listed_subfolders(self, tool_db):
        folder_id = self._setup_with_subfolders(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj")
        names = sorted(sf["name"] for sf in result["subfolders"])
        assert names == ["listed_sub"]

    def test_status_all_returns_all_subfolders(self, tool_db):
        folder_id = self._setup_with_subfolders(tool_db)
        result = get_folder_navigation(tool_db, folder_id, "/Users/u/proj", status="all")
        names = sorted(sf["name"] for sf in result["subfolders"])
        assert names == ["listed_sub", "removed_sub", "unlisted_sub"]

    def test_status_widens_subfolders(self, tool_db):
        folder_id = self._setup_with_subfolders(tool_db)
        result = get_folder_navigation(
            tool_db, folder_id, "/Users/u/proj", status=["listed", "unlisted"]
        )
        names = sorted(sf["name"] for sf in result["subfolders"])
        assert names == ["listed_sub", "unlisted_sub"]
