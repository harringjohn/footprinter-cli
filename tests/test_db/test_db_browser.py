"""Tests for footprinter.db.browser query functions.

Verifies that list_visits() and get_visit() include
mcp_view and mcp_read in returned dicts.
"""

from footprinter.db.browser import get_visit, list_visits


class TestBrowserAccessColumns:
    """Access control columns must appear in browser query results."""

    def _insert_visit(self, conn):
        conn.execute(
            """
            INSERT INTO visits
                (url, title, visit_time, browser, mcp_view, mcp_read)
            VALUES
                ('https://example.com', 'Example', '2025-01-01 12:00:00', 'safari',
                 'visible', 'allow')
            """
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_list_visits_includes_access_columns(self, tool_db):
        self._insert_visit(tool_db)
        result = list_visits(tool_db)
        visit = result["visits"][0]
        assert visit["mcp_view"] == "visible"
        assert visit["mcp_read"] == "allow"

    def test_get_visit_includes_access_columns(self, tool_db):
        visit_id = self._insert_visit(tool_db)
        visit = get_visit(tool_db, visit_id)
        assert visit is not None
        assert visit["mcp_view"] == "visible"
        assert visit["mcp_read"] == "allow"

    def test_get_visit_excludes_assignment_source(self, tool_db):
        """get_visit() return dict must NOT include assignment_source."""
        visit_id = self._insert_visit(tool_db)
        visit = get_visit(tool_db, visit_id)
        assert visit is not None
        assert "assignment_source" not in visit, "get_visit() should not return assignment_source"
