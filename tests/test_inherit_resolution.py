"""Tests for inherit → global policy resolution logic."""

import pytest

from footprinter.services import access_service as vf


@pytest.fixture
def conn(tool_db):
    """Full-schema database for resolution tests."""
    yield tool_db


class TestLoadGlobals:
    def test_reads_policies_from_db(self, conn):
        """load_globals() caches global visibility and permission policies."""
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'visible')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'allow')")
        conn.commit()

        vf.load_globals(conn)

        assert vf._global_visibility == "visible"
        assert vf.has_global_permission() is True

    def test_missing_global_policy_sets_none(self, conn):
        """When no global policy exists, cache is None (baseline fallback)."""
        vf.load_globals(conn)

        assert vf._global_visibility is None
        assert vf.is_global_policy_loaded() is False

    def test_overwrites_previous_cache(self, conn):
        """Successive calls overwrite stale cache values."""
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()
        vf.load_globals(conn)
        assert vf._global_visibility == "hidden"

        conn.execute("UPDATE visibility_policies SET setting = 'visible' WHERE scope = 'global'")
        conn.commit()
        vf.load_globals(conn)
        assert vf._global_visibility == "visible"


class TestResolveInheritVisibility:
    def test_none_returns_opaque(self):
        """None (truly missing) always fails closed to opaque."""
        assert vf.resolve_inherit_visibility(None) == "opaque"

    def test_inherit_with_global_visible(self, conn):
        """inherit resolves to global policy when global=visible."""
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'visible')")
        conn.commit()
        vf.load_globals(conn)

        assert vf.resolve_inherit_visibility("inherit") == "visible"

    def test_inherit_with_global_hidden(self, conn):
        """inherit resolves to global policy when global=hidden."""
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()
        vf.load_globals(conn)

        assert vf.resolve_inherit_visibility("inherit") == "hidden"

    def test_inherit_without_global_falls_to_baseline(self, conn):
        """inherit without global policy falls back to baseline (opaque)."""
        vf.load_globals(conn)

        assert vf.resolve_inherit_visibility("inherit") == "opaque"

    def test_explicit_values_pass_through(self):
        """Explicit values are returned unchanged."""
        assert vf.resolve_inherit_visibility("visible") == "visible"
        assert vf.resolve_inherit_visibility("opaque") == "opaque"
        assert vf.resolve_inherit_visibility("hidden") == "hidden"


class TestResolveInheritPermission:
    def test_none_returns_deny(self):
        """None (truly missing) always fails closed to deny."""
        assert vf.resolve_inherit_permission(None) == "deny"

    def test_inherit_with_global_allow(self, conn):
        """inherit resolves to global policy when global=allow."""
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'allow')")
        conn.commit()
        vf.load_globals(conn)

        assert vf.resolve_inherit_permission("inherit") == "allow"

    def test_inherit_with_global_deny(self, conn):
        """inherit resolves to global policy when global=deny."""
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'deny')")
        conn.commit()
        vf.load_globals(conn)

        assert vf.resolve_inherit_permission("inherit") == "deny"

    def test_inherit_without_global_falls_to_baseline(self, conn):
        """inherit without global policy falls back to baseline (allow)."""
        vf.load_globals(conn)

        assert vf.resolve_inherit_permission("inherit") == "allow"

    def test_explicit_values_pass_through(self):
        """Explicit values are returned unchanged."""
        assert vf.resolve_inherit_permission("allow") == "allow"
        assert vf.resolve_inherit_permission("deny") == "deny"


class TestEndToEnd:
    def test_filter_result_with_inherit_and_global_visible(self, conn):
        """Entity with inherit + global=visible is visible through the filter."""
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'visible')")
        conn.commit()
        vf.load_globals(conn)

        result = {
            "id": 1,
            "name": "file.txt",
            "content_type": ".txt",
            "source": "local",
            "path": "/test",
            "mcp_view": "inherit",
        }
        from footprinter.services.access_service import filter_result

        filtered = filter_result("file", result)
        assert filtered is not None
        assert filtered["name"] == "file.txt"
        assert filtered["path"] == "/test"

    def test_strip_content_with_inherit_and_global_allow(self, conn):
        """Content preserved when mcp_read=inherit and global=allow."""
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'allow')")
        conn.commit()
        vf.load_globals(conn)

        results = [{"id": 1, "snippet": "hello", "mcp_read": "inherit"}]
        from footprinter.services.access_service import strip_content_for_denied

        stripped = strip_content_for_denied("chat", results)
        assert stripped[0]["snippet"] == "hello"

    def test_strip_content_with_inherit_and_no_global(self, conn):
        """Content preserved when mcp_read=inherit and no global policy (baseline allow)."""
        vf.load_globals(conn)

        results = [{"id": 1, "snippet": "hello", "mcp_read": "inherit"}]
        from footprinter.services.access_service import strip_content_for_denied

        stripped = strip_content_for_denied("chat", results)
        assert stripped[0]["snippet"] == "hello"
