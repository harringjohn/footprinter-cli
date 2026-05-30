"""
Guard tests for artifacts → files surface naming cleanup.

These tests assert the new state after the rename:
- Query module importable as `footprinter.db.files`
- Policy scope `source:files` resolves correctly
- Item type `"file"` resolves correctly in permissions/visibility
- MCP discovery uses `files_by_source` key (not `artifacts_by_source`)
"""

from footprinter.permissions import can_read
from footprinter.visibility import get_visibility


class TestDbFilesModule:
    """Verify the db.files query module is importable."""

    def test_db_files_import(self):
        from footprinter.db.files import get_file, list_files

        assert callable(list_files)
        assert callable(get_file)


class TestFileItemType:
    """Verify permissions and visibility resolve for 'file' item type."""

    def test_permission_resolves_for_file_type(self, tool_db):
        """can_read with item_type='file' should not raise."""
        cursor = tool_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status)"
            " VALUES (1, 'test.txt', '/tmp/test.txt', 'local', 'listed')"
        )
        tool_db.commit()
        # Should resolve without error (result depends on policies)
        result = can_read(tool_db, "file", 1)
        assert isinstance(result, bool)

    def test_visibility_resolves_for_file_type(self, tool_db):
        """get_visibility with item_type='file' should not raise."""
        cursor = tool_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status)"
            " VALUES (1, 'test.txt', '/tmp/test.txt', 'local', 'listed')"
        )
        tool_db.commit()
        result = get_visibility(tool_db, "file", 1)
        assert result in ("full", "opaque", "hidden")


class TestSourceFilesScope:
    """Verify source:files policy scope works."""

    def test_source_files_permission_scope(self, tool_db):
        """Policy with scope 'source:files' should affect file permissions."""
        cursor = tool_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status)"
            " VALUES (1, 'test.txt', '/tmp/test.txt', 'local', 'listed')"
        )
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        tool_db.commit()
        assert can_read(tool_db, "file", 1) is True

    def test_source_files_visibility_scope(self, tool_db):
        """Policy with scope 'source:files' should affect file visibility."""
        cursor = tool_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source, status)"
            " VALUES (1, 'test.txt', '/tmp/test.txt', 'local', 'listed')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'full')")
        tool_db.commit()
        assert get_visibility(tool_db, "file", 1) == "full"


class TestMcpDiscoveryKeys:
    """Verify MCP discovery uses 'files_by_source' key."""

    def test_status_uses_files_keys(self):
        """Status response should use files_by_source, not artifacts_by_source."""
        import inspect

        from footprinter.db.status import get_mcp_status

        source = inspect.getsource(get_mcp_status)
        assert "files_by_source" in source, "Should use files_by_source key"
        assert "files_by_status" in source, "Should use files_by_status key"
        assert "artifacts_by_source" not in source, "Should not use artifacts_by_source"
        assert "artifacts_by_status" not in source, "Should not use artifacts_by_status"
