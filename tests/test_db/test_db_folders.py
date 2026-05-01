"""Tests for footprinter.db.folders query functions.

Verifies that list_folders() and get_folder() include both
mcp_view and mcp_read in returned dicts.
"""

from footprinter.db.folders import get_folder, list_folders


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
