"""Tests for MCP resource handler functions and the static guidance string."""

from unittest.mock import patch

from footprinter.mcp.db import DatabaseNotInitializedError


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
