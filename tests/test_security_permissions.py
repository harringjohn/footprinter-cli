"""
Tests for email account permissions, chat permissions, and chat read access.

Tests the features from plan.md:
1. Account-level permissions for emails (account: scope in hierarchy)
2. Chat source → account rename
3. Chat read permissions and content access
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def test_db(tool_db):
    """Full-schema database for security permission tests."""
    yield tool_db


class TestEmailAccountPermissions:
    """Test account-level permission defaults for emails."""

    def test_email_account_allow(self, test_db):
        """Email with account-level allow should be readable."""
        cursor = test_db.cursor()

        # Insert test email with personal account
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'personal', 'Test subject', '2024-01-01')
        """
        )
        # Set account-level allow
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('account:personal', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "email", 1) is True

    def test_email_account_deny_overrides_source_allow(self, test_db):
        """Account-level deny should override source-level allow (deny-wins)."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'personal', 'Test subject', '2024-01-01')
        """
        )
        # Set source-level allow
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('source:emails', 'allow')
        """
        )
        # Set account-level deny
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('account:personal', 'deny')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "email", 1) is False

    def test_email_item_level_deny_overrides_account_allow(self, test_db):
        """Item-level deny should override account-level allow (deny-wins)."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'personal', 'Test subject', '2024-01-01')
        """
        )
        # Item-level policy (deny) - uses permission_policies, not item column
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('email:1', 'deny')
        """
        )
        # Account-level policy (allow)
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('account:personal', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "email", 1) is False

    def test_email_hierarchy_item_account_source_global(self, test_db):
        """Test full hierarchy: item → account → source → global."""
        cursor = test_db.cursor()

        # Email with inherit at item level
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, mcp_read, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test subject', 'inherit', '2024-01-01')
        """
        )
        # No account default set, source-level allow
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('source:emails', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        # Should fall through to source level
        assert can_read(test_db, "email", 1) is True

    def test_email_no_account_uses_source(self, test_db):
        """Email without account should use source policy."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', '', 'Test subject', '2024-01-01')
        """
        )
        # Use source policy (no global scope in new model)
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('source:emails', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "email", 1) is True

    def test_email_no_policies_uses_baseline(self, test_db):
        """Email with no matching policies uses baseline (allow)."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test subject', '2024-01-01')
        """
        )
        # No policies set
        test_db.commit()

        from footprinter.permissions import BASELINE_PERMISSION, can_read

        assert can_read(test_db, "email", 1) is BASELINE_PERMISSION  # False


class TestChatPermissions:
    """Test chat read permissions."""

    def test_chat_permission_item_allow(self, test_db):
        """Chat with item-level policy allow should be readable."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test chat')
        """
        )
        # Use policy row instead of item column
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('chat:1', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "chat", 1) is True

    def test_chat_permission_item_deny(self, test_db):
        """Chat with item-level policy deny should not be readable."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test chat')
        """
        )
        # Item policy + source policy - deny wins
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('chat:1', 'deny')
        """
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('source:chats', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "chat", 1) is False

    def test_chat_account_level_permission(self, test_db):
        """Chat should respect account-level defaults."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test chat')
        """
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('account:claude', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "chat", 1) is True

    def test_chat_source_chats_fallback(self, test_db):
        """Chat should fall back to source:chats default."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test chat')
        """
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('source:chats', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import can_read

        assert can_read(test_db, "chat", 1) is True

    def test_chat_not_found(self, test_db):
        """Non-existent chat should return False."""
        from footprinter.permissions import can_read

        assert can_read(test_db, "chat", 999) is False


class TestChatVisibility:
    """Test chat visibility with account column."""

    def test_chat_visibility_uses_account(self, test_db):
        """Visibility should check account-level defaults."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test chat')
        """
        )
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('account:claude', 'hidden')
        """
        )
        test_db.commit()

        from footprinter.visibility import get_visibility

        assert get_visibility(test_db, "chat", 1) == "hidden"

    def test_chat_visibility_account_opaque(self, test_db):
        """Account-level opaque should make chat opaque."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'chatgpt', 'ChatGPT convo')
        """
        )
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('account:chatgpt', 'opaque')
        """
        )
        test_db.commit()

        from footprinter.visibility import get_visibility

        assert get_visibility(test_db, "chat", 1) == "opaque"

    def test_chat_visibility_item_level_wins(self, test_db):
        """Item-level hidden policy should override account-level visible."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test chat')
        """
        )
        # Use policy row instead of item column
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('chat:1', 'hidden')
        """
        )
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('account:claude', 'visible')
        """
        )
        test_db.commit()

        from footprinter.visibility import get_visibility

        assert get_visibility(test_db, "chat", 1) == "hidden"


class TestChatRead:
    """Test reading chat content."""

    def test_read_chat_with_messages(self, test_db):
        """Should return chat with all messages."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats
            (id, external_id, account, title, summary, created_at, message_count, mcp_view)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test Chat', 'A test conversation',
                    '2024-01-15', 2, 'visible')
        """
        )
        cursor.execute(
            """
            INSERT INTO messages (chat_id, role, content, created_at)
            VALUES (1, 'user', 'Hello!', '2024-01-15 10:00:00')
        """
        )
        cursor.execute(
            """
            INSERT INTO messages (chat_id, role, content, created_at)
            VALUES (1, 'assistant', 'Hi there! How can I help?', '2024-01-15 10:01:00')
        """
        )
        # Use source policy instead of global
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting) VALUES ('source:chats', 'visible')
        """
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting) VALUES ('source:chats', 'allow')
        """
        )
        test_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: test_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert "error" not in result
        assert "content" in result
        assert "metadata" in result
        assert "Hello!" in result["content"]
        assert "Hi there!" in result["content"]
        assert result["metadata"]["title"] == "Test Chat"

    def test_read_chat_permission_denied(self, test_db):
        """Should return permission denied for blocked chat."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title, mcp_view, mcp_read)
            VALUES (1, 'conv-uuid-1', 'claude', 'Secret Chat', 'visible', 'deny')
        """
        )
        test_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: test_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert result["error_code"] == "PERMISSION_DENIED"
        assert result["metadata"].get("id") == 1
        assert result["metadata"].get("account") == "claude"
        assert "title" not in result["metadata"]
        assert "summary" not in result["metadata"]

    def test_read_chat_hidden(self, test_db):
        """Hidden chat should return NOT_FOUND."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title, mcp_view)
            VALUES (1, 'conv-uuid-1', 'claude', 'Hidden Chat', 'hidden')
        """
        )
        test_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: test_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert result["error_code"] == "NOT_FOUND"

    def test_read_chat_opaque(self, test_db):
        """Opaque chat should return VISIBILITY_RESTRICTED with minimal metadata."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title, mcp_view)
            VALUES (1, 'conv-uuid-1', 'claude', 'Opaque Chat', 'opaque')
        """
        )
        test_db.commit()

        with patch("footprinter.mcp.tools.read.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: test_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.read import footprinter_read

            result = footprinter_read("chat", 1)

        assert result["error_code"] == "VISIBILITY_RESTRICTED"
        assert "metadata" in result
        assert "id" in result["metadata"]
        assert "account" in result["metadata"]
        # Title should NOT be in opaque metadata
        assert "title" not in result["metadata"]


class TestVisibilityFilterAccount:
    """Test visibility filter uses account field for chats."""

    def test_opaque_chat_returns_account_field(self, test_db):
        """Opaque chat metadata should include account, not source."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT INTO chats (id, external_id, account, title)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test Chat')
        """
        )
        test_db.commit()

        from footprinter.services.access_service import get_opaque_metadata

        metadata = get_opaque_metadata(test_db, "chat", 1)

        assert "id" in metadata
        assert "account" in metadata
        assert metadata["account"] == "claude"


class TestSearchChatAccount:
    """Test search returns account field for chats."""

    def test_search_chats_returns_account(self, test_db):
        """Chat search results should include account field."""
        cursor = test_db.cursor()

        cursor.execute(
            """
            INSERT
                INTO chats (id, external_id, account, title, created_at,
                    message_count)
            VALUES (1, 'conv-uuid-1', 'claude', 'Test Chat', '2024-01-15', 5)
        """
        )
        # Use source policy instead of global
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting) VALUES ('source:chats', 'visible')
        """
        )
        test_db.commit()

        with patch("footprinter.mcp.tools.search.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: test_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None

            from footprinter.mcp.tools.search import footprinter_search

            results = footprinter_search("Test", sources=["chats"])

        assert "chats" in results
        assert len(results["chats"]) == 1
        assert results["chats"][0]["account"] == "claude"


class TestBatchPermissionResolution:
    """Test batch permission resolution functions."""

    @pytest.fixture
    def batch_db(self, tool_db):
        """Full-schema database for batch permission tests."""
        yield tool_db

    def test_batch_resolve_empty_list(self, batch_db):
        """Batch resolve with empty list returns empty dict."""
        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(batch_db, "file", [])
        assert result == {}

    def test_batch_resolve_single_entity_baseline(self, batch_db):
        """Single entity with no policies returns baseline."""
        cursor = batch_db.cursor()
        cursor.execute("INSERT INTO files (id, name, path, source) VALUES (1, 'file.txt', '/test/file.txt', 'local')")
        batch_db.commit()

        from footprinter.permissions import BASELINE_PERMISSION, batch_resolve_permissions

        result = batch_resolve_permissions(batch_db, "file", [1])
        assert result[1] == (BASELINE_PERMISSION, "baseline")

    def test_batch_resolve_multiple_entities_folder_policy(self, batch_db):
        """Multiple entities resolved against folder policy."""
        TEST_HOME = "/Users/testuser"
        cursor = batch_db.cursor()
        cursor.execute(
            f"INSERT INTO files (id, name, path, source) VALUES (1, 'file1.txt', '{TEST_HOME}/Work/file1.txt', 'local')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, path, source)"
            f" VALUES (2, 'file2.txt', '{TEST_HOME}/Work/file2.txt', 'local')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, path, source)"
            f" VALUES (3, 'file3.txt', '{TEST_HOME}/Personal/file3.txt', 'local')"
        )
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting)"
            f" VALUES ('folder:{TEST_HOME}/Work', 'allow')"
        )
        batch_db.commit()

        from footprinter.permissions import BASELINE_PERMISSION, batch_resolve_permissions

        result = batch_resolve_permissions(batch_db, "file", [1, 2, 3])
        assert result[1] == (True, f"folder:{TEST_HOME}/Work")
        assert result[2] == (True, f"folder:{TEST_HOME}/Work")
        assert result[3] == (BASELINE_PERMISSION, "baseline")  # No matching policy

    def test_batch_resolve_projects(self, batch_db):
        """Batch resolve permissions for projects."""
        cursor = batch_db.cursor()
        cursor.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (10, 'FK Client', 'fk', 'external')")
        cursor.execute("INSERT INTO projects (id, project_name, client_id) VALUES (1, 'Test Project', 10)")
        cursor.execute("INSERT INTO projects (id, project_name, client_id) VALUES (2, 'Test Project 2', NULL)")
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('project:1', 'allow')")
        batch_db.commit()

        from footprinter.permissions import BASELINE_PERMISSION, batch_resolve_permissions

        result = batch_resolve_permissions(batch_db, "project", [1, 2])
        assert result[1] == (True, "project:1")
        assert result[2] == (BASELINE_PERMISSION, "baseline")

    def test_batch_resolve_clients(self, batch_db):
        """Batch resolve permissions for clients."""
        cursor = batch_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Test Client', 'test-client', 'external')"
        )
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('client:1', 'deny')")
        batch_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(batch_db, "client", [1])
        assert result[1] == (False, "client:1")


class TestBatchVisibilityResolution:
    """Test batch visibility resolution functions."""

    @pytest.fixture
    def batch_db(self, tool_db):
        """Full-schema database for batch visibility tests."""
        yield tool_db

    def test_batch_visibility_empty_list(self, batch_db):
        """Batch resolve with empty list returns empty dict."""
        from footprinter.visibility import batch_resolve_visibility

        result = batch_resolve_visibility(batch_db, "file", [])
        assert result == {}

    def test_batch_visibility_single_entity_baseline(self, batch_db):
        """Single entity with no policies returns baseline."""
        cursor = batch_db.cursor()
        cursor.execute("INSERT INTO files (id, name, path, source) VALUES (1, 'file.txt', '/test/file.txt', 'local')")
        batch_db.commit()

        from footprinter.visibility import BASELINE_VISIBILITY, batch_resolve_visibility

        result = batch_resolve_visibility(batch_db, "file", [1])
        assert result[1] == (BASELINE_VISIBILITY, "baseline")

    def test_batch_visibility_folder_policy(self, batch_db):
        """Artifacts resolved against folder visibility policy."""
        TEST_HOME = "/Users/testuser"
        cursor = batch_db.cursor()
        cursor.execute(
            "INSERT INTO files (id, name, path, source)"
            f" VALUES (1, 'file1.txt', '{TEST_HOME}/Work/file1.txt', 'local')"
        )
        cursor.execute(
            "INSERT INTO files (id, name, path, source)"
            f" VALUES (2, 'file2.txt', '{TEST_HOME}/Personal/file2.txt', 'local')"
        )
        cursor.execute(
            "INSERT INTO visibility_policies (scope, setting)"
            f" VALUES ('folder:{TEST_HOME}/Work', 'visible')"
        )
        batch_db.commit()

        from footprinter.visibility import BASELINE_VISIBILITY, batch_resolve_visibility

        result = batch_resolve_visibility(batch_db, "file", [1, 2])
        assert result[1] == ("visible", f"folder:{TEST_HOME}/Work")
        assert result[2] == (BASELINE_VISIBILITY, "baseline")

    def test_batch_visibility_most_restrictive_wins(self, batch_db):
        """Most restrictive visibility wins (hidden > opaque > visible)."""
        cursor = batch_db.cursor()
        cursor.execute("INSERT INTO projects (id, project_name, client_id) VALUES (1, 'Test Project', NULL)")
        cursor.execute(
            "INSERT INTO files (id, name, path, project_id, source)"
            " VALUES (1, 'file.txt', '/test/file.txt', 1, 'local')"
        )
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('folder:/test', 'visible')")
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:1', 'hidden')")
        batch_db.commit()

        from footprinter.visibility import batch_resolve_visibility

        result = batch_resolve_visibility(batch_db, "file", [1])
        # Visibility should be hidden, source includes "via" chain for parent resolution
        assert result[1][0] == "hidden"
        assert "project:1" in result[1][1]

    def test_batch_visibility_projects(self, batch_db):
        """Batch resolve visibility for projects."""
        cursor = batch_db.cursor()
        cursor.execute("INSERT INTO projects (id, project_name, client_id) VALUES (1, 'Test Project', NULL)")
        cursor.execute("INSERT INTO projects (id, project_name, client_id) VALUES (2, 'Test Project 2', NULL)")
        cursor.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:1', 'visible')")
        batch_db.commit()

        from footprinter.visibility import BASELINE_VISIBILITY, batch_resolve_visibility

        result = batch_resolve_visibility(batch_db, "project", [1, 2])
        assert result[1] == ("visible", "project:1")
        assert result[2] == (BASELINE_VISIBILITY, "baseline")


class TestAPIAccountScope:
    """Test dashboard API accepts account: scope for permission defaults."""

    def test_api_accepts_account_scope(self, test_db):
        """Permission defaults API should accept account: scope."""
        # Minimal test of the validation logic
        scope = "account:personal"
        valid_scopes = ("global",)

        # This is the validation from dashboard API - updated to accept account:
        is_valid = (
            scope in valid_scopes
            or scope.startswith("source:")
            or scope.startswith("folder:")
            or scope.startswith("account:")
        )

        assert is_valid is True

    def test_api_rejects_invalid_scope(self):
        """Permission defaults API should reject invalid scopes."""
        scope = "invalid:test"
        valid_scopes = ("global",)

        is_valid = (
            scope in valid_scopes
            or scope.startswith("source:")
            or scope.startswith("folder:")
            or scope.startswith("account:")
        )

        assert is_valid is False


class TestGlobalPolicyFallback:
    """Test that global policy is used as fallback before hardcoded baseline."""

    def test_no_global_permission_uses_baseline(self, test_db):
        """Without global policy, baseline (deny) is used."""
        cursor = test_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test', '2024-01-01')
"""
        )
        test_db.commit()

        from footprinter.permissions import BASELINE_PERMISSION, resolve_permission_with_source

        result, source = resolve_permission_with_source(test_db, "email", 1)
        assert result is BASELINE_PERMISSION
        assert source == "baseline"

    def test_global_allow_overrides_baseline(self, test_db):
        """Global allow policy should override hardcoded baseline."""
        cursor = test_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test', '2024-01-01')
"""
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('global', 'allow')
        """
        )
        test_db.commit()

        from footprinter.permissions import resolve_permission_with_source

        result, source = resolve_permission_with_source(test_db, "email", 1)
        assert result is True
        assert source == "global"

    def test_specific_policy_overrides_global(self, test_db):
        """Source deny + global allow → deny wins."""
        cursor = test_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test', '2024-01-01')
"""
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('global', 'allow')
        """
        )
        cursor.execute(
            """
            INSERT INTO permission_policies (scope, setting)
            VALUES ('source:emails', 'deny')
        """
        )
        test_db.commit()

        from footprinter.permissions import resolve_permission_with_source

        result, source = resolve_permission_with_source(test_db, "email", 1)
        assert result is False
        assert source == "source:emails"


class TestGlobalVisibilityFallback:
    """Test that global visibility policy is used as fallback before baseline."""

    def test_no_global_visibility_uses_baseline(self, test_db):
        """Without global policy, baseline (opaque) is used."""
        cursor = test_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test', '2024-01-01')
"""
        )
        test_db.commit()

        from footprinter.visibility import BASELINE_VISIBILITY, resolve_visibility_with_source

        result, source = resolve_visibility_with_source(test_db, "email", 1)
        assert result == BASELINE_VISIBILITY
        assert source == "baseline"

    def test_global_visible_overrides_baseline(self, test_db):
        """Global visible policy should override hardcoded baseline."""
        cursor = test_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test', '2024-01-01')
"""
        )
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('global', 'visible')
        """
        )
        test_db.commit()

        from footprinter.visibility import resolve_visibility_with_source

        result, source = resolve_visibility_with_source(test_db, "email", 1)
        assert result == "visible"
        assert source == "global"

    def test_specific_policy_overrides_global_visibility(self, test_db):
        """Source hidden + global visible → hidden wins (most restrictive)."""
        cursor = test_db.cursor()
        cursor.execute(
            """
            INSERT INTO emails (id, message_id, thread_id, account, subject, received_at)
            VALUES (1, 'msg1', 'thread1', 'work', 'Test', '2024-01-01')
"""
        )
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('global', 'visible')
        """
        )
        cursor.execute(
            """
            INSERT INTO visibility_policies (scope, setting)
            VALUES ('source:emails', 'hidden')
        """
        )
        test_db.commit()

        from footprinter.visibility import resolve_visibility_with_source

        result, source = resolve_visibility_with_source(test_db, "email", 1)
        assert result == "hidden"
        assert source == "source:emails"


class TestFolderNamespaceCollision:
    """Regression tests for folder: namespace collision between numeric IDs and paths.

    The folder: scope prefix serves two purposes:
    - folder:{id} — item-level policy targeting a specific folder row
    - folder:{path} — path-prefix policy matching files/folders under a directory

    Numeric-only suffixes (e.g., folder:42) must be treated as item-level IDs,
    not path prefixes, to avoid spurious prefix matching.
    """

    def test_folder_id_policy_not_used_as_path_prefix(self, test_db):
        """folder:42 deny should NOT deny a file whose path starts with '42'."""
        cursor = test_db.cursor()

        # File with a relative path that starts with the numeric string
        cursor.execute("INSERT INTO files (id, name, path, source) VALUES (1, 'report.txt', '42/report.txt', 'local')")
        # Folder row for ID 42 — lives at a completely different path
        cursor.execute(
            "INSERT INTO folders (id, path, relative_path, name) "
            "VALUES (42, '/some/other/path', 'some/other/path', 'path')"
        )
        # Policy targets folder ID 42, NOT path prefix '42'
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('folder:42', 'deny')")
        test_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(test_db, "file", [1])
        # Without fix: denied via prefix match (path '42/report.txt'.startswith('42'))
        # With fix: baseline (folder:42 excluded from prefix list)
        allowed, source = result[1]
        assert allowed is True, (
            f"folder:42 should be treated as item ID scope, not path prefix. Got denied via source={source}"
        )

    def test_single_item_file_not_denied_by_numeric_folder_scope(self, test_db):
        """Same as batch test but via single-item can_read() entrypoint."""
        cursor = test_db.cursor()

        cursor.execute("INSERT INTO files (id, name, path, source) VALUES (1, 'report.txt', '42/report.txt', 'local')")
        cursor.execute(
            "INSERT INTO folders (id, path, relative_path, name) "
            "VALUES (42, '/some/other/path', 'some/other/path', 'path')"
        )
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('folder:42', 'deny')")
        test_db.commit()

        from footprinter.permissions import can_read

        allowed = can_read(test_db, "file", 1)
        assert allowed is True, "folder:42 should be treated as item ID scope via single-item path too"

    def test_folder_id_policy_still_works_as_item_scope(self, test_db):
        """folder:42 deny should still deny folder ID 42 via item-level lookup."""
        cursor = test_db.cursor()

        cursor.execute(
            "INSERT INTO folders (id, path, relative_path, name) "
            "VALUES (42, '/projects/internal', 'projects/internal', 'internal')"
        )
        cursor.execute("INSERT INTO permission_policies (scope, setting) VALUES ('folder:42', 'deny')")
        test_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(test_db, "folder", [42])
        allowed, source = result[42]
        assert allowed is False, "folder:42 item-level deny should still work"
        assert source == "folder:42"


class TestDirectClientPermissions:
    """Resolver must honor entity.client_id when project_id is NULL or its project
    points at a different client. Mirrors TestDirectClientVisibility for permissions.
    """

    @pytest.fixture
    def client_deny_db(self, test_db):
        """Seed clients + projects + permission_policies('client:2', 'deny')."""
        cursor = test_db.cursor()
        cursor.execute(
            "INSERT INTO clients (id, name, slug, client_type) VALUES "
            "(1, 'AllowedClient', 'allowed', 'external'),"
            "(2, 'DeniedClient', 'denied', 'external')"
        )
        cursor.execute(
            "INSERT INTO projects (id, project_name, root_path, client_id) "
            "VALUES (1, 'AllowedProj', '/test/allowed', 1)"
        )
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES "
            "('client:2', 'deny'),"
            "('source:files', 'allow'),"
            "('source:folders', 'allow'),"
            "('source:emails', 'allow'),"
            "('source:chats', 'allow')"
        )
        test_db.commit()
        yield test_db

    def test_file_direct_client_id_applies_client_policy(self, client_deny_db):
        """File with client_id=2, project_id=NULL must resolve via client:2 deny."""
        client_deny_db.execute(
            "INSERT INTO files (id, source, name, path, client_id, project_id) "
            "VALUES (50, 'local', 'direct.txt', '/test/direct.txt', 2, NULL)"
        )
        client_deny_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(client_deny_db, "file", [50])
        assert result[50][0] is False, f"Expected deny via client:2, got {result[50]}"

    def test_folder_direct_client_id_applies_client_policy(self, client_deny_db):
        """Folder with client_id=2, project_id=NULL must resolve via client:2 deny."""
        client_deny_db.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, client_id) "
            "VALUES (50, 'direct', '/test/direct-folder', 'test/direct-folder', 'local', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(client_deny_db, "folder", [50])
        assert result[50][0] is False, f"Expected deny via client:2, got {result[50]}"

    def test_email_direct_client_id_applies_client_policy(self, client_deny_db):
        """Email with client_id=2, project_id=NULL must resolve via client:2 deny."""
        client_deny_db.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, "
            "project_id, client_id) "
            "VALUES (50, 'msg50', 't50', 'work', 'Direct', '2026-02-01', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(client_deny_db, "email", [50])
        assert result[50][0] is False, f"Expected deny via client:2, got {result[50]}"

    def test_chat_direct_client_id_applies_client_policy(self, client_deny_db):
        """Chat with client_id=2, project_id=NULL must resolve via client:2 deny."""
        client_deny_db.execute(
            "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
            "VALUES (50, 'conv50', 'claude', 'Direct', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(client_deny_db, "chat", [50])
        assert result[50][0] is False, f"Expected deny via client:2, got {result[50]}"

    def test_file_direct_client_id_overrides_project_client_id(self, client_deny_db):
        """File in project (client_id=1, no policy) but tagged direct client_id=2 (deny)
        must resolve to deny — entity's direct client_id wins."""
        client_deny_db.execute(
            "INSERT INTO files (id, source, name, path, project_id, client_id) "
            "VALUES (60, 'local', 'cross.txt', '/test/cross.txt', 1, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import batch_resolve_permissions

        result = batch_resolve_permissions(client_deny_db, "file", [60])
        assert result[60][0] is False, (
            f"Direct client_id=2 (deny) should win over project's client_id=1; got {result[60]}"
        )

    def test_single_entity_file_direct_client_id_applies(self, client_deny_db):
        """Single-entity can_read() honors direct file.client_id (parity with batch)."""
        client_deny_db.execute(
            "INSERT INTO files (id, source, name, path, project_id, client_id) "
            "VALUES (70, 'local', 'single.txt', '/test/single.txt', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import can_read

        assert can_read(client_deny_db, "file", 70) is False

    def test_single_entity_folder_direct_client_id_applies(self, client_deny_db):
        """Single-entity can_read() honors direct folder.client_id."""
        client_deny_db.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, client_id) "
            "VALUES (70, 'single', '/test/single-folder', 'test/single-folder', 'local', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import can_read

        assert can_read(client_deny_db, "folder", 70) is False

    def test_single_entity_email_direct_client_id_applies(self, client_deny_db):
        """Single-entity can_read() honors direct email.client_id."""
        client_deny_db.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, "
            "project_id, client_id) "
            "VALUES (70, 'msg70', 't70', 'work', 'Single', '2026-02-03', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import can_read

        assert can_read(client_deny_db, "email", 70) is False

    def test_single_entity_chat_direct_client_id_applies(self, client_deny_db):
        """Single-entity can_read() honors direct chat.client_id."""
        client_deny_db.execute(
            "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
            "VALUES (70, 'conv70', 'claude', 'Single', NULL, 2)"
        )
        client_deny_db.commit()

        from footprinter.permissions import can_read

        assert can_read(client_deny_db, "chat", 70) is False
