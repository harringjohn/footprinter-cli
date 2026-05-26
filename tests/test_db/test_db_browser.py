"""Tests for footprinter.db.browser query functions.

Verifies that list_visits() and get_visit() include
mcp_view and mcp_read in returned dicts and that the
standardized default_exclude=["removed"] filter is applied.
"""

from footprinter.db.browser import get_visit, insert_visit, list_visits


def _insert_visits_mixed_status(conn):
    conn.execute(
        """
        INSERT INTO visits (id, url, title, visit_time, browser, status,
                            mcp_view, mcp_read)
        VALUES
            (1, 'https://listed.example.com',   'Listed',   '2026-01-15 10:00:00',
             'safari', 'listed',   'visible', 'allow'),
            (2, 'https://unlisted.example.com', 'Unlisted', '2026-01-15 11:00:00',
             'safari', 'unlisted', 'visible', 'allow'),
            (3, 'https://removed.example.com',  'Removed',  '2026-01-15 12:00:00',
             'safari', 'removed',  'visible', 'allow')
        """
    )
    conn.commit()


class TestListVisitsDefaultExclude:
    """Default filter excludes ``removed`` only — unlisted is visible."""

    def test_default_returns_listed_and_unlisted(self, tool_db):
        _insert_visits_mixed_status(tool_db)
        result = list_visits(tool_db)
        titles = {v["title"] for v in result["visits"]}
        assert titles == {"Listed", "Unlisted"}

    def test_default_excludes_removed(self, tool_db):
        _insert_visits_mixed_status(tool_db)
        result = list_visits(tool_db)
        titles = {v["title"] for v in result["visits"]}
        assert "Removed" not in titles

    def test_status_all_returns_everything(self, tool_db):
        _insert_visits_mixed_status(tool_db)
        result = list_visits(tool_db, status="all")
        titles = {v["title"] for v in result["visits"]}
        assert titles == {"Listed", "Unlisted", "Removed"}

    def test_explicit_status_filter(self, tool_db):
        _insert_visits_mixed_status(tool_db)
        result = list_visits(tool_db, status="removed")
        titles = [v["title"] for v in result["visits"]]
        assert titles == ["Removed"]


class TestGetVisitNoStatusFilter:
    """Single-record getter returns regardless of status (matches get_email/get_chat)."""

    def test_get_visit_returns_unlisted(self, tool_db):
        _insert_visits_mixed_status(tool_db)
        visit = get_visit(tool_db, 2)
        assert visit is not None
        assert visit["title"] == "Unlisted"

    def test_get_visit_returns_removed(self, tool_db):
        _insert_visits_mixed_status(tool_db)
        visit = get_visit(tool_db, 3)
        assert visit is not None
        assert visit["title"] == "Removed"


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


class TestInsertVisitSchemaDefaults:
    """indexed_at / updated_at must come from the schema
    DEFAULT after the hardcoded CURRENT_TIMESTAMP literals are removed."""

    def test_insert_visit_populates_timestamps_via_default(self, tool_db):
        visit_id = insert_visit(
            tool_db,
            {
                "url": "https://defaults.example.com",
                "title": "Defaults",
                "visit_time": "2026-01-15 10:00:00",
                "browser": "safari",
            },
        )
        assert isinstance(visit_id, int)

        row = tool_db.execute(
            "SELECT indexed_at, updated_at FROM visits WHERE id = ?", (visit_id,)
        ).fetchone()
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None
