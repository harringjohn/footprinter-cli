"""Tests for footprinter.db.projects listing/aggregate behavior.

Pins that inline COUNT subqueries inside ``list_projects`` and
``get_project_detail`` align with the standardized
``default_exclude=["removed"]`` parent filter — unlisted children are
counted, only removed are excluded.
"""

from footprinter.db.projects import create_project, get_project_detail, list_projects, update_project


def _seed_project_with_mixed_status_files(conn):
    conn.execute(
        "INSERT INTO projects (id, name, status) "
        "VALUES (1, 'Alpha', 'listed')"
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


class TestCreateProjectSlug:
    """create_project derives a (non-unique) slug from the name."""

    def test_create_populates_slug_from_name(self, tool_db):
        row = create_project(tool_db, name="My Web App")
        assert row["name"] == "My Web App"
        assert row["slug"] == "my-web-app"

    def test_names_are_not_unique_slug_repeats(self, tool_db):
        a = create_project(tool_db, name="Duplicate")
        b = create_project(tool_db, name="Duplicate")
        assert a["id"] != b["id"]
        assert a["slug"] == b["slug"] == "duplicate"

    def test_update_regenerates_slug_on_rename(self, tool_db):
        row = create_project(tool_db, name="Before")
        update_project(tool_db, row["id"], name="After Rename")
        renamed = get_project_detail(tool_db, row["id"])
        assert renamed["name"] == "After Rename"
        stored_slug = tool_db.execute(
            "SELECT slug FROM projects WHERE id = ?", (row["id"],)
        ).fetchone()["slug"]
        assert stored_slug == "after-rename"
