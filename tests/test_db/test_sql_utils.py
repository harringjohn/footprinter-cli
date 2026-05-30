"""Tests for footprinter.db.sql_utils.update_entity_relationships."""

import sqlite3

import pytest

from footprinter.db.sql_utils import update_entity_relationships


class TestUpdateEntityRelationships:
    def test_returns_none_for_missing_entity(self, db_conn):
        result = update_entity_relationships(db_conn, "visits", 999, project_id=1)
        assert result is None

    def test_sets_project_id(self, db_conn):
        result = update_entity_relationships(db_conn, "visits", 1, project_id=1)
        assert result is True
        row = db_conn.execute("SELECT project_id FROM visits WHERE id = 1").fetchone()
        assert row["project_id"] == 1

    def test_sets_client_id(self, db_conn):
        result = update_entity_relationships(db_conn, "visits", 1, client_id=1)
        assert result is True
        row = db_conn.execute("SELECT client_id FROM visits WHERE id = 1").fetchone()
        assert row["client_id"] == 1

    def test_clears_project_with_zero(self, db_conn):
        update_entity_relationships(db_conn, "files", 1, project_id=1)
        update_entity_relationships(db_conn, "files", 1, project_id=0)
        row = db_conn.execute("SELECT project_id FROM files WHERE id = 1").fetchone()
        assert row["project_id"] is None

    def test_clears_client_with_zero(self, db_conn):
        update_entity_relationships(db_conn, "visits", 1, client_id=1)
        update_entity_relationships(db_conn, "visits", 1, client_id=0)
        row = db_conn.execute("SELECT client_id FROM visits WHERE id = 1").fetchone()
        assert row["client_id"] is None

    def test_raises_for_invalid_project(self, db_conn):
        with pytest.raises(ValueError, match="No project with id 999"):
            update_entity_relationships(db_conn, "visits", 1, project_id=999)

    def test_raises_for_invalid_client(self, db_conn):
        with pytest.raises(ValueError, match="No client with id 999"):
            update_entity_relationships(db_conn, "visits", 1, client_id=999)

    def test_noop_returns_true(self, db_conn):
        result = update_entity_relationships(db_conn, "visits", 1)
        assert result is True

    def test_commits_persists(self, db_conn):
        update_entity_relationships(db_conn, "visits", 1, project_id=1)
        fresh = sqlite3.connect(db_conn.execute("PRAGMA database_list").fetchone()[2])
        fresh.row_factory = sqlite3.Row
        row = fresh.execute("SELECT project_id FROM visits WHERE id = 1").fetchone()
        fresh.close()
        assert row["project_id"] == 1

    def test_assignment_source_fallback(self, tmp_path):
        """DB without assignment_source column still succeeds via OperationalError fallback."""
        db_path = tmp_path / "minimal.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, project_id INTEGER, client_id INTEGER)")
        conn.execute("INSERT INTO projects (id, name) VALUES (1, 'P1')")
        conn.execute("INSERT INTO visits (id) VALUES (1)")
        conn.commit()
        result = update_entity_relationships(conn, "visits", 1, project_id=1)
        assert result is True
        row = conn.execute("SELECT project_id FROM visits WHERE id = 1").fetchone()
        assert row["project_id"] == 1
        conn.close()

    @pytest.mark.parametrize("table", ["visits", "files", "chats", "emails"])
    def test_works_across_tables(self, db_conn, table):
        result = update_entity_relationships(db_conn, table, 1, project_id=1)
        assert result is True
        row = db_conn.execute(f"SELECT project_id FROM {table} WHERE id = 1").fetchone()
        assert row["project_id"] == 1
