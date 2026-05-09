"""Tests for footprinter.cli._common exports and backward compatibility.

Covers: resolve_identifier, connect_db, add_json_flag, output_json,
        console, VALID_STATUSES, _make_slug, queries backward compat,
        and the no-delete-project guardrail.
"""

import argparse
import json
import sqlite3
from datetime import datetime

import pytest

# ---------------------------------------------------------------------------
# Fixture: seeded database with 1 client + 1 project
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver_db(tmp_path):
    """Create a DB with full schema, seed 1 client + 1 project."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    db.conn.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO clients (name, slug, client_type, status) VALUES ('Acme Corp', 'acme-corp', 'external', 'listed')"
    )
    conn.execute(
        "INSERT INTO projects (project_name, project_type, root_path, status) "
        "VALUES ('Manila', 'python', '/Users/test/Work/manila', 'listed')"
    )
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. TestResolveIdentifierNumericId
# ---------------------------------------------------------------------------


class TestResolveIdentifierWhitelist:
    def test_invalid_table_raises_value_error(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        with pytest.raises(ValueError, match="Invalid table/column"):
            resolve_identifier(resolver_db, "evil_table", "name", "1")

    def test_invalid_column_raises_value_error(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        with pytest.raises(ValueError, match="Invalid table/column"):
            resolve_identifier(resolver_db, "clients", "evil_col", "1")

    def test_both_invalid_raises_value_error(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        with pytest.raises(ValueError, match="Invalid table/column"):
            resolve_identifier(resolver_db, "evil_table", "evil_col", "1")

    def test_valid_clients_table_allowed(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "clients", "name", "Acme Corp")
        assert result == 1

    def test_valid_projects_table_allowed(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "projects", "project_name", "Manila")
        assert result == 1


# ---------------------------------------------------------------------------
# 1. TestResolveIdentifierNumericId
# ---------------------------------------------------------------------------


class TestResolveIdentifierNumericId:
    def test_resolve_client_by_id(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "clients", "name", "1")
        assert result == 1

    def test_resolve_project_by_id(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "projects", "project_name", "1")
        assert result == 1


# ---------------------------------------------------------------------------
# 2. TestResolveIdentifierByName
# ---------------------------------------------------------------------------


class TestResolveIdentifierByName:
    def test_resolve_client_by_exact_name(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "clients", "name", "Acme Corp")
        assert result == 1

    def test_resolve_case_insensitive(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "clients", "name", "acme corp")
        assert result == 1

    def test_resolve_project_by_name(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        result = resolve_identifier(resolver_db, "projects", "project_name", "Manila")
        assert result == 1


# ---------------------------------------------------------------------------
# 3. TestResolveIdentifierNotFound
# ---------------------------------------------------------------------------


class TestResolveIdentifierNotFound:
    def test_not_found_by_name(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        with pytest.raises(ValueError, match="No .* found"):
            resolve_identifier(resolver_db, "clients", "name", "Nonexistent")

    def test_not_found_by_id(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        with pytest.raises(ValueError, match="No .* found"):
            resolve_identifier(resolver_db, "clients", "name", "999")


# ---------------------------------------------------------------------------
# 4. TestResolveIdentifierAmbiguous
# ---------------------------------------------------------------------------


class TestResolveIdentifierAmbiguous:
    def test_ambiguous_raises_with_match_list(self, resolver_db):
        from footprinter.cli._common import resolve_identifier

        # Insert a second project with the same name
        resolver_db.execute(
            "INSERT INTO projects (project_name, project_type, root_path, status) "
            "VALUES ('Manila', 'node', '/Users/test/Work/manila-2', 'listed')"
        )
        resolver_db.commit()

        with pytest.raises(ValueError, match="Ambiguous") as exc_info:
            resolve_identifier(resolver_db, "projects", "project_name", "Manila")

        # Error message should list both matches
        msg = str(exc_info.value)
        assert "Manila" in msg


# ---------------------------------------------------------------------------
# 5. TestConnectDb
# ---------------------------------------------------------------------------


class TestConnectDb:
    def test_returns_connection_when_db_exists(self, tmp_path):
        from footprinter.cli._common import connect_db
        from footprinter.ingest.database import Database

        db_path = tmp_path / "test.db"
        Database(str(db_path)).conn.close()

        conn = connect_db(db_path)
        assert conn is not None
        assert conn.row_factory == sqlite3.Row

        # Verify PRAGMA was set
        cursor = conn.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        assert timeout == 5000
        conn.close()

    def test_returns_none_when_db_missing(self, tmp_path):
        from footprinter.cli._common import connect_db

        result = connect_db(tmp_path / "nonexistent.db")
        assert result is None


# ---------------------------------------------------------------------------
# 6. TestAddJsonFlag
# ---------------------------------------------------------------------------


class TestAddJsonFlag:
    def test_adds_json_flag_default_false(self):
        from footprinter.cli._common import add_json_flag

        parser = argparse.ArgumentParser()
        add_json_flag(parser)
        args = parser.parse_args([])
        assert args.json is False

    def test_json_flag_true(self):
        from footprinter.cli._common import add_json_flag

        parser = argparse.ArgumentParser()
        add_json_flag(parser)
        args = parser.parse_args(["--json"])
        assert args.json is True


# ---------------------------------------------------------------------------
# 7. TestOutputJson
# ---------------------------------------------------------------------------


class TestOutputJson:
    def test_prints_valid_json(self, capsys):
        from footprinter.cli._common import output_json

        data = {"key": "value", "count": 42}
        output_json(data)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_handles_datetime(self, capsys):
        from footprinter.cli._common import output_json

        data = {"ts": datetime(2026, 1, 1, 12, 0, 0)}
        output_json(data)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "2026" in parsed["ts"]


# ---------------------------------------------------------------------------
# 8. TestConsoleExport
# ---------------------------------------------------------------------------


class TestConsoleExport:
    def test_console_is_rich_instance(self):
        from rich.console import Console

        from footprinter.cli._common import console

        assert isinstance(console, Console)


# ---------------------------------------------------------------------------
# 9. TestValidStatuses
# ---------------------------------------------------------------------------


class TestValidStatuses:
    def test_contains_all_statuses(self):
        from footprinter.cli._common import VALID_STATUSES

        expected = {"listed", "unlisted", "removed"}
        assert VALID_STATUSES == expected

    def test_is_frozenset(self):
        from footprinter.cli._common import VALID_STATUSES

        assert isinstance(VALID_STATUSES, frozenset)


# ---------------------------------------------------------------------------
# 10. TestMakeSlug
# ---------------------------------------------------------------------------


class TestMakeSlug:
    def test_basic_slugification(self):
        from footprinter.utils.text import _make_slug

        assert _make_slug("Acme Corp") == "acme-corp"

    def test_special_chars(self):
        from footprinter.utils.text import _make_slug

        assert _make_slug("Hello, World! @#$") == "hello-world"

    def test_leading_trailing_dashes(self):
        from footprinter.utils.text import _make_slug

        assert _make_slug("---Acme---") == "acme"


# ---------------------------------------------------------------------------
# 11. TestQueriesBackwardCompat
# ---------------------------------------------------------------------------


class TestQueriesSubmoduleImports:
    def test_query_functions_importable_from_submodules(self):
        from footprinter.db.clients import list_clients, update_client
        from footprinter.db.projects import (
            get_project_detail,
            list_project_files,
            list_projects,
            update_project,
        )

        for fn in [
            list_projects,
            list_project_files,
            get_project_detail,
            list_clients,
            update_project,
            update_client,
        ]:
            assert callable(fn)


# ---------------------------------------------------------------------------
# 12. TestNoDeleteProject
# ---------------------------------------------------------------------------


class TestNoDeleteProject:
    def test_db_projects_has_no_delete_project(self):
        import footprinter.db.projects as projects_mod

        assert not hasattr(projects_mod, "delete_project")
