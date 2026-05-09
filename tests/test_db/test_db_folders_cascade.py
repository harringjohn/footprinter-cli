"""Tests for footprinter.db.folders.cascade_project_id."""

import sqlite3

import pytest


@pytest.fixture
def conn():
    """In-memory SQLite with folders, files, and projects tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE projects (  id INTEGER PRIMARY KEY,  project_name TEXT)")
    db.execute(
        "CREATE TABLE folders ("
        "  id INTEGER PRIMARY KEY,"
        "  path TEXT,"
        "  relative_path TEXT,"
        "  name TEXT,"
        "  source TEXT,"
        "  project_id INTEGER,"
        "  parent_folder_id INTEGER"
        ")"
    )
    db.execute(
        "CREATE TABLE files ("
        "  id INTEGER PRIMARY KEY,"
        "  name TEXT,"
        "  folder_id INTEGER,"
        "  project_id INTEGER,"
        "  status TEXT DEFAULT 'listed'"
        ")"
    )

    # Seed a project
    db.execute("INSERT INTO projects (id, project_name) VALUES (1, 'TestProject')")

    # Build a folder tree:
    #   1 (root)
    #     ├── 2 (child)
    #     │   └── 3 (grandchild)
    #     └── 4 (child)
    db.execute("INSERT INTO folders VALUES (1, '/root', 'root', 'root', 'local', NULL, NULL)")
    db.execute("INSERT INTO folders VALUES (2, '/root/child1', 'root/child1', 'child1', 'local', NULL, 1)")
    db.execute("INSERT INTO folders VALUES (3, '/root/child1/grand', 'root/child1/grand', 'grand', 'local', NULL, 2)")
    db.execute("INSERT INTO folders VALUES (4, '/root/child2', 'root/child2', 'child2', 'local', NULL, 1)")

    # Files in various folders
    db.execute("INSERT INTO files VALUES (10, 'a.txt', 1, NULL, 'listed')")
    db.execute("INSERT INTO files VALUES (11, 'b.txt', 2, NULL, 'listed')")
    db.execute("INSERT INTO files VALUES (12, 'c.txt', 3, NULL, 'listed')")
    db.execute("INSERT INTO files VALUES (13, 'removed.txt', 2, NULL, 'removed')")
    db.execute("INSERT INTO files VALUES (14, 'd.txt', 4, NULL, 'listed')")

    db.commit()
    return db


class TestCascadeProjectId:
    def test_sets_project_on_descendants(self, conn):
        from footprinter.db.folders import cascade_project_id

        result = cascade_project_id(conn, 1, 1)
        assert result["folders_updated"] == 4  # root + 3 descendants

        rows = conn.execute("SELECT id, project_id FROM folders ORDER BY id").fetchall()
        for row in rows:
            assert row["project_id"] == 1

    def test_sets_project_on_files(self, conn):
        from footprinter.db.folders import cascade_project_id

        result = cascade_project_id(conn, 1, 1)
        assert result["files_updated"] == 4  # 4 active files (not the removed one)

        rows = conn.execute("SELECT id, project_id FROM files WHERE status = 'listed' ORDER BY id").fetchall()
        for row in rows:
            assert row["project_id"] == 1

    def test_skips_removed_files(self, conn):
        from footprinter.db.folders import cascade_project_id

        cascade_project_id(conn, 1, 1)
        removed = conn.execute("SELECT project_id FROM files WHERE id = 13").fetchone()
        assert removed["project_id"] is None

    def test_clear(self, conn):
        from footprinter.db.folders import cascade_project_id

        # First set, then clear
        cascade_project_id(conn, 1, 1)
        result = cascade_project_id(conn, 1, None, clear=True)
        assert result["folders_updated"] == 4

        rows = conn.execute("SELECT project_id FROM folders ORDER BY id").fetchall()
        for row in rows:
            assert row["project_id"] is None

    def test_invalid_project(self, conn):
        from footprinter.db.folders import cascade_project_id

        with pytest.raises(ValueError, match="No project with id 999"):
            cascade_project_id(conn, 1, 999)

    def test_empty_tree(self, conn):
        """Folder with no descendants returns zero counts."""
        from footprinter.db.folders import cascade_project_id

        # Folder 3 (grandchild) has no children
        # Remove its file first so files_updated = 0
        conn.execute("DELETE FROM files WHERE folder_id = 3")
        conn.commit()

        result = cascade_project_id(conn, 3, 1)
        assert result["folders_updated"] == 1  # just the folder itself
        assert result["files_updated"] == 0
