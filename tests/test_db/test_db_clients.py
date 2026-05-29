"""Tests for footprinter.db.clients listing behavior.

Pins the standardized ``default_exclude=["removed"]`` filter pattern:
listed and unlisted clients are returned by default; removed clients are
hidden unless ``status="all"`` (or an explicit value) is passed.
"""

from footprinter.db.clients import create_client, list_clients
from footprinter.db.projects import create_project


def _insert_clients(conn):
    conn.execute(
        """
        INSERT INTO clients (id, name, slug, client_type, status)
        VALUES
            (1, 'Listed Co',   'listed-co',   'external', 'listed'),
            (2, 'Unlisted Co', 'unlisted-co', 'external', 'unlisted'),
            (3, 'Removed Co',  'removed-co',  'external', 'removed')
        """
    )
    conn.commit()


class TestListClientsDefaultExclude:
    """Default filter excludes ``removed`` only — unlisted is visible."""

    def test_default_returns_listed_and_unlisted(self, tool_db):
        _insert_clients(tool_db)
        result = list_clients(tool_db)
        statuses = {c["status"] for c in result["clients"]}
        assert statuses == {"listed", "unlisted"}

    def test_default_excludes_removed(self, tool_db):
        _insert_clients(tool_db)
        result = list_clients(tool_db)
        names = {c["name"] for c in result["clients"]}
        assert "Removed Co" not in names

    def test_status_all_returns_everything(self, tool_db):
        _insert_clients(tool_db)
        result = list_clients(tool_db, status="all")
        statuses = {c["status"] for c in result["clients"]}
        assert statuses == {"listed", "unlisted", "removed"}

    def test_explicit_status_filter(self, tool_db):
        _insert_clients(tool_db)
        result = list_clients(tool_db, status="unlisted")
        names = [c["name"] for c in result["clients"]]
        assert names == ["Unlisted Co"]


class TestListClientsFileCountAlignment:
    """file_count subquery must match the parent default (exclude only removed)."""

    def _seed_client_with_mixed_status_files(self, conn):
        conn.execute(
            "INSERT INTO clients (id, name, slug, client_type, status) "
            "VALUES (1, 'Acme', 'acme', 'external', 'listed')"
        )
        conn.execute(
            "INSERT INTO projects (id, name, status, client_id) "
            "VALUES (1, 'Alpha', 'listed', 1)"
        )
        conn.execute(
            """INSERT INTO files (id, name, path, source, status, content_type, size_bytes, project_id)
               VALUES
                   (1, 'a.md', '/p/alpha/a.md', 'local', 'listed',   'markdown', 100, 1),
                   (2, 'b.md', '/p/alpha/b.md', 'local', 'unlisted', 'markdown', 200, 1),
                   (3, 'c.md', '/p/alpha/c.md', 'local', 'removed',  'markdown', 300, 1)"""
        )
        conn.commit()

    def test_file_count_includes_unlisted(self, tool_db):
        self._seed_client_with_mixed_status_files(tool_db)
        result = list_clients(tool_db)
        client = next(c for c in result["clients"] if c["name"] == "Acme")
        # listed (1) + unlisted (1), removed excluded
        assert client["file_count"] == 2


class TestSchemaDefaultsForNewRows:
    """New-row insert paths must produce status='listed' via the schema DEFAULT.

    Guards that removing the hardcoded ``'listed'`` literal from
    ``create_client`` / ``create_project`` INSERTs must not change the
    observed value for new rows.
    """

    def test_create_client_status_default(self, tool_db):
        result = create_client(
            tool_db,
            name="Defaults Co",
            client_type="external",
        )
        row = tool_db.execute(
            "SELECT status FROM clients WHERE id = ?", (result["id"],)
        ).fetchone()
        assert row["status"] == "listed"

    def test_create_project_status_default(self, tool_db):
        result = create_project(
            tool_db,
            name="Defaults Project",
        )
        row = tool_db.execute(
            "SELECT status FROM projects WHERE id = ?", (result["id"],)
        ).fetchone()
        assert row["status"] == "listed"

    def test_create_project_explicit_status_honored(self, tool_db):
        """Caller-supplied status must still flow through to the row."""
        result = create_project(
            tool_db,
            name="Unlisted Project",
            status="unlisted",
        )
        row = tool_db.execute(
            "SELECT status FROM projects WHERE id = ?", (result["id"],)
        ).fetchone()
        assert row["status"] == "unlisted"
