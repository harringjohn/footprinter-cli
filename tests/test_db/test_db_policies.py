"""Tests for footprinter.db.policies — access control policy CRUD."""

import sqlite3

import pytest


@pytest.fixture
def conn():
    """In-memory SQLite with policy tables."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE visibility_policies (  scope TEXT PRIMARY KEY,  setting TEXT NOT NULL,  updated_at TEXT)")
    db.execute("CREATE TABLE permission_policies (  scope TEXT PRIMARY KEY,  setting TEXT NOT NULL,  updated_at TEXT)")
    db.commit()
    return db


# ---------------------------------------------------------------------------
# Visibility policy tests
# ---------------------------------------------------------------------------


class TestSetVisibilityPolicy:
    def test_insert_new(self, conn):
        from footprinter.db.policies import set_visibility_policy

        result = set_visibility_policy(conn, "global", "full")
        assert result is True
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        assert row["setting"] == "full"

    def test_upsert_existing(self, conn):
        from footprinter.db.policies import set_visibility_policy

        set_visibility_policy(conn, "global", "full")
        set_visibility_policy(conn, "global", "hidden")
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        assert row["setting"] == "hidden"

    def test_invalid_setting(self, conn):
        from footprinter.db.policies import set_visibility_policy

        with pytest.raises(ValueError, match="Invalid visibility setting"):
            set_visibility_policy(conn, "global", "bogus")

    def test_invalid_scope_rejected(self, conn):
        from footprinter.db.policies import set_visibility_policy

        with pytest.raises(ValueError, match="Invalid scope"):
            set_visibility_policy(conn, "banana", "full")

    @pytest.mark.parametrize(
        "scope",
        [
            "global",
            "source:files",
            "folder:~/Work",
            "file:42",
            "project:1",
            "client:2",
            "email:10",
            "chat:5",
            "account:personal",
        ],
    )
    def test_valid_scopes_accepted(self, conn, scope):
        from footprinter.db.policies import set_visibility_policy

        set_visibility_policy(conn, scope, "full")

    @pytest.mark.parametrize(
        "scope",
        ["", "source:", "file:abc", "unknown:1", "  ", "file:", "project:abc"],
    )
    def test_invalid_scope_patterns(self, conn, scope):
        from footprinter.db.policies import set_visibility_policy

        with pytest.raises(ValueError, match="Invalid scope"):
            set_visibility_policy(conn, scope, "full")


class TestDeleteVisibilityPolicy:
    def test_delete_existing(self, conn):
        from footprinter.db.policies import delete_visibility_policy, set_visibility_policy

        set_visibility_policy(conn, "global", "full")
        result = delete_visibility_policy(conn, "global")
        assert result is True
        row = conn.execute("SELECT * FROM visibility_policies WHERE scope = 'global'").fetchone()
        assert row is None

    def test_delete_missing(self, conn):
        from footprinter.db.policies import delete_visibility_policy

        result = delete_visibility_policy(conn, "nonexistent")
        assert result is False


class TestClearVisibilityPolicies:
    def test_clear_returns_count(self, conn):
        from footprinter.db.policies import clear_visibility_policies, set_visibility_policy

        set_visibility_policy(conn, "global", "full")
        set_visibility_policy(conn, "folder:~/Work", "hidden")
        count = clear_visibility_policies(conn)
        assert count == 2
        row = conn.execute("SELECT COUNT(*) FROM visibility_policies").fetchone()
        assert row[0] == 0


class TestListVisibilityPolicies:
    def test_list_returns_dicts(self, conn):
        from footprinter.db.policies import list_visibility_policies, set_visibility_policy

        set_visibility_policy(conn, "global", "full")
        set_visibility_policy(conn, "folder:~/Work", "hidden")
        result = list_visibility_policies(conn)
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(r, dict) for r in result)
        scopes = {r["scope"] for r in result}
        assert scopes == {"global", "folder:~/Work"}
        for r in result:
            assert "scope" in r
            assert "setting" in r
            assert "updated_at" in r


# ---------------------------------------------------------------------------
# Permission policy tests (mirror visibility)
# ---------------------------------------------------------------------------


class TestSetPermissionPolicy:
    def test_insert_new(self, conn):
        from footprinter.db.policies import set_permission_policy

        result = set_permission_policy(conn, "global", "deny")
        assert result is True
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        assert row["setting"] == "deny"

    def test_upsert_existing(self, conn):
        from footprinter.db.policies import set_permission_policy

        set_permission_policy(conn, "global", "deny")
        set_permission_policy(conn, "global", "allow")
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        assert row["setting"] == "allow"

    def test_invalid_setting(self, conn):
        from footprinter.db.policies import set_permission_policy

        with pytest.raises(ValueError, match="Invalid permission setting"):
            set_permission_policy(conn, "global", "bogus")

    def test_invalid_scope_rejected(self, conn):
        from footprinter.db.policies import set_permission_policy

        with pytest.raises(ValueError, match="Invalid scope"):
            set_permission_policy(conn, "banana", "deny")

    @pytest.mark.parametrize(
        "scope",
        [
            "global",
            "source:files",
            "folder:~/Work",
            "file:42",
            "project:1",
            "client:2",
            "email:10",
            "chat:5",
            "account:personal",
        ],
    )
    def test_valid_scopes_accepted(self, conn, scope):
        from footprinter.db.policies import set_permission_policy

        set_permission_policy(conn, scope, "deny")


class TestDeletePermissionPolicy:
    def test_delete_existing(self, conn):
        from footprinter.db.policies import delete_permission_policy, set_permission_policy

        set_permission_policy(conn, "global", "deny")
        result = delete_permission_policy(conn, "global")
        assert result is True

    def test_delete_missing(self, conn):
        from footprinter.db.policies import delete_permission_policy

        result = delete_permission_policy(conn, "nonexistent")
        assert result is False


class TestClearPermissionPolicies:
    def test_clear_returns_count(self, conn):
        from footprinter.db.policies import clear_permission_policies, set_permission_policy

        set_permission_policy(conn, "global", "deny")
        set_permission_policy(conn, "source:files", "allow")
        count = clear_permission_policies(conn)
        assert count == 2


class TestListPermissionPolicies:
    def test_list_returns_dicts(self, conn):
        from footprinter.db.policies import list_permission_policies, set_permission_policy

        set_permission_policy(conn, "global", "deny")
        result = list_permission_policies(conn)
        assert len(result) == 1
        assert result[0]["scope"] == "global"
        assert result[0]["setting"] == "deny"


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


class TestSeedDefaults:
    def test_seed_visibility_defaults(self, conn):
        from footprinter.db.policies import seed_visibility_defaults

        result = seed_visibility_defaults(conn)
        assert result is True
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        assert row["setting"] == "full"

    def test_seed_visibility_idempotent(self, conn):
        from footprinter.db.policies import seed_visibility_defaults

        seed_visibility_defaults(conn)
        result = seed_visibility_defaults(conn)
        assert result is False  # already existed

    def test_seed_permission_defaults(self, conn):
        from footprinter.db.policies import seed_permission_defaults

        result = seed_permission_defaults(conn)
        assert result is True
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        assert row["setting"] == "allow"

    def test_seed_permission_idempotent(self, conn):
        from footprinter.db.policies import seed_permission_defaults

        seed_permission_defaults(conn)
        result = seed_permission_defaults(conn)
        assert result is False

    def test_seed_access_policies(self, conn):
        from footprinter.db.policies import seed_access_policies

        result = seed_access_policies(conn)
        assert result == {"visibility_seeded": True, "permission_seeded": True}
        # Second call — both already exist
        result2 = seed_access_policies(conn)
        assert result2 == {"visibility_seeded": False, "permission_seeded": False}


# ---------------------------------------------------------------------------
# is_folder_path_scope — shared scope utility
# ---------------------------------------------------------------------------


class TestIsFolderPathScope:
    def test_path_scope_returns_true(self):
        from footprinter.db.policies import is_folder_path_scope

        assert is_folder_path_scope("folder:~/Work") is True

    def test_numeric_id_returns_false(self):
        from footprinter.db.policies import is_folder_path_scope

        assert is_folder_path_scope("folder:42") is False

    def test_path_with_slashes(self):
        from footprinter.db.policies import is_folder_path_scope

        assert is_folder_path_scope("folder:/Users/test/Work") is True

    def test_zero_is_numeric(self):
        from footprinter.db.policies import is_folder_path_scope

        assert is_folder_path_scope("folder:0") is False

    def test_mixed_alphanumeric(self):
        from footprinter.db.policies import is_folder_path_scope

        assert is_folder_path_scope("folder:42abc") is True
