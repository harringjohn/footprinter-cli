"""Tests for access_service — 3-stage access gating + visibility filtering.

Combines tests from former test_read_service.py and test_service_visibility.py.
"""

import pytest

from footprinter.services import Role, access_service
from footprinter.services.access_service import (
    filter_result,
    filter_results_list,
    load_globals,
    resolve_inherit_visibility,
    strip_content_for_denied,
)

# ---------------------------------------------------------------------------
# 3-stage gating (formerly TestReadServiceGating)
# ---------------------------------------------------------------------------


class TestAccessServiceGating:
    """Test the 3-stage gating: existence -> visibility -> permission."""

    def test_visible_file_returns_ok(self, service_db):
        """Visible file with allow permission -> status ok."""
        result = access_service.gate_access(
            service_db,
            "file",
            1,
            role=Role.VIEWER,
        )
        assert result["status"] == "ok"
        assert "metadata" in result

    def test_hidden_file_returns_hidden(self, service_db):
        result = access_service.gate_access(
            service_db,
            "file",
            2,
            role=Role.VIEWER,
        )
        assert result["status"] == "hidden"

    def test_opaque_file_returns_opaque(self, service_db):
        result = access_service.gate_access(
            service_db,
            "file",
            3,
            role=Role.VIEWER,
        )
        assert result["status"] == "opaque"
        assert "metadata" in result

    def test_denied_file_returns_denied(self, service_db):
        """File 3 is opaque, which gates before permission. Create a visible+denied file."""
        service_db.execute(
            """INSERT INTO files (id, name, path, source, status, content_type,
                                  size_bytes, project_id, folder_id, mcp_view, mcp_read)
               VALUES (10, 'denied.md', '/Users/u/Work/denied.md', 'local', 'active',
                       'markdown', 100, 1, 1, 'visible', 'deny')"""
        )
        service_db.commit()
        result = access_service.gate_access(
            service_db,
            "file",
            10,
            role=Role.VIEWER,
        )
        assert result["status"] == "denied"

    def test_not_found_returns_not_found(self, service_db):
        result = access_service.gate_access(
            service_db,
            "file",
            999,
            role=Role.VIEWER,
        )
        assert result["status"] == "not_found"

    def test_invalid_type(self, service_db):
        result = access_service.gate_access(
            service_db,
            "bogus",
            1,
            role=Role.VIEWER,
        )
        assert result["status"] == "invalid_type"

    def test_admin_bypasses_visibility(self, service_db):
        """ADMIN should see hidden files."""
        result = access_service.gate_access(
            service_db,
            "file",
            2,
            role=Role.ADMIN,
        )
        assert result["status"] == "ok"

    def test_admin_bypasses_permission(self, service_db):
        """ADMIN should read denied files."""
        service_db.execute(
            """INSERT INTO files (id, name, path, source, status, content_type,
                                  size_bytes, project_id, folder_id, mcp_view, mcp_read)
               VALUES (11, 'admin.md', '/Users/u/Work/admin.md', 'local', 'active',
                       'markdown', 100, 1, 1, 'visible', 'deny')"""
        )
        service_db.commit()
        result = access_service.gate_access(
            service_db,
            "file",
            11,
            role=Role.ADMIN,
        )
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Email gating (formerly TestReadServiceEmail)
# ---------------------------------------------------------------------------


class TestAccessServiceEmail:
    def test_email_read_returns_content(self, service_db):
        result = access_service.gate_access(
            service_db,
            "email",
            1,
            role=Role.VIEWER,
        )
        assert result["status"] == "ok"
        assert "metadata" in result

    def test_hidden_email_not_found(self, service_db):
        result = access_service.gate_access(
            service_db,
            "email",
            2,
            role=Role.VIEWER,
        )
        assert result["status"] == "hidden"


# ---------------------------------------------------------------------------
# Chat gating (formerly TestReadServiceChat)
# ---------------------------------------------------------------------------


class TestAccessServiceChat:
    def test_chat_read_returns_content(self, service_db):
        result = access_service.gate_access(
            service_db,
            "chat",
            1,
            role=Role.VIEWER,
        )
        assert result["status"] == "ok"
        assert "content" in result
        assert "visible message" in result["content"]
        assert "visible reply" in result["content"]

    def test_chat_summary_in_content_not_metadata(self, service_db):
        """Summary should be in content (gated by mcp_read), not metadata."""
        result = access_service.gate_access(
            service_db,
            "chat",
            1,
            role=Role.VIEWER,
        )
        assert result["status"] == "ok"
        assert "summary" not in result["metadata"]
        assert "Chat about visible topics" in result["content"]

    def test_chat_denied_does_not_expose_summary(self, service_db):
        """Denied chat must not leak summary in opaque metadata."""
        service_db.execute(
            """INSERT INTO chats (id, external_id, account, title, summary,
                                  message_count, mcp_view, mcp_read)
               VALUES (10, 'conv-denied', 'claude', 'Denied Chat',
                       'Secret summary', 0, 'visible', 'deny')"""
        )
        service_db.commit()
        result = access_service.gate_access(
            service_db,
            "chat",
            10,
            role=Role.VIEWER,
        )
        assert result["status"] == "denied"
        assert "summary" not in result.get("metadata", {})

    def test_chat_no_summary_still_works(self, service_db):
        """Chat with NULL summary should return messages without crashing."""
        service_db.execute(
            """INSERT INTO chats (id, external_id, account, title, summary,
                                  message_count, mcp_view, mcp_read)
               VALUES (11, 'conv-nosummary', 'claude', 'No Summary Chat',
                       NULL, 1, 'visible', 'allow')"""
        )
        service_db.execute(
            """INSERT INTO messages (chat_id, role, content)
               VALUES (11, 'user', 'hello there')"""
        )
        service_db.commit()
        result = access_service.gate_access(
            service_db,
            "chat",
            11,
            role=Role.VIEWER,
        )
        assert result["status"] == "ok"
        assert "hello there" in result["content"]
        assert "None" not in result["content"]
        assert "summary" not in result["metadata"]

    def test_hidden_chat_not_found(self, service_db):
        result = access_service.gate_access(
            service_db,
            "chat",
            2,
            role=Role.VIEWER,
        )
        assert result["status"] == "hidden"


# ---------------------------------------------------------------------------
# No inline SQL guard (formerly TestReadServiceNoInlineSQL)
# ---------------------------------------------------------------------------


class TestAccessServiceNoInlineSQL:
    def test_no_raw_sql_in_access_service(self):
        """access_service must not contain inline SQL — all queries route through db/.

        load_globals() has 2 conn.execute calls for global policy lookups —
        these are policy table reads, not entity queries.
        """
        import inspect

        source = inspect.getsource(access_service)
        cursor_count = source.count("cursor.execute")
        conn_count = source.count("conn.execute")
        # load_globals: 2 conn.execute for policy tables (acceptable)
        assert cursor_count == 0, f"Unexpected cursor.execute count: {cursor_count}"
        assert conn_count <= 2, f"Unexpected conn.execute count: {conn_count}"


# ---------------------------------------------------------------------------
# filter_result (formerly test_service_visibility.py)
# ---------------------------------------------------------------------------


def test_filter_result_hidden_returns_none():
    item = {"id": 1, "name": "secret", "mcp_view": "hidden"}
    assert filter_result("file", item) is None


def test_filter_result_opaque_returns_minimal_fields():
    item = {
        "id": 1,
        "name": "secret.py",
        "content_type": "python",
        "source": "local",
        "path": "/full/path",
        "mcp_view": "opaque",
    }
    result = filter_result("file", item)
    assert result is not None
    assert set(result.keys()) == {"id", "content_type", "source"}
    assert result["id"] == 1


def test_filter_result_visible_returns_full():
    item = {
        "id": 1,
        "name": "readme.md",
        "content_type": "markdown",
        "source": "local",
        "path": "/visible/path",
        "mcp_view": "visible",
    }
    result = filter_result("file", item)
    assert result == item


def test_filter_result_opaque_client_fields():
    item = {
        "id": 1,
        "name": "Acme Corp",
        "client_type": "external",
        "status": "active",
        "path_pattern": "~/Work/clients/acme/",
        "mcp_view": "opaque",
    }
    result = filter_result("client", item)
    assert result is not None
    assert set(result.keys()) == {"id", "client_type", "status"}


@pytest.mark.parametrize(
    "item_type, item, expected_keys",
    [
        pytest.param(
            "file",
            {
                "id": 1,
                "name": "report.py",
                "content_type": "python",
                "source": "local",
                "path": "/full/path",
                "project_id": 42,
                "mcp_view": "opaque",
            },
            {"id", "content_type", "source", "project_id"},
            id="file-project_id",
        ),
        pytest.param(
            "email",
            {"id": 2, "subject": "Hello", "account": "work", "project_id": 10, "client_id": 5, "mcp_view": "opaque"},
            {"id", "account", "project_id", "client_id"},
            id="email-project_id+client_id",
        ),
        pytest.param(
            "chat",
            {
                "id": 3,
                "title": "Session",
                "account": "personal",
                "project_id": 10,
                "client_id": 5,
                "mcp_view": "opaque",
            },
            {"id", "account", "project_id", "client_id"},
            id="chat-project_id+client_id",
        ),
        pytest.param(
            "folder",
            {
                "id": 4,
                "path": "/Work/proj",
                "direct_files": 3,
                "direct_file_count": 3,
                "source": "local",
                "project_id": 7,
                "mcp_view": "opaque",
            },
            {"id", "direct_files", "direct_file_count", "source", "project_id"},
            id="folder-project_id",
        ),
        pytest.param(
            "visit",
            {"id": 5, "url": "https://example.com", "browser": "safari", "project_id": 8, "mcp_view": "opaque"},
            {"id", "browser", "project_id"},
            id="visit-project_id",
        ),
        pytest.param(
            "project",
            {
                "id": 6,
                "name": "Footprinter",
                "type": "code",
                "project_type": "personal",
                "status": "active",
                "client_id": 2,
                "mcp_view": "opaque",
            },
            {"id", "type", "project_type", "status", "client_id"},
            id="project-client_id",
        ),
    ],
)
def test_filter_result_opaque_includes_fk_columns(item_type, item, expected_keys):
    result = filter_result(item_type, item)
    assert result is not None
    assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# filter_results_list
# ---------------------------------------------------------------------------


def test_filter_results_list_removes_hidden_counts_suppressed():
    items = [
        {"id": 1, "name": "visible", "mcp_view": "visible"},
        {"id": 2, "name": "hidden", "mcp_view": "hidden"},
        {"id": 3, "name": "opaque", "mcp_view": "opaque", "browser": "safari"},
    ]
    filtered, suppressed = filter_results_list("visit", items)
    assert suppressed == 1
    assert len(filtered) == 2
    assert filtered[0]["id"] == 1
    assert set(filtered[1].keys()) == {"id", "browser"}


# ---------------------------------------------------------------------------
# strip_content_for_denied
# ---------------------------------------------------------------------------


def test_strip_content_for_denied():
    items = [
        {"id": 1, "snippet": "some content", "mcp_read": "allow"},
        {"id": 2, "snippet": "secret stuff", "mcp_read": "deny"},
        {"id": 3, "snippet": "inherit content", "mcp_read": "inherit"},
    ]
    result = strip_content_for_denied("file", items)
    assert "snippet" in result[0]
    assert "snippet" not in result[1]
    assert "snippet" in result[2]


def test_strip_content_for_denied_chat_fields():
    items = [
        {"id": 1, "snippet": "text", "summary": "sum", "mcp_read": "deny"},
    ]
    result = strip_content_for_denied("chat", items)
    assert "snippet" not in result[0]
    assert "summary" not in result[0]


# ---------------------------------------------------------------------------
# resolve_inherit_visibility
# ---------------------------------------------------------------------------


def test_resolve_inherit_uses_global(service_db):
    """With global policy = 'visible', inherit should resolve to 'visible'."""
    load_globals(service_db)
    assert resolve_inherit_visibility("inherit") == "visible"


def test_resolve_inherit_none_returns_opaque():
    """Missing mcp_view (None) should fail-closed to 'opaque'."""
    assert resolve_inherit_visibility(None) == "opaque"


def test_resolve_inherit_explicit_passes_through():
    assert resolve_inherit_visibility("hidden") == "hidden"
    assert resolve_inherit_visibility("visible") == "visible"
    assert resolve_inherit_visibility("opaque") == "opaque"


# ---------------------------------------------------------------------------
# has_global_permission (new public API)
# ---------------------------------------------------------------------------


def test_has_global_permission_returns_bool(service_db):
    """has_global_permission() returns True when global policy is 'allow'."""
    load_globals(service_db)
    assert access_service.has_global_permission() is True


def test_has_global_permission_false_when_none():
    """has_global_permission() returns False when no global policy loaded."""
    import footprinter.services.access_service as _mod

    old = _mod._global_permission
    try:
        _mod._global_permission = None
        assert access_service.has_global_permission() is False
    finally:
        _mod._global_permission = old


# ---------------------------------------------------------------------------
# is_global_policy_loaded (public API for "any policy loaded?")
# ---------------------------------------------------------------------------


def test_is_global_policy_loaded_true(service_db):
    """is_global_policy_loaded() returns True when a global policy exists."""
    load_globals(service_db)
    assert access_service.is_global_policy_loaded() is True


def test_is_global_policy_loaded_false():
    """is_global_policy_loaded() returns False when no global policy loaded."""
    import footprinter.services.access_service as _mod

    old = _mod._global_permission
    try:
        _mod._global_permission = None
        assert access_service.is_global_policy_loaded() is False
    finally:
        _mod._global_permission = old
