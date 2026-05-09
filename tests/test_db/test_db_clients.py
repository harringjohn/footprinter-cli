"""Tests for footprinter.db.clients listing behavior.

Pins the standardized ``default_exclude=["removed"]`` filter pattern:
listed and unlisted clients are returned by default; removed clients are
hidden unless ``status="all"`` (or an explicit value) is passed.
"""

from footprinter.db.clients import list_clients


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
