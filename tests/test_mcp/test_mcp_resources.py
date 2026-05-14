"""Tests for MCP resource handler functions and the static guidance string."""

from unittest.mock import patch

from footprinter.utils.exceptions import DatabaseNotInitializedError
from footprinter.services.roles import Role


# ---------------------------------------------------------------------------
# TestSummaryResource — context_summary() backed by status_service
# ---------------------------------------------------------------------------
class TestSummaryResource:
    """Tests for context_summary (footprinter.mcp.resources.context)."""

    def _call(self, mcp_db):
        """Call context_summary() with a patched DB connection."""
        with patch("footprinter.mcp.resources.context.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            from footprinter.mcp.resources.context import context_summary

            return context_summary()

    def test_returns_dict(self, tool_db):
        result = self._call(tool_db)
        assert isinstance(result, dict)

    def test_response_shape_matches_status_tool(self, tool_db):
        """Resource mirrors status_service.get_status(role=VIEWER) — same keys as footprinter_status."""
        result = self._call(tool_db)
        expected_keys = {
            "sources",
            "files_by_source",
            "files_by_status",
            "projects_by_status",
            "emails_by_client",
            "chats_by_client",
        }
        assert set(result.keys()) == expected_keys

    def test_handles_uninitialized_db(self):
        """If the DB is missing tables, the resource returns a structured MCP error, not a raw exception."""
        with patch("footprinter.mcp.resources.context.get_db") as mock_get_db:
            mock_get_db.side_effect = DatabaseNotInitializedError()
            from footprinter.mcp.resources.context import context_summary

            result = context_summary()
        assert isinstance(result, dict)
        assert result.get("error_code") == "DB_NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# TestGuidanceResource — context_guidance() static string
# ---------------------------------------------------------------------------
class TestGuidanceResource:
    """Tests for context_guidance (footprinter.mcp.resources.context)."""

    def test_returns_string(self):
        from footprinter.mcp.resources.context import context_guidance

        assert isinstance(context_guidance(), str)
        assert context_guidance().strip() != ""

    def test_mentions_core_tools(self):
        """Guidance must reference the keyword-search, status, and read tools by name."""
        from footprinter.mcp.resources.context import context_guidance

        guidance = context_guidance()
        for tool in ("footprinter_search", "footprinter_status", "footprinter_read"):
            assert tool in guidance, f"Guidance missing reference to {tool}"

    def test_is_substantial(self):
        """Guidance should be at least 200 chars — short enough to scan, long enough to be useful."""
        from footprinter.mcp.resources.context import context_guidance

        assert len(context_guidance()) >= 200


# ---------------------------------------------------------------------------
# TestModuleStructure — sanity checks on the resources package
# ---------------------------------------------------------------------------
class TestModuleStructure:
    def test_resources_package_importable(self):
        import footprinter.mcp.resources  # noqa: F401

    def test_context_module_importable(self):
        import footprinter.mcp.resources.context  # noqa: F401

    def test_guidance_constant_exists(self):
        from footprinter.mcp.resources.context import GUIDANCE

        assert isinstance(GUIDANCE, str)
        assert len(GUIDANCE) >= 200

    def test_discoverability_module_importable(self):
        import footprinter.mcp.resources.discoverability  # noqa: F401


# ---------------------------------------------------------------------------
# TestSystemStatusResource — system_status() backed by status_service (VIEWER)
# ---------------------------------------------------------------------------
class TestSystemStatusResource:
    """Tests for system_status (footprinter.mcp.resources.discoverability)."""

    def _call(self, mcp_db):
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            from footprinter.mcp.resources.discoverability import system_status

            return system_status()

    def test_returns_dict(self, tool_db):
        result = self._call(tool_db)
        assert isinstance(result, dict)

    def test_response_shape_matches_viewer_status(self, tool_db):
        """Resource mirrors status_service.get_status(role=VIEWER) — same keys as the VIEWER status payload."""
        result = self._call(tool_db)
        expected_keys = {
            "sources",
            "files_by_source",
            "files_by_status",
            "projects_by_status",
            "emails_by_client",
            "chats_by_client",
        }
        assert set(result.keys()) == expected_keys

    def test_handles_uninitialized_db(self):
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.side_effect = DatabaseNotInitializedError()
            from footprinter.mcp.resources.discoverability import system_status

            result = system_status()
        assert isinstance(result, dict)
        assert result.get("error_code") == "DB_NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# TestProjectsResource — projects_list() backed by project_service (VIEWER)
# ---------------------------------------------------------------------------
class TestProjectsResource:
    """Tests for projects_list (footprinter.mcp.resources.discoverability)."""

    def _call(self, mcp_db):
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            from footprinter.mcp.resources.discoverability import projects_list

            return projects_list()

    def test_returns_dict_with_projects_list(self, tool_db):
        result = self._call(tool_db)
        assert isinstance(result, dict)
        assert "projects" in result
        assert isinstance(result["projects"], list)
        assert "pagination" in result
        assert isinstance(result["pagination"], dict)

    def test_uses_viewer_role(self, tool_db):
        """projects_list delegates to project_service.list_ with role=Role.VIEWER."""
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: tool_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            with patch("footprinter.mcp.resources.discoverability.project_service.list_") as mock_list:
                mock_list.return_value = {"projects": [], "pagination": {}}
                from footprinter.mcp.resources.discoverability import projects_list

                projects_list()

        mock_list.assert_called_once()
        kwargs = mock_list.call_args.kwargs
        assert kwargs.get("role") == Role.VIEWER

    def test_handles_uninitialized_db(self):
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.side_effect = DatabaseNotInitializedError()
            from footprinter.mcp.resources.discoverability import projects_list

            result = projects_list()
        assert isinstance(result, dict)
        assert result.get("error_code") == "DB_NOT_INITIALIZED"


# ---------------------------------------------------------------------------
# TestAccessPoliciesResource — access_policies() backed by db.policies
# ---------------------------------------------------------------------------
class TestAccessPoliciesResource:
    """Tests for access_policies (footprinter.mcp.resources.discoverability)."""

    def _call(self, mcp_db):
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: mcp_db
            mock_get_db.return_value.__exit__ = lambda s, *args: None
            from footprinter.mcp.resources.discoverability import access_policies

            return access_policies()

    def test_returns_visibility_and_permission_keys(self, tool_db):
        result = self._call(tool_db)
        assert isinstance(result, dict)
        assert "visibility" in result
        assert "permission" in result
        assert isinstance(result["visibility"], list)
        assert isinstance(result["permission"], list)

    def test_empty_policies_returns_empty_lists(self, tool_db):
        """With no policy rows seeded, both lists are empty (not None, not missing)."""
        result = self._call(tool_db)
        assert result["visibility"] == []
        assert result["permission"] == []

    def test_returns_seeded_policies(self, tool_db):
        """Seeded policies appear in the resource payload with scope/setting fields."""
        from footprinter.db.policies import set_permission_policy, set_visibility_policy

        set_visibility_policy(tool_db, "global", "visible")
        set_permission_policy(tool_db, "global", "deny")

        result = self._call(tool_db)
        vis_scopes = {p["scope"] for p in result["visibility"]}
        perm_scopes = {p["scope"] for p in result["permission"]}
        assert "global" in vis_scopes
        assert "global" in perm_scopes

    def test_handles_uninitialized_db(self):
        with patch("footprinter.mcp.resources.discoverability.get_db") as mock_get_db:
            mock_get_db.side_effect = DatabaseNotInitializedError()
            from footprinter.mcp.resources.discoverability import access_policies

            result = access_policies()
        assert isinstance(result, dict)
        assert result.get("error_code") == "DB_NOT_INITIALIZED"
