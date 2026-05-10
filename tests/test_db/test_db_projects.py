"""Tests for footprinter.db.projects listing/aggregate behavior.

Pins that inline COUNT subqueries inside ``list_projects`` and
``get_project_detail`` align with the standardized
``default_exclude=["removed"]`` parent filter — unlisted children are
counted, only removed are excluded.
"""

from footprinter.db.projects import get_project_detail, list_projects


def _seed_project_with_mixed_status_files(conn):
    conn.execute(
        "INSERT INTO projects (id, project_name, root_path, status) "
        "VALUES (1, 'Alpha', '/p/alpha', 'listed')"
    )
    conn.execute(
        """INSERT INTO files (id, name, path, source, status, content_type, size_bytes, project_id)
           VALUES
               (1, 'a.md', '/p/alpha/a.md', 'local', 'listed',   'markdown', 100, 1),
               (2, 'b.md', '/p/alpha/b.md', 'local', 'unlisted', 'markdown', 200, 1),
               (3, 'c.md', '/p/alpha/c.md', 'local', 'removed',  'markdown', 300, 1)"""
    )
    conn.commit()


class TestListProjectsFileCountAlignment:
    """list_projects file_count subquery must include unlisted files."""

    def test_file_count_includes_unlisted(self, tool_db):
        _seed_project_with_mixed_status_files(tool_db)
        result = list_projects(tool_db)
        project = next(p for p in result["projects"] if p["name"] == "Alpha")
        # listed (1) + unlisted (1), removed excluded → 2 files, 300 bytes
        assert project["file_count"] == 2
        assert project["size_bytes"] == 300


class TestGetProjectDetailAlignment:
    """get_project_detail file_count and total_size subqueries must include unlisted files."""

    def test_file_count_and_size_include_unlisted(self, tool_db):
        _seed_project_with_mixed_status_files(tool_db)
        detail = get_project_detail(tool_db, 1)
        assert detail is not None
        assert detail["file_count"] == 2
        assert detail["total_size"] == 300
