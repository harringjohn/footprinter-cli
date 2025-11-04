"""
Tests proving each access control bypass from #163 is fixed.

Each test sets up visibility/permission policies and verifies that the
relevant MCP function respects them.

Dashboard endpoint tests split to test_access_control_dashboard.py.
"""

import sqlite3
from unittest.mock import patch

import pytest
from conftest import populate_access_control_db

from footprinter.services.access_service import (
    OPAQUE_CLIENT_FIELDS,
    OPAQUE_PROJECT_FIELDS,
    _filter_to_opaque,
    filter_results_list,
)
from footprinter.visibility import get_source_visibility, get_visibility

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bypass_db(tmp_path):
    """Database with test data for bypass tests."""
    db_path = tmp_path / "bypass.db"
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    db.conn.close()
    populate_access_control_db(db_path)
    return db_path


# ==============================================================================
# Bypass 3: Chat search (unit-level — semantic indexer requires ChromaDB)
# ==============================================================================


class TestChatSearch:
    """Hidden chats must be excluded from search results."""

    def test_filter_removes_hidden_chats(self, bypass_db):
        # Simulate search results with chat_id and mcp_view from DB
        results = [
            {"chat_id": 1, "title": "Visible Chat", "score": 0.9, "mcp_view": "visible"},
            {"chat_id": 2, "title": "Hidden Chat", "score": 0.8, "mcp_view": "hidden"},
            {"chat_id": 3, "title": "Opaque Chat", "score": 0.7, "mcp_view": "opaque"},
        ]

        filtered, suppressed = filter_results_list("chat", results, id_key="chat_id")

        assert suppressed == 1, "Hidden chat should be suppressed"
        # Visible item keeps chat_id, opaque item loses it (filtered to minimal fields)
        assert len(filtered) == 2, "Should have visible + opaque, not hidden"
        # Verify hidden chat (id=2) is not in the results
        remaining_ids = [r.get("chat_id") for r in filtered]
        assert 2 not in remaining_ids, "Hidden chat should be excluded from search"


# ==============================================================================
# Bypass 4: MCP project entity
# ==============================================================================


class TestProjectEntity:
    """Hidden → NOT_FOUND, opaque → minimal fields only."""

    def test_hidden_project_not_found(self, bypass_db):
        from footprinter.mcp.tools.navigation import footprinter_project

        with patch("footprinter.mcp.db.get_db_path", return_value=bypass_db):
            result = footprinter_project("Hidden Project")
        assert result.get("error") is not None, "Hidden project should return error"

    def test_opaque_project_minimal_fields(self, bypass_db):
        from footprinter.mcp.tools.navigation import footprinter_project

        with patch("footprinter.mcp.db.get_db_path", return_value=bypass_db):
            result = footprinter_project("Opaque Project")
        assert "error" not in result
        # Should only contain opaque fields
        for key in result:
            assert key in OPAQUE_PROJECT_FIELDS, f"Opaque project should not expose '{key}'"

    def test_visible_project_full_fields(self, bypass_db):
        from footprinter.mcp.tools.navigation import footprinter_project

        with patch("footprinter.mcp.db.get_db_path", return_value=bypass_db):
            result = footprinter_project("Visible Project")
        assert "error" not in result
        assert "project_name" in result or "root_path" in result


# ==============================================================================
# Bypass 5: MCP client entity
# ==============================================================================


class TestClientEntity:
    """Hidden → NOT_FOUND, opaque → minimal fields only."""

    def test_hidden_client_not_found(self, bypass_db):
        from footprinter.mcp.tools.navigation import footprinter_client

        with patch("footprinter.mcp.db.get_db_path", return_value=bypass_db):
            result = footprinter_client("Hidden Client")
        assert result.get("error") is not None, "Hidden client should return error"

    def test_opaque_client_minimal_fields(self, bypass_db):
        from footprinter.mcp.tools.navigation import footprinter_client

        with patch("footprinter.mcp.db.get_db_path", return_value=bypass_db):
            result = footprinter_client("Opaque Client")
        assert "error" not in result
        for key in result:
            assert key in OPAQUE_CLIENT_FIELDS, f"Opaque client should not expose '{key}'"

    def test_visible_client_full_fields(self, bypass_db):
        from footprinter.mcp.tools.navigation import footprinter_client

        with patch("footprinter.mcp.db.get_db_path", return_value=bypass_db):
            result = footprinter_client("Visible Client")
        assert "error" not in result
        assert "name" in result


# ==============================================================================
# Bypass 7: Browser search baseline
# ==============================================================================


class TestBrowserSearchBaseline:
    """No policy → opaque (baseline), not full visibility."""

    def test_no_policy_returns_opaque_baseline(self, bypass_db):
        conn = sqlite3.connect(str(bypass_db))
        conn.row_factory = sqlite3.Row

        # No browser visibility policy exists in our test data
        visibility = get_source_visibility(conn, "source:browser")
        conn.close()

        assert visibility == "opaque", "No policy should default to opaque baseline, not full visibility"


# ==============================================================================
# Infrastructure: get_visibility() project/client support
# ==============================================================================


class TestGetVisibilityProjectClient:
    """get_visibility() must resolve project and client types."""

    def test_project_hidden(self, bypass_db):
        conn = sqlite3.connect(str(bypass_db))
        conn.row_factory = sqlite3.Row
        assert get_visibility(conn, "project", 2) == "hidden"
        conn.close()

    def test_project_opaque(self, bypass_db):
        conn = sqlite3.connect(str(bypass_db))
        conn.row_factory = sqlite3.Row
        assert get_visibility(conn, "project", 3) == "opaque"
        conn.close()

    def test_client_hidden(self, bypass_db):
        conn = sqlite3.connect(str(bypass_db))
        conn.row_factory = sqlite3.Row
        assert get_visibility(conn, "client", 2) == "hidden"
        conn.close()

    def test_client_opaque(self, bypass_db):
        conn = sqlite3.connect(str(bypass_db))
        conn.row_factory = sqlite3.Row
        assert get_visibility(conn, "client", 3) == "opaque"
        conn.close()


class TestOpaqueFilterProjectClient:
    """_filter_to_opaque() must handle project and client types."""

    def test_project_opaque_filter(self):
        full = {"id": 1, "project_name": "Secret", "project_type": "python", "status": "active"}
        filtered = _filter_to_opaque("project", full)
        assert set(filtered.keys()) == {"id", "project_type", "status"}

    def test_client_opaque_filter(self):
        full = {"id": 1, "name": "Secret", "client_type": "external", "status": "active"}
        filtered = _filter_to_opaque("client", full)
        assert set(filtered.keys()) == {"id", "client_type", "status"}
