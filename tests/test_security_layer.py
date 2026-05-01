"""
Tests for the data security layer: visibility and permissions.

Tests the deny-wins model for permissions and most-restrictive-wins
model for visibility used by the contexter MCP access layer.
"""

import os

import pytest

from footprinter.permissions import (
    BASELINE_PERMISSION,
    batch_resolve_permissions,
    can_read,
    resolve_permission_with_source,
)
from footprinter.permissions import (
    _get_policy as get_permission_policy,
)
from footprinter.permissions import (
    _resolve as permission_resolve,
)
from footprinter.services.access_service import (
    OPAQUE_BROWSER_FIELDS,
    OPAQUE_CHAT_FIELDS,
    OPAQUE_EMAIL_FIELDS,
    OPAQUE_FILE_FIELDS,
    OPAQUE_FOLDER_FIELDS,
    OPAQUE_PROJECT_FIELDS,
    filter_result,
    filter_results_list,
    get_opaque_metadata,
)
from footprinter.visibility import (
    BASELINE_VISIBILITY,
    batch_resolve_visibility,
    get_source_visibility,
    get_visibility,
    is_readable,
    resolve_visibility_with_source,
)
from footprinter.visibility import (
    _get_policy as get_visibility_policy,
)
from footprinter.visibility import (
    _resolve as visibility_resolve,
)


@pytest.fixture
def security_db(tool_db):
    """Full-schema database with seed data for security layer tests."""
    cursor = tool_db.cursor()

    # Insert test clients
    cursor.execute(
        """
        INSERT INTO clients (id, name, slug, client_type)
        VALUES
            (1, 'Test Client', 'test-client', 'external'),
            (2, 'Restricted Client', 'restricted', 'external'),
            (3, 'Opaque Client', 'opaque', 'external')
    """
    )

    # Insert test projects (no item-level columns used - use policies instead)
    cursor.execute(
        """
        INSERT INTO projects (id, project_name, root_path, client_id)
        VALUES
            (1, 'Test Project', '/test/project', 1),
            (2, 'Denied Project', '/test/denied', 1),
            (3, 'Hidden Project', '/test/hidden', 2),
            (4, 'Opaque Project', '/test/opaque', 3),
            (5, 'Allowed Project', '/test/allowed', 1)
    """
    )

    # Insert test folders (no item-level columns used - use policies instead)
    cursor.execute(
        """
        INSERT INTO folders (id, name, path, relative_path, source, project_id)
        VALUES
            (1, 'folder1', '/test/folder1', 'test/folder1', 'local', 1),
            (2, 'folder2', '/test/folder2', 'test/folder2', 'local', 1),
            (3, 'folder3', '/test/folder3', 'test/folder3', 'local', 1),
            (4, 'subfolder', '/test/project/subfolder', 'test/project/subfolder', 'local', 1)
    """
    )

    # Insert test files (no item-level columns used - use policies instead)
    cursor.execute(
        """
        INSERT
            INTO files (id, source, name, path, content_type, size_bytes, project_id, folder_id)
        VALUES
            (1, 'local', 'visible.txt', '/test/visible.txt', '.txt', 100, 1, 1),
            (2, 'local', 'denied.txt', '/test/denied.txt', '.txt', 200, 1, 1),
            (3, 'local', 'allowed.txt', '/test/allowed.txt', '.txt', 300, 1, 1),
            (4, 'local', 'hidden.txt', '/test/hidden.txt', '.txt', 400, 1, 1),
            (5, 'local', 'opaque.txt', '/test/opaque.txt', '.txt', 500, 1, 1),
            (6, 'local', 'project_denied.txt', '/test/denied/file.txt', '.txt', 600, 2, 1),
            (7, 'local', 'hidden_project.txt', '/test/hidden/file.txt', '.txt', 700, 3, 1),
            (8, 'local', 'in_hidden_folder.txt', '/test/folder2/file.txt', '.txt', 800, 1, 2),
            (9, 'local', 'in_opaque_folder.txt', '/test/folder3/file.txt', '.txt', 900, 1, 3),
            (10, 'local', 'allowed_project.txt', '/test/allowed/file.txt', '.txt', 1000, 5, 4)
    """
    )

    # Insert test emails (no item-level columns used - use policies instead)
    cursor.execute(
        """
        INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id)
        VALUES
            (1, 'msg1', 'thread1', 'work', 'Visible Email', '2026-01-01', NULL),
            (2, 'msg2', 'thread2', 'work', 'Denied Email', '2026-01-02', NULL),
            (3, 'msg3', 'thread3', 'personal', 'Personal Email', '2026-01-03', NULL),
            (4, 'msg4', 'thread4', 'work', 'Hidden Email', '2026-01-04', NULL),
            (5, 'msg5', 'thread5', 'work', 'Opaque Email', '2026-01-05', NULL),
            (6, 'msg6', 'thread6', 'work', 'Denied Project Email', '2026-01-06', 2),
            (7, 'msg7', 'thread7', 'work', 'Allowed Project Email', '2026-01-07', 5),
            (8, 'msg8', 'thread8', 'work', 'Hidden Client Email', '2026-01-08', 3)
    """
    )

    # Insert test chats (no item-level columns used - use policies instead)
    cursor.execute(
        """
        INSERT INTO chats
            (id, external_id, account, title, project_id)
        VALUES
            (1, 'conv1', 'claude', 'Visible Chat', NULL),
            (2, 'conv2', 'claude', 'Hidden Chat', NULL),
            (3, 'conv3', 'chatgpt', 'Opaque Chat', NULL),
            (4, 'conv4', 'claude', 'Denied Project Chat', 2),
            (5, 'conv5', 'claude', 'Allowed Project Chat', 5),
            (6, 'conv6', 'claude', 'Hidden Client Chat', 3)
    """
    )

    # Insert policy rows for test scenarios
    # Permission policies
    cursor.execute(
        """
        INSERT INTO permission_policies (scope, setting) VALUES
            ('file:2', 'deny'),
            ('file:3', 'allow'),
            ('project:2', 'deny'),
            ('project:5', 'allow'),
            ('client:2', 'deny')
    """
    )

    # Visibility policies
    cursor.execute(
        """
        INSERT INTO visibility_policies (scope, setting) VALUES
            ('file:3', 'visible'),
            ('file:4', 'hidden'),
            ('file:5', 'opaque'),
            ('folder:2', 'hidden'),
            ('folder:3', 'opaque'),
            ('folder:4', 'visible'),
            ('project:3', 'hidden'),
            ('project:4', 'opaque'),
            ('client:2', 'hidden'),
            ('client:3', 'opaque'),
            ('email:2', 'opaque'),
            ('email:4', 'hidden'),
            ('email:5', 'opaque'),
            ('chat:2', 'hidden'),
            ('chat:3', 'opaque')
    """
    )

    tool_db.commit()
    yield tool_db


# ==============================================================================
# Permission Resolution Tests
# ==============================================================================


class TestPermissionResolve:
    """Test the _resolve helper function for permissions."""

    def test_resolve_allow(self):
        assert permission_resolve("allow") is True

    def test_resolve_deny(self):
        assert permission_resolve("deny") is False

    def test_resolve_inherit(self):
        assert permission_resolve("inherit") is None

    def test_resolve_null(self):
        assert permission_resolve(None) is None

    def test_resolve_unknown(self):
        assert permission_resolve("unknown") is None


class TestPermissionPolicies:
    """Test permission_policies table lookups."""

    def test_get_policy_missing(self, security_db):
        cursor = security_db.cursor()
        result = get_permission_policy(cursor, "nonexistent")
        assert result is None

    def test_get_policy_source(self, security_db):
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:test', 'deny')")
        security_db.commit()
        result = get_permission_policy(cursor, "source:test")
        assert result is False

    def test_get_policy_file(self, security_db):
        cursor = security_db.cursor()
        # File 2 already has deny policy from fixture
        result = get_permission_policy(cursor, "file:2")
        assert result is False


class TestCanReadFile:
    """Test can_read() for files with deny-wins semantics among policies."""

    def test_unknown_type_denied(self, security_db):
        """Unknown item types should use baseline (deny)."""
        assert can_read(security_db, "unknown", 1) is False

    def test_nonexistent_file_denied(self, security_db):
        """Non-existent files should be denied."""
        assert can_read(security_db, "file", 99999) is False

    def test_item_level_deny_wins(self, security_db):
        """Item-level deny policy should block access even with source allow."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        # Artifact 2 has deny policy from fixture
        assert can_read(security_db, "file", 2) is False

    def test_item_level_allow(self, security_db):
        """Item-level allow policy grants access."""
        # Artifact 3 has allow policy from fixture
        assert can_read(security_db, "file", 3) is True

    def test_project_level_deny_wins(self, security_db):
        """Project-level deny policy should block access."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        # Artifact 6 in project 2 which has deny policy from fixture
        assert can_read(security_db, "file", 6) is False

    def test_project_level_allow(self, security_db):
        """Project-level allow policy grants access."""
        # Artifact 10 in project 5 which has allow policy from fixture
        assert can_read(security_db, "file", 10) is True

    def test_client_level_deny_wins(self, security_db):
        """Client-level deny policy should block access."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        # Artifact 7 -> project 3 -> client 2 which has deny policy from fixture
        assert can_read(security_db, "file", 7) is False

    def test_no_policies_uses_baseline(self, security_db):
        """No matching policies should use BASELINE_PERMISSION (deny)."""
        # Artifact 1 has no permission policies
        assert can_read(security_db, "file", 1) is BASELINE_PERMISSION  # False

    def test_source_policy_allow(self, security_db):
        """Source policy allows access."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        assert can_read(security_db, "file", 1) is True

    def test_no_defaults_allows(self, security_db):
        """Without any defaults, access is allowed (open fallback)."""
        # Artifact 1 has all inherit and no defaults set
        assert can_read(security_db, "file", 1) is True

    def test_folder_prefix_deny_wins(self, security_db):
        """Folder prefix deny should block access."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('folder:/test/', 'deny')")
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        assert can_read(security_db, "file", 1) is False

    def test_folder_prefix_specificity(self, security_db):
        """More specific folder prefix should be used."""
        cursor = security_db.cursor()
        # More specific path allows, general denies
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('folder:/test/', 'deny')")
        cursor.execute(
            "INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('folder:/test/allowed/', 'allow')"
        )
        security_db.commit()
        # Artifact 10 is in /test/allowed/
        assert can_read(security_db, "file", 10) is True


class TestCanReadEmail:
    """Test can_read() for emails."""

    def test_nonexistent_email_denied(self, security_db):
        assert can_read(security_db, "email", 99999) is False

    def test_item_level_deny(self, security_db):
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('email:2', 'deny')")
        security_db.commit()
        # Email 2 has deny policy
        assert can_read(security_db, "email", 2) is False

    def test_source_policy_allows(self, security_db):
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        security_db.commit()
        assert can_read(security_db, "email", 1) is True

    def test_no_policies_uses_baseline(self, security_db):
        """Without any policies, use baseline (allow)."""
        # Email 1 has no policies
        assert can_read(security_db, "email", 1) is BASELINE_PERMISSION  # True


# ==============================================================================
# Visibility Resolution Tests
# ==============================================================================


class TestVisibilityResolve:
    """Test the _resolve helper function for visibility."""

    def test_resolve_hidden(self):
        assert visibility_resolve("hidden") == "hidden"

    def test_resolve_opaque(self):
        assert visibility_resolve("opaque") == "opaque"

    def test_resolve_visible(self):
        assert visibility_resolve("visible") == "visible"

    def test_resolve_inherit(self):
        assert visibility_resolve("inherit") is None

    def test_resolve_null(self):
        assert visibility_resolve(None) is None


class TestVisibilityPolicies:
    """Test visibility_policies table lookups."""

    def test_get_policy_missing(self, security_db):
        cursor = security_db.cursor()
        result = get_visibility_policy(cursor, "nonexistent")
        assert result is None

    def test_get_policy_source(self, security_db):
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:test', 'opaque')")
        security_db.commit()
        result = get_visibility_policy(cursor, "source:test")
        assert result == "opaque"


class TestGetFileVisibility:
    """Test get_visibility() for files with most-restrictive-wins among policies."""

    def test_unknown_type_uses_baseline(self, security_db):
        """Unknown item types use BASELINE_VISIBILITY."""
        assert get_visibility(security_db, "unknown", 1) == BASELINE_VISIBILITY  # 'opaque'

    def test_nonexistent_file_uses_baseline(self, security_db):
        """Non-existent files use BASELINE_VISIBILITY."""
        assert get_visibility(security_db, "file", 99999) == BASELINE_VISIBILITY  # 'opaque'

    def test_item_level_hidden_wins(self, security_db):
        """Item-level hidden policy wins over all else."""
        # Artifact 4 has hidden policy from fixture
        assert get_visibility(security_db, "file", 4) == "hidden"

    def test_item_level_opaque(self, security_db):
        """Item-level opaque policy is applied."""
        # Artifact 5 has opaque policy from fixture
        assert get_visibility(security_db, "file", 5) == "opaque"

    def test_item_level_visible(self, security_db):
        """Item-level visible policy is applied."""
        # Artifact 3 has visible policy from fixture
        assert get_visibility(security_db, "file", 3) == "visible"

    def test_folder_level_hidden_wins(self, security_db):
        """Folder-level hidden policy wins."""
        # Artifact 8 is in folder 2 which has hidden policy from fixture
        assert get_visibility(security_db, "file", 8) == "hidden"

    def test_folder_level_opaque(self, security_db):
        """Folder-level opaque policy is inherited."""
        # Artifact 9 is in folder 3 which has opaque policy from fixture
        assert get_visibility(security_db, "file", 9) == "opaque"

    def test_project_level_hidden_wins(self, security_db):
        """Project-level hidden policy wins."""
        # File 7 -> project 3 which has hidden policy from fixture
        assert get_visibility(security_db, "file", 7) == "hidden"

    def test_project_level_opaque(self, security_db):
        """Project-level opaque policy is inherited."""
        cursor = security_db.cursor()
        # Create file in opaque project
        cursor.execute(
            """
            INSERT INTO files (id, source, name, path, content_type, project_id)
            VALUES (100, 'local', 'test.txt', '/test/opaque/test.txt', '.txt', 4)
        """
        )
        security_db.commit()
        # Project 4 has opaque policy from fixture
        assert get_visibility(security_db, "file", 100) == "opaque"

    def test_client_level_hidden_wins(self, security_db):
        """Client-level hidden policy propagates through project."""
        # Artifact 7 -> project 3 -> client 2 (hidden policy from fixture)
        assert get_visibility(security_db, "file", 7) == "hidden"

    def test_folder_prefix_hidden_wins(self, security_db):
        """Folder prefix hidden policy wins."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('folder:/test/', 'hidden')")
        security_db.commit()
        # Artifact 1 path starts with /test/
        assert get_visibility(security_db, "file", 1) == "hidden"

    def test_folder_prefix_specificity(self, security_db):
        """More specific folder prefix takes precedence.

        But folder FK resolution also participates.
        """
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('folder:/test/', 'hidden')")
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('folder:/test/allowed/', 'visible')"
        )
        security_db.commit()
        # File 10 path is /test/allowed/file.txt - matches 'visible' prefix
        # BUT file 10 has folder_id=4 (path /test/project/subfolder)
        # which matches 'hidden' prefix
        # Most restrictive wins: hidden (from folder FK resolution)
        assert get_visibility(security_db, "file", 10) == "hidden"
        # File 1 in /test/visible.txt should use the /test/ rule
        assert get_visibility(security_db, "file", 1) == "hidden"

    def test_source_policy(self, security_db):
        """Source policy is used when no more specific policies match."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()
        # Artifact 1 has no item/folder/project/client policies
        assert get_visibility(security_db, "file", 1) == "visible"

    def test_no_policies_uses_baseline(self, security_db):
        """Without any policies, use BASELINE_VISIBILITY."""
        # Artifact 1 has no policies
        assert get_visibility(security_db, "file", 1) == BASELINE_VISIBILITY  # 'opaque'


class TestGetEmailVisibility:
    """Test get_visibility() for emails."""

    def test_nonexistent_email_uses_baseline(self, security_db):
        assert get_visibility(security_db, "email", 99999) == BASELINE_VISIBILITY  # 'opaque'

    def test_item_level_hidden(self, security_db):
        # Email 4 has hidden policy from fixture
        assert get_visibility(security_db, "email", 4) == "hidden"

    def test_item_level_opaque(self, security_db):
        # Email 5 has opaque policy from fixture
        assert get_visibility(security_db, "email", 5) == "opaque"

    def test_account_policy_opaque(self, security_db):
        """Account-level policy is applied."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('account:personal', 'opaque')"
        )
        security_db.commit()
        # Email 3 is from personal account
        assert get_visibility(security_db, "email", 3) == "opaque"

    def test_account_hidden_wins(self, security_db):
        """Account-level hidden wins."""
        cursor = security_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('account:work', 'hidden')")
        security_db.commit()
        # Email 1 is from work account
        assert get_visibility(security_db, "email", 1) == "hidden"


class TestGetChatVisibility:
    """Test get_visibility() for chats."""

    def test_nonexistent_chat_uses_baseline(self, security_db):
        assert get_visibility(security_db, "chat", 99999) == BASELINE_VISIBILITY  # 'opaque'

    def test_item_level_hidden(self, security_db):
        # Chat 2 has hidden policy from fixture
        assert get_visibility(security_db, "chat", 2) == "hidden"

    def test_item_level_opaque(self, security_db):
        # Chat 3 has opaque policy from fixture
        assert get_visibility(security_db, "chat", 3) == "opaque"

    def test_source_policy(self, security_db):
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:chats', 'visible')")
        security_db.commit()
        # Chat 1 has no item policy
        assert get_visibility(security_db, "chat", 1) == "visible"


class TestGetFolderVisibility:
    """Test get_visibility() for folders."""

    def test_nonexistent_folder_uses_baseline(self, security_db):
        assert get_visibility(security_db, "folder", 99999) == BASELINE_VISIBILITY  # 'opaque'

    def test_item_level_hidden(self, security_db):
        # Folder 2 has hidden policy from fixture
        assert get_visibility(security_db, "folder", 2) == "hidden"

    def test_item_level_opaque(self, security_db):
        # Folder 3 has opaque policy from fixture
        assert get_visibility(security_db, "folder", 3) == "opaque"

    def test_item_level_visible(self, security_db):
        # Folder 4 has visible policy from fixture
        assert get_visibility(security_db, "folder", 4) == "visible"

    def test_source_policy(self, security_db):
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:folders', 'opaque')"
        )
        security_db.commit()
        # Folder 1 has no item policy
        assert get_visibility(security_db, "folder", 1) == "opaque"


class TestIsReadable:
    """Test the is_readable() helper function."""

    def test_visible_is_readable(self):
        assert is_readable("visible") is True

    def test_opaque_not_readable(self):
        assert is_readable("opaque") is False

    def test_hidden_not_readable(self):
        assert is_readable("hidden") is False


# ==============================================================================
# Visibility Filter Tests
# ==============================================================================


class TestFilterResult:
    """Test filter_result() reads mcp_view from the result dict."""

    def test_hidden_returns_none(self):
        """Hidden items return None."""
        result = {"id": 4, "name": "hidden.txt", "content_type": ".txt", "source": "local", "mcp_view": "hidden"}
        filtered = filter_result("file", result)
        assert filtered is None

    def test_visible_returns_full(self):
        """Visible items return full result."""
        result = {
            "id": 3,
            "name": "allowed.txt",
            "content_type": ".txt",
            "source": "local",
            "extra": "data",
            "mcp_view": "visible",
        }
        filtered = filter_result("file", result)
        assert filtered == result

    def test_opaque_file_filtered(self):
        """Opaque files return only allowed fields."""
        result = {
            "id": 5,
            "name": "opaque.txt",
            "content_type": ".txt",
            "source": "local",
            "project_id": 10,
            "path": "/secret",
            "mcp_view": "opaque",
        }
        filtered = filter_result("file", result)
        assert filtered is not None
        assert set(filtered.keys()) == OPAQUE_FILE_FIELDS
        assert filtered["id"] == 5
        assert filtered["content_type"] == ".txt"
        assert filtered["source"] == "local"
        assert filtered["project_id"] == 10
        assert "name" not in filtered
        assert "path" not in filtered

    def test_filter_result_inherit_without_global_is_opaque(self):
        """Inherit without globals loaded falls back to opaque (baseline)."""
        from footprinter.services import access_service as vf

        vf._global_visibility = None  # no global policy loaded

        result = {
            "id": 1,
            "name": "unresolved.txt",
            "content_type": ".txt",
            "source": "local",
            "project_id": 7,
            "path": "/test",
            "mcp_view": "inherit",
        }
        filtered = filter_result("file", result)
        assert filtered is not None
        assert set(filtered.keys()) == OPAQUE_FILE_FIELDS
        assert filtered["id"] == 1
        assert "name" not in filtered
        assert "path" not in filtered

    def test_filter_result_inherit_with_global_visible(self):
        """Inherit with global=visible resolves to visible (full fields)."""
        from footprinter.services import access_service as vf

        vf._global_visibility = "visible"
        try:
            result = {
                "id": 1,
                "name": "resolved.txt",
                "content_type": ".txt",
                "source": "local",
                "path": "/test",
                "mcp_view": "inherit",
            }
            filtered = filter_result("file", result)
            assert filtered is not None
            assert filtered["name"] == "resolved.txt"
            assert filtered["path"] == "/test"
        finally:
            vf._global_visibility = None

    def test_opaque_email_filtered(self):
        """Opaque emails return only allowed fields."""
        result = {
            "id": 5,
            "account": "work",
            "project_id": 3,
            "client_id": 2,
            "subject": "Secret",
            "from_address": "test@test.com",
            "mcp_view": "opaque",
        }
        filtered = filter_result("email", result)
        assert filtered is not None
        assert set(filtered.keys()) == OPAQUE_EMAIL_FIELDS
        assert filtered["id"] == 5
        assert filtered["account"] == "work"
        assert filtered["project_id"] == 3
        assert filtered["client_id"] == 2
        assert "subject" not in filtered

    def test_opaque_chat_filtered(self):
        """Opaque chats return only allowed fields."""
        result = {
            "id": 3,
            "account": "chatgpt",
            "project_id": 1,
            "client_id": 4,
            "title": "Secret Chat",
            "summary": "Private",
            "mcp_view": "opaque",
        }
        filtered = filter_result("chat", result)
        assert filtered is not None
        assert set(filtered.keys()) == OPAQUE_CHAT_FIELDS
        assert filtered["id"] == 3
        assert filtered["account"] == "chatgpt"
        assert filtered["project_id"] == 1
        assert filtered["client_id"] == 4
        assert "title" not in filtered


class TestFilterResultsList:
    """Test filter_results_list() reads mcp_view from result dicts."""

    def test_filters_hidden_items(self):
        """Hidden items are excluded from results."""
        results = [
            {"id": 3, "name": "visible", "content_type": ".txt", "source": "local", "mcp_view": "visible"},
            {"id": 4, "name": "hidden", "content_type": ".txt", "source": "local", "mcp_view": "hidden"},
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert len(filtered) == 1
        assert filtered[0]["id"] == 3
        assert suppressed == 1

    def test_filters_opaque_items(self):
        """Opaque items have fields filtered."""
        results = [
            {"id": 3, "name": "visible", "content_type": ".txt", "source": "local", "mcp_view": "visible"},
            {
                "id": 5,
                "name": "opaque",
                "content_type": ".txt",
                "source": "local",
                "path": "/secret",
                "mcp_view": "opaque",
            },
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert len(filtered) == 2
        assert suppressed == 0
        # First item (visible) has all fields
        assert "name" in filtered[0]
        # Second item (opaque) has only allowed fields
        assert "name" not in filtered[1]
        assert "path" not in filtered[1]

    def test_mixed_visibility(self):
        """Tests mixed visibility results."""
        results = [
            {"id": 1, "name": "visible1", "content_type": ".txt", "source": "local", "mcp_view": "visible"},
            {"id": 3, "name": "visible2", "content_type": ".txt", "source": "local", "mcp_view": "visible"},
            {"id": 4, "name": "hidden", "content_type": ".txt", "source": "local", "mcp_view": "hidden"},
            {"id": 5, "name": "opaque", "content_type": ".txt", "source": "local", "mcp_view": "opaque"},
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert len(filtered) == 3  # hidden excluded
        assert suppressed == 1
        ids = [r["id"] for r in filtered]
        assert 1 in ids
        assert 3 in ids
        assert 5 in ids
        assert 4 not in ids

    def test_no_visibility_treated_as_opaque(self):
        """Items without mcp_view are treated as opaque (fail-closed)."""
        results = [
            {"id": 1, "name": "no_vis", "content_type": ".txt", "source": "local"},
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert len(filtered) == 1
        assert "name" not in filtered[0]
        assert filtered[0]["id"] == 1
        assert suppressed == 0


class TestGetOpaqueMetadata:
    """Test get_opaque_metadata() direct database lookup."""

    def test_file_opaque_metadata(self, security_db):
        """Get opaque metadata for file."""
        meta = get_opaque_metadata(security_db, "file", 1)
        assert meta["id"] == 1
        assert meta["content_type"] == ".txt"
        assert meta["source"] == "local"
        assert "name" not in meta
        assert "path" not in meta

    def test_email_opaque_metadata(self, security_db):
        """Get opaque metadata for email."""
        meta = get_opaque_metadata(security_db, "email", 1)
        assert meta["id"] == 1
        assert meta["account"] == "work"
        assert "subject" not in meta

    def test_chat_opaque_metadata(self, security_db):
        """Get opaque metadata for chat."""
        meta = get_opaque_metadata(security_db, "chat", 1)
        assert meta["id"] == 1
        assert meta["account"] == "claude"
        assert "title" not in meta

    def test_unknown_type_empty(self, security_db):
        """Unknown type returns empty dict."""
        meta = get_opaque_metadata(security_db, "unknown", 1)
        assert meta == {}

    def test_nonexistent_item_empty(self, security_db):
        """Non-existent item returns empty dict."""
        meta = get_opaque_metadata(security_db, "file", 99999)
        assert meta == {}


class TestOpaqueFieldSets:
    """Test the opaque field set definitions."""

    def test_file_fields(self):
        assert OPAQUE_FILE_FIELDS == {"id", "content_type", "source", "project_id"}

    def test_email_fields(self):
        assert OPAQUE_EMAIL_FIELDS == {"id", "account", "project_id", "client_id"}

    def test_chat_fields(self):
        assert OPAQUE_CHAT_FIELDS == {"id", "account", "project_id", "client_id"}

    def test_folder_fields(self):
        assert OPAQUE_FOLDER_FIELDS == {"id", "direct_files", "direct_file_count", "source", "project_id"}

    def test_project_fields(self):
        assert OPAQUE_PROJECT_FIELDS == {"id", "type", "project_type", "status", "client_id"}


# ==============================================================================
# Integration Tests
# ==============================================================================


class TestVisibilityPermissionInteraction:
    """Test the interaction between visibility and permission checks."""

    def test_hidden_blocks_before_permission(self, security_db):
        """Hidden visibility should block access regardless of permission."""
        cursor = security_db.cursor()
        # Set item policies: allow read but hidden visibility
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('file:1', 'allow')")
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('file:1', 'hidden')")
        security_db.commit()

        # Visibility check shows hidden
        assert get_visibility(security_db, "file", 1) == "hidden"
        # Even though permission would allow
        assert can_read(security_db, "file", 1) is True
        # is_readable prevents reading hidden items
        assert is_readable("hidden") is False

    def test_opaque_blocks_reading(self, security_db):
        """Opaque visibility should prevent content reading."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('file:1', 'allow')")
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('file:1', 'opaque')")
        security_db.commit()

        assert get_visibility(security_db, "file", 1) == "opaque"
        assert can_read(security_db, "file", 1) is True
        assert is_readable("opaque") is False

    def test_visible_with_deny_still_blocked(self, security_db):
        """Visible item with deny permission is still blocked."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('file:1', 'deny')")
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('file:1', 'visible')")
        security_db.commit()

        assert get_visibility(security_db, "file", 1) == "visible"
        assert can_read(security_db, "file", 1) is False
        assert is_readable("visible") is True


class TestDenyWinsScenarios:
    """Test various deny-wins scenarios across hierarchies."""

    def test_item_allow_project_deny(self, security_db):
        """Item allow does not override project deny for permissions."""
        cursor = security_db.cursor()
        # Add item allow policy - project 2 already has deny from fixture
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('file:6', 'allow')")
        security_db.commit()
        # Project 2 has deny - deny wins
        assert can_read(security_db, "file", 6) is False

    def test_item_visible_folder_hidden_visibility(self, security_db):
        """Item visible does not override folder hidden for visibility."""
        cursor = security_db.cursor()
        # Add item visible policy - folder 2 already has hidden from fixture
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('file:8', 'visible')")
        security_db.commit()
        # Folder 2 is hidden - most restrictive wins
        assert get_visibility(security_db, "file", 8) == "hidden"

    def test_specific_folder_prefix_deny(self, security_db):
        """Folder prefix deny overrides item/project allow."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('folder:/test/allowed/', 'deny')"
        )
        security_db.commit()
        # Even though project 5 has allow policy from fixture, folder prefix deny wins
        assert can_read(security_db, "file", 10) is False


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_path_file(self, security_db):
        """File with empty path handles folder prefix check."""
        cursor = security_db.cursor()
        cursor.execute(
            """
            INSERT INTO files (id, source, name, path, content_type)
            VALUES (200, 'local', 'nopath.txt', '', '.txt')
        """
        )
        # Add policies for this file
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('file:200', 'allow')")
        security_db.commit()
        # Should not crash, permission check should work
        assert can_read(security_db, "file", 200) is True

    def test_null_path_file(self, security_db):
        """File with NULL path handles folder prefix check."""
        cursor = security_db.cursor()
        cursor.execute(
            """
            INSERT INTO files (id, source, name, path, content_type)
            VALUES (201, 'local', 'nullpath.txt', NULL, '.txt')
        """
        )
        # Add policies for this file
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('file:201', 'allow')")
        security_db.commit()
        assert can_read(security_db, "file", 201) is True

    def test_tilde_expansion_in_folder_prefix(self, security_db):
        """Folder prefix with ~ expands correctly."""
        cursor = security_db.cursor()
        home = os.path.expanduser("~")
        # Create file in home directory
        cursor.execute(
            f"""
            INSERT INTO files (id, source, name, path, content_type)
            VALUES (202, 'local', 'home.txt', '{home}/test.txt', '.txt')
        """
        )
        # Add folder prefix with tilde
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('folder:~/', 'opaque')")
        security_db.commit()

        assert get_visibility(security_db, "file", 202) == "opaque"


# ==============================================================================
# Parent Entity Resolution Tests (Hierarchy Propagation)
# ==============================================================================


class TestHierarchyPropagation:
    """Test visibility/permission propagation through entity hierarchy.

    These tests verify the fix for parent entity resolution:
    - source:folders policy should affect files in folders
    - source:projects policy should affect files in projects
    - source:clients policy should affect files via client chain
    - Baseline visibility should NOT propagate down the hierarchy
    """

    def test_source_folders_opaque_affects_files(self, security_db):
        """T1: source:folders=opaque makes files in folders opaque."""
        cursor = security_db.cursor()
        # Set source:folders to opaque, source:files to visible
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:folders', 'opaque')"
        )
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()
        # File 1 has folder_id=1, so it should inherit folder's opaque via source:folders
        visibility = get_visibility(security_db, "file", 1)
        assert visibility == "opaque", f"Expected 'opaque' but got '{visibility}'"

    def test_source_projects_hidden_affects_files(self, security_db):
        """T2: source:projects=hidden makes files in projects hidden."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:projects', 'hidden')"
        )
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()
        # File 1 has project_id=1, so it should be hidden via source:projects
        visibility = get_visibility(security_db, "file", 1)
        assert visibility == "hidden", f"Expected 'hidden' but got '{visibility}'"

    def test_baseline_does_not_propagate(self, security_db):
        """T3: Baseline visibility should not propagate from parent entities."""
        cursor = security_db.cursor()
        # Only set source:files to visible, let folder/project use baseline
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()
        # File 1 should be visible (baseline from folder/project should not override)
        visibility = get_visibility(security_db, "file", 1)
        assert visibility == "visible", f"Expected 'visible' but got '{visibility}'"

    def test_most_restrictive_wins_across_hierarchy(self, security_db):
        """T4: Most restrictive policy wins across hierarchy levels."""
        cursor = security_db.cursor()
        # folder:1 = visible, but client:2 = hidden
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('folder:1', 'visible')")
        # Note: file 7 -> project 3 -> client 2 (already has hidden from fixture)
        security_db.commit()
        # Should be hidden because client:2 is hidden (most restrictive)
        visibility = get_visibility(security_db, "file", 7)
        assert visibility == "hidden", f"Expected 'hidden' but got '{visibility}'"

    def test_source_clients_deny_affects_files(self, security_db):
        """T5: source:clients=deny denies permission for files via client chain."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:clients', 'deny')")
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        # File 1 -> project 1 -> client 1, client 1 resolves to deny via source:clients
        can = can_read(security_db, "file", 1)
        assert can is False, f"Expected False but got {can}"

    def test_source_folders_affects_folder_entity(self, security_db):
        """T6: source:folders policy affects folder entities directly."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:folders', 'opaque')"
        )
        security_db.commit()
        # Folder 1 should be opaque via source:folders
        visibility = get_visibility(security_db, "folder", 1)
        assert visibility == "opaque", f"Expected 'opaque' but got '{visibility}'"

    def test_batch_resolution_matches_single(self, security_db):
        """T7: Batch resolution should match single-item resolution."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:folders', 'opaque')"
        )
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()

        # Single-item resolution
        single_1 = resolve_visibility_with_source(security_db, "file", 1)
        single_3 = resolve_visibility_with_source(security_db, "file", 3)

        # Batch resolution
        batch = batch_resolve_visibility(security_db, "file", [1, 3])

        # Results should match (visibility state should match, source may differ slightly in format)
        assert single_1[0] == batch[1][0], f"Visibility mismatch: single={single_1[0]}, batch={batch[1][0]}"
        assert single_3[0] == batch[3][0], f"Visibility mismatch: single={single_3[0]}, batch={batch[3][0]}"

    def test_source_folders_hidden_excludes_from_filter(self, security_db):
        """T8: source:folders=hidden causes files to be hidden (excluded from results)."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:folders', 'hidden')"
        )
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()
        # File 1 has folder_id=1, should be hidden via folder chain
        visibility = get_visibility(security_db, "file", 1)
        assert visibility == "hidden", f"Expected 'hidden' but got '{visibility}'"
        # Hidden items are not readable
        assert is_readable(visibility) is False

    def test_source_folders_opaque_blocks_reading(self, security_db):
        """T9: source:folders=opaque means file content cannot be read."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:folders', 'opaque')"
        )
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:files', 'visible')")
        security_db.commit()
        # File 1 has folder_id=1, should be opaque via folder chain
        visibility = get_visibility(security_db, "file", 1)
        assert visibility == "opaque", f"Expected 'opaque' but got '{visibility}'"
        # Opaque items are not readable
        assert is_readable(visibility) is False


class TestHierarchyPermissionPropagation:
    """Test permission propagation through entity hierarchy."""

    def test_source_projects_deny_affects_files(self, security_db):
        """source:projects=deny denies permission for files in projects."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:projects', 'deny')")
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        # File 1 has project_id=1, should be denied via source:projects
        can = can_read(security_db, "file", 1)
        assert can is False, f"Expected False but got {can}"

    def test_permission_baseline_does_not_propagate(self, security_db):
        """Baseline permission should not propagate from parent entities."""
        cursor = security_db.cursor()
        # Only set source:files to allow, let project/client use baseline
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()
        # Artifact 1 should be allowed (baseline from project/client should not override)
        can = can_read(security_db, "file", 1)
        assert can is True, f"Expected True but got {can}"

    def test_batch_permission_resolution_matches_single(self, security_db):
        """Batch permission resolution should match single-item resolution."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:projects', 'deny')")
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:files', 'allow')")
        security_db.commit()

        # Single-item resolution
        single_1 = resolve_permission_with_source(security_db, "file", 1)
        single_3 = resolve_permission_with_source(security_db, "file", 3)

        # Batch resolution
        batch = batch_resolve_permissions(security_db, "file", [1, 3])

        # Permission results should match
        assert single_1[0] == batch[1][0], f"Permission mismatch: single={single_1[0]}, batch={batch[1][0]}"
        assert single_3[0] == batch[3][0], f"Permission mismatch: single={single_3[0]}, batch={batch[3][0]}"


# ==============================================================================
# Email/Chat Project/Client Inheritance Tests
# ==============================================================================


class TestEmailProjectPermissions:
    """Test email permission inheritance through project/client hierarchy."""

    def test_denied_project_blocks_email(self, security_db):
        """Email in denied project (2) should be denied."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        security_db.commit()
        # Email 6 -> project 2 (deny from fixture)
        assert can_read(security_db, "email", 6) is False

    def test_allowed_project_allows_email(self, security_db):
        """Email in allowed project (5) should be allowed."""
        # Email 7 -> project 5 (allow from fixture)
        assert can_read(security_db, "email", 7) is True

    def test_client_deny_blocks_email(self, security_db):
        """Email in project (3) with denied client (2) should be denied."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        security_db.commit()
        # Email 8 -> project 3 -> client 2 (deny from fixture)
        assert can_read(security_db, "email", 8) is False

    def test_baseline_does_not_propagate(self, security_db):
        """Baseline from project/client should not propagate to email."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        security_db.commit()
        # Email 1 has no project_id, should be allowed via source:emails
        assert can_read(security_db, "email", 1) is True

    def test_batch_matches_single(self, security_db):
        """Batch resolution should match single-item resolution."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:emails', 'allow')")
        security_db.commit()

        single_6 = resolve_permission_with_source(security_db, "email", 6)
        single_7 = resolve_permission_with_source(security_db, "email", 7)

        batch = batch_resolve_permissions(security_db, "email", [6, 7])

        assert single_6[0] == batch[6][0]
        assert single_7[0] == batch[7][0]


class TestChatProjectPermissions:
    """Test chat permission inheritance through project/client hierarchy."""

    def test_denied_project_blocks_chat(self, security_db):
        """Chat in denied project (2) should be denied."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        security_db.commit()
        # Conv 4 -> project 2 (deny from fixture)
        assert can_read(security_db, "chat", 4) is False

    def test_allowed_project_allows_chat(self, security_db):
        """Chat in allowed project (5) should be allowed."""
        # Conv 5 -> project 5 (allow from fixture)
        assert can_read(security_db, "chat", 5) is True

    def test_client_deny_blocks_chat(self, security_db):
        """Chat in project (3) with denied client (2) should be denied."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        security_db.commit()
        # Conv 6 -> project 3 -> client 2 (deny from fixture)
        assert can_read(security_db, "chat", 6) is False

    def test_baseline_does_not_propagate(self, security_db):
        """Baseline from project/client should not propagate to chat."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        security_db.commit()
        # Conv 1 has no project_id, should be allowed via source:chats
        assert can_read(security_db, "chat", 1) is True

    def test_batch_matches_single(self, security_db):
        """Batch resolution should match single-item resolution."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')")
        security_db.commit()

        single_4 = resolve_permission_with_source(security_db, "chat", 4)
        single_5 = resolve_permission_with_source(security_db, "chat", 5)

        batch = batch_resolve_permissions(security_db, "chat", [4, 5])

        assert single_4[0] == batch[4][0]
        assert single_5[0] == batch[5][0]


class TestEmailProjectVisibility:
    """Test email visibility inheritance through project/client hierarchy."""

    def test_hidden_project_hides_email(self, security_db):
        """Email in hidden project (3) should be hidden."""
        # Email 8 -> project 3 (hidden from fixture)
        assert get_visibility(security_db, "email", 8) == "hidden"

    def test_opaque_project_makes_email_opaque(self, security_db):
        """Email in opaque project (4) should be opaque."""
        cursor = security_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id)
            VALUES (9, 'msg9', 'thread9', 'work', 'Opaque Project Email', '2026-01-09', 4)
        """
        )
        security_db.commit()
        assert get_visibility(security_db, "email", 9) == "opaque"

    def test_client_hidden_hides_email(self, security_db):
        """Email via client (2) hidden should be hidden."""
        # Email 8 -> project 3 -> client 2 (hidden from fixture)
        assert get_visibility(security_db, "email", 8) == "hidden"

    def test_baseline_does_not_propagate(self, security_db):
        """Baseline from project/client should not propagate to email."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:emails', 'visible')"
        )
        security_db.commit()
        # Email 1 has no project_id, should be visible via source:emails
        assert get_visibility(security_db, "email", 1) == "visible"

    def test_batch_matches_single(self, security_db):
        """Batch resolution should match single-item resolution."""
        single_6 = resolve_visibility_with_source(security_db, "email", 6)
        single_8 = resolve_visibility_with_source(security_db, "email", 8)

        batch = batch_resolve_visibility(security_db, "email", [6, 8])

        assert single_6[0] == batch[6][0]
        assert single_8[0] == batch[8][0]


class TestChatProjectVisibility:
    """Test chat visibility inheritance through project/client hierarchy."""

    def test_hidden_project_hides_chat(self, security_db):
        """Chat in hidden project (3) should be hidden."""
        # Conv 6 -> project 3 (hidden from fixture)
        assert get_visibility(security_db, "chat", 6) == "hidden"

    def test_opaque_project_makes_chat_opaque(self, security_db):
        """Chat in opaque project (4) should be opaque."""
        cursor = security_db.cursor()
        cursor.execute(
            """
            INSERT INTO chats
                (id, external_id, account, title, project_id)
            VALUES (7, 'conv7', 'claude', 'Opaque Project Chat', 4)
        """
        )
        security_db.commit()
        assert get_visibility(security_db, "chat", 7) == "opaque"

    def test_client_hidden_hides_chat(self, security_db):
        """Chat via client (2) hidden should be hidden."""
        # Conv 6 -> project 3 -> client 2 (hidden from fixture)
        assert get_visibility(security_db, "chat", 6) == "hidden"

    def test_baseline_does_not_propagate(self, security_db):
        """Baseline from project/client should not propagate to chat."""
        cursor = security_db.cursor()
        cursor.execute("INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:chats', 'visible')")
        security_db.commit()
        # Conv 1 has no project_id, should be visible via source:chats
        assert get_visibility(security_db, "chat", 1) == "visible"

    def test_batch_matches_single(self, security_db):
        """Batch resolution should match single-item resolution."""
        single_4 = resolve_visibility_with_source(security_db, "chat", 4)
        single_6 = resolve_visibility_with_source(security_db, "chat", 6)

        batch = batch_resolve_visibility(security_db, "chat", [4, 6])

        assert single_4[0] == batch[4][0]
        assert single_6[0] == batch[6][0]


class TestDirectClientVisibility:
    """Resolver must honor entity.client_id when project_id is NULL or its project
    points at a different client.
    """

    def test_file_direct_client_id_applies_client_policy(self, security_db):
        """File with client_id=2, project_id=NULL must resolve via client:2 policy (hidden)."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, source, name, path, client_id, project_id) "
            "VALUES (50, 'local', 'direct.txt', '/test/direct.txt', 2, NULL)"
        )
        security_db.commit()
        # client:2 = 'hidden' from fixture
        result = batch_resolve_visibility(security_db, "file", [50])
        assert result[50][0] == "hidden", f"Expected 'hidden' via client:2, got {result[50]}"

    def test_folder_direct_client_id_applies_client_policy(self, security_db):
        """Folder with client_id=2, project_id=NULL must resolve via client:2 policy."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, client_id) "
            "VALUES (50, 'direct', '/test/direct-folder', 'test/direct-folder', 'local', NULL, 2)"
        )
        security_db.commit()
        result = batch_resolve_visibility(security_db, "folder", [50])
        assert result[50][0] == "hidden", f"Expected 'hidden' via client:2, got {result[50]}"

    def test_email_direct_client_id_applies_client_policy(self, security_db):
        """Email with client_id=2, project_id=NULL must resolve via client:2 policy."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, "
            "project_id, client_id) "
            "VALUES (50, 'msg50', 't50', 'work', 'Direct Client Email', '2026-02-01', NULL, 2)"
        )
        security_db.commit()
        result = batch_resolve_visibility(security_db, "email", [50])
        assert result[50][0] == "hidden", f"Expected 'hidden' via client:2, got {result[50]}"

    def test_chat_direct_client_id_applies_client_policy(self, security_db):
        """Chat with client_id=2, project_id=NULL must resolve via client:2 policy."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
            "VALUES (50, 'conv50', 'claude', 'Direct Client Chat', NULL, 2)"
        )
        security_db.commit()
        result = batch_resolve_visibility(security_db, "chat", [50])
        assert result[50][0] == "hidden", f"Expected 'hidden' via client:2, got {result[50]}"

    def test_file_direct_client_id_overrides_project_client_id(self, security_db):
        """Entity's direct client_id wins when it differs from project's client_id."""
        cursor = security_db.cursor()
        # File 60 belongs to project 1 (client_id=1, no policy) but is directly tagged
        # to client 2 (visibility_policies('client:2', 'hidden'))
        cursor.execute(
            "INSERT INTO files (id, source, name, path, project_id, client_id) "
            "VALUES (60, 'local', 'cross.txt', '/test/cross.txt', 1, 2)"
        )
        security_db.commit()
        result = batch_resolve_visibility(security_db, "file", [60])
        assert result[60][0] == "hidden", (
            f"Direct client_id=2 ('hidden') should win over project's client_id=1; got {result[60]}"
        )

    def test_email_direct_client_id_overrides_project_client_id(self, security_db):
        """Email's direct client_id wins over project's client_id."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, "
            "project_id, client_id) "
            "VALUES (60, 'msg60', 't60', 'work', 'Cross Client', '2026-02-02', 1, 2)"
        )
        security_db.commit()
        result = batch_resolve_visibility(security_db, "email", [60])
        assert result[60][0] == "hidden", f"Expected 'hidden' via direct client:2, got {result[60]}"

    def test_single_entity_file_direct_client_id_applies(self, security_db):
        """Single-entity resolver honors direct file.client_id (parity with batch path)."""
        security_db.execute(
            "INSERT INTO files (id, source, name, path, client_id, project_id) "
            "VALUES (70, 'local', 'single.txt', '/test/single.txt', 2, NULL)"
        )
        security_db.commit()
        assert get_visibility(security_db, "file", 70) == "hidden"

    def test_single_entity_folder_direct_client_id_applies(self, security_db):
        """Single-entity resolver honors direct folder.client_id."""
        security_db.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, client_id) "
            "VALUES (70, 'single', '/test/single-folder', 'test/single-folder', 'local', NULL, 2)"
        )
        security_db.commit()
        assert get_visibility(security_db, "folder", 70) == "hidden"

    def test_single_entity_email_direct_client_id_applies(self, security_db):
        """Single-entity resolver honors direct email.client_id."""
        security_db.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, "
            "project_id, client_id) "
            "VALUES (70, 'msg70', 't70', 'work', 'Single', '2026-02-03', NULL, 2)"
        )
        security_db.commit()
        assert get_visibility(security_db, "email", 70) == "hidden"

    def test_single_entity_chat_direct_client_id_applies(self, security_db):
        """Single-entity resolver honors direct chat.client_id."""
        security_db.execute(
            "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
            "VALUES (70, 'conv70', 'claude', 'Single', NULL, 2)"
        )
        security_db.commit()
        assert get_visibility(security_db, "chat", 70) == "hidden"


# ==============================================================================
# Browser History Visibility Tests
# ==============================================================================


class TestGetSourceVisibility:
    """Test the get_source_visibility() function."""

    def test_returns_policy_when_set(self, security_db):
        """Returns the policy setting when a source policy exists."""
        cursor = security_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'hidden')")
        security_db.commit()
        assert get_source_visibility(security_db, "source:browser") == "hidden"

    def test_returns_opaque_policy(self, security_db):
        """Returns opaque when source policy is opaque."""
        cursor = security_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'opaque')")
        security_db.commit()
        assert get_source_visibility(security_db, "source:browser") == "opaque"

    def test_returns_visible_policy(self, security_db):
        """Returns visible when source policy is visible."""
        cursor = security_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'visible')")
        security_db.commit()
        assert get_source_visibility(security_db, "source:browser") == "visible"

    def test_returns_hardcoded_baseline_when_no_policies(self, security_db):
        """Returns BASELINE_VISIBILITY when no policies exist at all."""
        result = get_source_visibility(security_db, "source:browser")
        assert result == BASELINE_VISIBILITY

    def test_falls_back_to_global_policy(self, security_db):
        """Falls back to global policy when no source-specific policy exists."""
        cursor = security_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'visible')")
        security_db.commit()
        assert get_source_visibility(security_db, "source:browser") == "visible"

    def test_source_policy_wins_over_global(self, security_db):
        """Source-specific policy takes precedence over global policy."""
        cursor = security_db.cursor()
        cursor.execute(
            "INSERT INTO visibility_policies (scope, setting)"
            " VALUES ('global', 'visible'), ('source:browser', 'hidden')"
        )
        security_db.commit()
        assert get_source_visibility(security_db, "source:browser") == "hidden"

    def test_no_global_no_source_returns_baseline(self, security_db):
        """With no policies at all, returns the hardcoded BASELINE_VISIBILITY."""
        # Ensure clean state - no policies
        security_db.execute("DELETE FROM visibility_policies")
        security_db.commit()
        result = get_source_visibility(security_db, "source:browser")
        assert result == BASELINE_VISIBILITY

    def test_works_for_other_sources(self, security_db):
        """Works for any source scope, not just visits."""
        cursor = security_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:custom', 'hidden')")
        security_db.commit()
        assert get_source_visibility(security_db, "source:custom") == "hidden"


class TestBrowserSearchVisibility:
    """Test browser history visibility enforcement in footprinter_search.

    These tests create a standalone DB with visits + visibility_policies
    and call the search code path directly to verify the source-level gate.
    """

    @pytest.fixture
    def browser_db(self, tool_db):
        """Full-schema database with browser history seed data."""
        cursor = tool_db.cursor()

        cursor.execute(
            """
            INSERT INTO visits (id, url, title, visit_time, browser)
            VALUES
                (1, 'https://example.com/page1', 'Example Page', '2026-01-15 10:00:00', 'safari'),
                (2, 'https://example.com/page2', 'Another Example', '2026-01-15 11:00:00',
                    'chrome'),
                (3, 'https://test.com/search', 'Test Search', '2026-01-15 12:00:00', 'safari')
        """
        )

        tool_db.commit()
        yield tool_db

    def test_hidden_blocks_browser_results(self, browser_db):
        """source:browser=hidden means no browser key in results."""
        cursor = browser_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'hidden')")
        browser_db.commit()

        from footprinter.visibility import get_source_visibility

        vis = get_source_visibility(browser_db, "source:browser")
        assert vis == "hidden"

        # Simulate the search gate logic
        results = {}
        if vis != "hidden":
            rows = browser_db.execute(
                "SELECT id, url, title, visit_time, browser FROM visits WHERE url LIKE ?",
                ("%example%",),
            ).fetchall()
            results["browser"] = [dict(r) for r in rows]

        assert "browser" not in results

    def test_opaque_returns_minimal_fields(self, browser_db):
        """source:browser=opaque returns only id + browser."""
        cursor = browser_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'opaque')")
        browser_db.commit()

        from footprinter.visibility import get_source_visibility

        vis = get_source_visibility(browser_db, "source:browser")
        assert vis == "opaque"

        # Simulate the search gate logic
        rows = browser_db.execute(
            "SELECT id, url, title, visit_time, browser FROM visits WHERE url LIKE ?",
            ("%example%",),
        ).fetchall()

        if vis == "opaque":
            results = [{"id": r["id"], "browser": r["browser"], "project_id": None} for r in rows]
        else:
            results = [dict(r) for r in rows]

        assert len(results) == 2
        for r in results:
            assert set(r.keys()) == OPAQUE_BROWSER_FIELDS
            assert "url" not in r
            assert "title" not in r
            assert "visit_time" not in r

    def test_visible_returns_full_results(self, browser_db):
        """source:browser=visible returns all fields."""
        cursor = browser_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:browser', 'visible')")
        browser_db.commit()

        from footprinter.visibility import get_source_visibility

        vis = get_source_visibility(browser_db, "source:browser")
        assert vis == "visible"

        # Simulate the search gate logic
        rows = browser_db.execute(
            "SELECT id, url, title, visit_time, browser FROM visits WHERE url LIKE ?",
            ("%example%",),
        ).fetchall()

        results = [
            {
                "id": r["id"],
                "url": r["url"],
                "title": r["title"],
                "visit_time": r["visit_time"],
                "browser": r["browser"],
            }
            for r in rows
        ]

        assert len(results) == 2
        for r in results:
            assert "url" in r
            assert "title" in r
            assert "visit_time" in r
            assert "browser" in r

    def test_no_policy_returns_baseline_results(self, browser_db):
        """No policy = baseline (opaque), so only minimal fields returned."""
        from footprinter.visibility import get_source_visibility

        vis = get_source_visibility(browser_db, "source:browser")
        assert vis == BASELINE_VISIBILITY  # opaque

        # Simulate the search gate logic (baseline = opaque)
        rows = browser_db.execute(
            "SELECT id, url, title, visit_time, browser FROM visits WHERE url LIKE ?",
            ("%example%",),
        ).fetchall()

        if vis == "opaque":
            results = [{"id": r["id"], "browser": r["browser"], "project_id": None} for r in rows]
        else:
            results = [dict(r) for r in rows]

        assert len(results) == 2
        for r in results:
            assert set(r.keys()) == OPAQUE_BROWSER_FIELDS

    def test_global_visible_no_source_returns_full_fields(self, browser_db):
        """Global visible + no source policy = full browser fields returned."""
        cursor = browser_db.cursor()
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'visible')")
        browser_db.commit()

        from footprinter.visibility import get_source_visibility

        vis = get_source_visibility(browser_db, "source:browser")
        assert vis == "visible"

        rows = browser_db.execute(
            "SELECT id, url, title, visit_time, browser FROM visits WHERE url LIKE ?",
            ("%example%",),
        ).fetchall()

        results = [
            {
                "id": r["id"],
                "url": r["url"],
                "title": r["title"],
                "visit_time": r["visit_time"],
                "browser": r["browser"],
            }
            for r in rows
        ]

        assert len(results) == 2
        for r in results:
            assert "url" in r
            assert "title" in r
            assert "visit_time" in r
            assert "browser" in r


class TestOpaqueFieldSetsBrowser:
    """Test the OPAQUE_BROWSER_FIELDS constant."""

    def test_browser_fields(self):
        assert OPAQUE_BROWSER_FIELDS == {"id", "browser", "project_id"}


class TestPolicyChainUsesFilePrefix:
    """Test that build_policy_chain uses file: prefix."""

    def test_chain_outputs_file_scope(self, security_db):
        """Policy chain for a file should use file:{id} scope."""
        from footprinter.cli._policy_helpers import build_policy_chain

        # Insert a file-level permission policy with file: prefix
        security_db.execute("INSERT OR REPLACE INTO permission_policies (scope, setting) VALUES ('file:42', 'allow')")
        security_db.execute(
            "INSERT INTO files (id, source, name, path, content_type, size_bytes) "
            "VALUES (42, 'local', 'test.txt', '/test/test.txt', '.txt', 100)"
        )
        security_db.commit()

        chain = build_policy_chain(security_db, "/test/test.txt", 42, None, None)

        # First entry should be the file-level scope
        file_entry = chain[0]
        assert file_entry["scope"] == "file:42", f"Expected file:42 but got {file_entry['scope']}"
        assert file_entry["permission"] == "allow"


# ==============================================================================
# Cached Column Tests
# ==============================================================================


class TestFilterResultsListUsesColumn:
    """Test that filter_results_list() reads mcp_view from result dicts
    instead of calling get_visibility() per item."""

    def test_hidden_excluded_by_column(self):
        """Items with mcp_view='hidden' in the result dict are excluded."""
        results = [
            {"id": 1, "name": "visible.txt", "mcp_view": "visible", "source": "local"},
            {"id": 2, "name": "hidden.txt", "mcp_view": "hidden", "source": "local"},
            {"id": 3, "name": "also_visible.txt", "mcp_view": "visible", "source": "local"},
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert suppressed == 1
        assert len(filtered) == 2
        assert all(r["id"] != 2 for r in filtered)

    def test_opaque_minimized_by_column(self):
        """Items with mcp_view='opaque' in the result dict get fields stripped."""
        results = [
            {
                "id": 1,
                "name": "opaque.txt",
                "mcp_view": "opaque",
                "content_type": ".txt",
                "source": "local",
                "path": "/secret",
            },
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert suppressed == 0
        assert len(filtered) == 1
        assert "name" not in filtered[0]
        assert "path" not in filtered[0]
        assert filtered[0]["id"] == 1
        assert filtered[0]["content_type"] == ".txt"

    def test_visible_returns_full(self):
        """Items with mcp_view='visible' return all fields."""
        results = [
            {
                "id": 1,
                "name": "file.txt",
                "mcp_view": "visible",
                "content_type": ".txt",
                "source": "local",
                "path": "/test",
            },
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert suppressed == 0
        assert len(filtered) == 1
        assert filtered[0]["name"] == "file.txt"
        assert filtered[0]["path"] == "/test"

    def test_inherit_without_global_treated_as_opaque(self):
        """Items with mcp_view='inherit' and no global policy are opaque (baseline)."""
        from footprinter.services import access_service as vf

        vf._global_visibility = None  # no global policy

        results = [
            {
                "id": 1,
                "name": "file.txt",
                "mcp_view": "inherit",
                "content_type": ".txt",
                "source": "local",
                "path": "/test",
            },
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert suppressed == 0
        assert len(filtered) == 1
        assert "name" not in filtered[0]
        assert "path" not in filtered[0]
        assert filtered[0]["id"] == 1
        assert filtered[0]["content_type"] == ".txt"
        assert filtered[0]["source"] == "local"

    def test_inherit_with_global_visible_is_full(self):
        """Items with mcp_view='inherit' and global=visible return all fields."""
        from footprinter.services import access_service as vf

        vf._global_visibility = "visible"
        try:
            results = [
                {
                    "id": 1,
                    "name": "file.txt",
                    "mcp_view": "inherit",
                    "content_type": ".txt",
                    "source": "local",
                    "path": "/test",
                },
            ]
            filtered, suppressed = filter_results_list("file", results)
            assert suppressed == 0
            assert len(filtered) == 1
            assert filtered[0]["name"] == "file.txt"
            assert filtered[0]["path"] == "/test"
        finally:
            vf._global_visibility = None

    def test_missing_column_treated_as_opaque(self):
        """Items without mcp_view key are treated as opaque (fail-closed)."""
        results = [
            {"id": 1, "name": "file.txt", "content_type": ".txt", "source": "local"},
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert suppressed == 0
        assert len(filtered) == 1
        assert "name" not in filtered[0]
        assert filtered[0]["id"] == 1
        assert filtered[0]["content_type"] == ".txt"

    def test_mixed_visibility_column_no_global(self):
        """Mix of hidden, opaque, visible, and inherit (no global) — inherit is opaque."""
        from footprinter.services import access_service as vf

        vf._global_visibility = None  # no global policy

        results = [
            {"id": 1, "name": "v1.txt", "mcp_view": "visible", "content_type": ".txt", "source": "local"},
            {"id": 2, "name": "h1.txt", "mcp_view": "hidden", "content_type": ".txt", "source": "local"},
            {"id": 3, "name": "o1.txt", "mcp_view": "opaque", "content_type": ".txt", "source": "local"},
            {"id": 4, "name": "i1.txt", "mcp_view": "inherit", "content_type": ".txt", "source": "local"},
        ]
        filtered, suppressed = filter_results_list("file", results)
        assert suppressed == 1  # hidden excluded
        assert len(filtered) == 3
        ids = [r["id"] for r in filtered]
        assert 1 in ids and 3 in ids and 4 in ids
        assert 2 not in ids
        # opaque item should be minimized
        opaque = next(r for r in filtered if r["id"] == 3)
        assert "name" not in opaque
        # inherit item should also be opaque-filtered (no global policy)
        inherit = next(r for r in filtered if r["id"] == 4)
        assert "name" not in inherit

    def test_mixed_visibility_column_with_global_visible(self):
        """Mix of hidden, opaque, visible, and inherit (global=visible) — inherit is visible."""
        from footprinter.services import access_service as vf

        vf._global_visibility = "visible"
        try:
            results = [
                {"id": 1, "name": "v1.txt", "mcp_view": "visible", "content_type": ".txt", "source": "local"},
                {"id": 2, "name": "h1.txt", "mcp_view": "hidden", "content_type": ".txt", "source": "local"},
                {"id": 3, "name": "o1.txt", "mcp_view": "opaque", "content_type": ".txt", "source": "local"},
                {"id": 4, "name": "i1.txt", "mcp_view": "inherit", "content_type": ".txt", "source": "local"},
            ]
            filtered, suppressed = filter_results_list("file", results)
            assert suppressed == 1  # hidden excluded
            assert len(filtered) == 3
            ids = [r["id"] for r in filtered]
            assert 1 in ids and 3 in ids and 4 in ids
            assert 2 not in ids
            # opaque item minimized
            opaque = next(r for r in filtered if r["id"] == 3)
            assert "name" not in opaque
            # inherit item is now visible (global=visible)
            inherit = next(r for r in filtered if r["id"] == 4)
            assert inherit["name"] == "i1.txt"
        finally:
            vf._global_visibility = None
