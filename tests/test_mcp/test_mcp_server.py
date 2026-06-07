"""Tests for MCP server construction, tool registration, and parameter schemas."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# TestModuleDocstring — 3 tests
# ---------------------------------------------------------------------------
class TestModuleDocstring:
    """Tests that the module docstring accurately describes server capabilities."""

    def test_module_docstring_exists(self):
        """server.py must have a module-level docstring."""
        import footprinter.mcp.server as mod

        assert mod.__doc__ is not None, "Module docstring is missing"

    def test_no_metadata_only_claim(self):
        """Docstring must NOT claim 'metadata-only' — the server serves full content."""
        import footprinter.mcp.server as mod

        assert "metadata-only" not in mod.__doc__.lower(), "Docstring falsely claims metadata-only access"

    def test_describes_content_access(self):
        """Docstring should mention content access, not just metadata."""
        import footprinter.mcp.server as mod

        assert "content" in mod.__doc__.lower(), "Docstring should mention content access"


# ---------------------------------------------------------------------------
# TestServerBuild — 3 tests
# ---------------------------------------------------------------------------
class TestServerBuild:
    """Tests for _build_server()."""

    def test_build_server_returns_fastmcp(self):
        """_build_server() returns a FastMCP instance."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        # FastMCP might not be installed, so check if it returned something
        if server is not None:
            from mcp.server.fastmcp import FastMCP

            assert isinstance(server, FastMCP)

    def test_build_server_name(self):
        """Server name is 'footprinter'."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        if server is not None:
            assert server.name == "footprinter"

    def test_build_server_always_returns_server(self):
        """_build_server() always returns a server (MCP is a base dep)."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        assert server is not None


# ---------------------------------------------------------------------------
# TestServerInstructions — 4 tests
# ---------------------------------------------------------------------------
class TestServerInstructions:
    """Tests that the server registers a concise instructions string for tool discovery."""

    def _get_instructions(self):
        from footprinter.mcp.server import _build_server

        server = _build_server()
        assert server is not None
        return server.instructions

    def test_server_has_instructions(self):
        """Server must register a non-None instructions string."""
        instructions = self._get_instructions()
        assert instructions is not None, "Server has no instructions string"
        assert isinstance(instructions, str)

    def test_instructions_names_core_tools(self):
        """Instructions must name all 5 core tools."""
        instructions = self._get_instructions()
        for tool in (
            "footprinter_status",
            "footprinter_search",
            "footprinter_folder",
            "footprinter_semantic",
            "footprinter_read",
        ):
            assert tool in instructions, f"Instructions missing {tool}"

    def test_instructions_search_vs_folder_disambiguation(self):
        """Instructions must clarify that search matches name tokens, not paths."""
        instructions = self._get_instructions()
        assert "name" in instructions.lower(), "Should mention name-token matching"
        assert "path" in instructions.lower(), "Should mention path resolution"

    def test_instructions_conciseness(self):
        """Instructions should be 200-800 chars — concise enough for system prompts."""
        instructions = self._get_instructions()
        length = len(instructions)
        assert length >= 200, f"Instructions too short ({length} chars)"
        assert length <= 800, f"Instructions too long ({length} chars)"


# ---------------------------------------------------------------------------
# TestToolRegistration — 3 tests
# ---------------------------------------------------------------------------
class TestToolRegistration:
    """Tests for tool registration on the server."""

    def _get_tool_names(self, server):
        """Get registered tool names from FastMCP server.

        Note: Uses private API (_tool_manager._tools) — may break
        on FastMCP upgrades.
        """
        return set(server._tool_manager._tools.keys())

    def test_all_seven_tools_registered(self):
        """Exactly 7 tools should be registered (with semantic extras)."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        if server is None:
            pytest.skip("MCP library not available")
        tools = self._get_tool_names(server)
        assert len(tools) == 7

    def test_expected_tool_names(self):
        """Tool names match the expected set."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        if server is None:
            pytest.skip("MCP library not available")

        expected = {
            "footprinter_status",
            "footprinter_search",
            "footprinter_project",
            "footprinter_client",
            "footprinter_folder",
            "footprinter_semantic",
            "footprinter_read",
        }
        actual = self._get_tool_names(server)
        assert actual == expected

    def test_semantic_not_registered_without_extras(self):
        """When semantic extras unavailable, semantic tool not registered (6 tools)."""
        with patch("footprinter.mcp.server._SEMANTIC_AVAILABLE", False):
            from footprinter.mcp.server import _build_server

            server = _build_server()
            if server is None:
                pytest.skip("MCP library not available")
            tools = self._get_tool_names(server)
            assert len(tools) == 6
            assert "footprinter_semantic" not in tools


# ---------------------------------------------------------------------------
# TestToolParameterSchemas — 7 tests
# ---------------------------------------------------------------------------
class TestToolParameterSchemas:
    """Verify required vs optional params for each tool."""

    @pytest.fixture(autouse=True)
    def _build(self):
        from footprinter.mcp.server import _build_server

        self.server = _build_server()
        if self.server is None:
            pytest.skip("MCP library not available")
        self.tools = self.server._tool_manager._tools

    def _required_params(self, tool_name):
        """Return the set of required parameter names from the JSON Schema."""
        tool = self.tools[tool_name]
        schema = tool.parameters
        return set(schema.get("required", []))

    def _all_params(self, tool_name):
        """Return all parameter names from the JSON Schema."""
        tool = self.tools[tool_name]
        schema = tool.parameters
        return set(schema.get("properties", {}).keys())

    def test_footprinter_status_no_required(self):
        assert self._required_params("footprinter_status") == set()

    def test_footprinter_search_params(self):
        required = self._required_params("footprinter_search")
        all_params = self._all_params("footprinter_search")
        # query is optional (empty = list recent items)
        for optional in (
            "query",
            "sources",
            "project",
            "client",
            "date_from",
            "date_to",
            "limit",
            "account",
            "sender",
            "days_back",
            "folder",
            "mime_type",
        ):
            assert optional in all_params
            assert optional not in required

    def test_footprinter_project_params(self):
        required = self._required_params("footprinter_project")
        assert "project_name" in required

    def test_footprinter_client_params(self):
        required = self._required_params("footprinter_client")
        assert "client_name" in required

    def test_footprinter_folder_params(self):
        required = self._required_params("footprinter_folder")
        assert "path" in required

    def test_footprinter_semantic_params(self):
        """footprinter_semantic requires query, has optional source and limit."""
        required = self._required_params("footprinter_semantic")
        assert "query" in required
        all_params = self._all_params("footprinter_semantic")
        assert "source" in all_params
        assert "source" not in required
        assert "limit" in all_params
        assert "limit" not in required

    def test_footprinter_read_params(self):
        required = self._required_params("footprinter_read")
        assert "item_type" in required
        assert "item_id" in required
        all_params = self._all_params("footprinter_read")
        assert "format" in all_params
        assert "format" not in required


# ---------------------------------------------------------------------------
# TestToolDescriptions — 3 tests
# ---------------------------------------------------------------------------
class TestToolDescriptions:
    """Verify search tool docstrings contain LLM guidance sections."""

    def test_footprinter_search_has_llm_guidance(self):
        """footprinter_search docstring has AND logic, cross-ref, and return key docs."""
        from footprinter.mcp.tools.search import footprinter_search

        doc = footprinter_search.__doc__
        assert doc is not None
        assert "AND" in doc, "Should explain AND logic for multi-word queries"
        assert "WHEN TO USE" in doc, "Should have a WHEN TO USE section"
        assert "footprinter_semantic" in doc, "Should cross-reference semantic search"
        assert "summary" in doc, "Should document the summary return key"
        assert "token" in doc.lower(), "Should explain token-based matching"
        assert "footprinter_folder" in doc, "Should route path lookups to footprinter_folder"

    def test_footprinter_semantic_has_llm_guidance(self):
        """footprinter_semantic docstring has semantic info and cross-ref."""
        from footprinter.mcp.tools.semantic import footprinter_semantic

        doc = footprinter_semantic.__doc__
        assert doc is not None
        assert "semantic" in doc.lower(), "Should explain semantic matching"
        assert "WHEN TO USE" in doc, "Should have a WHEN TO USE section"
        assert "footprinter_search" in doc, "Should cross-reference keyword search"
        assert "snippet" in doc, "Should document the snippet return key"

    def test_docstrings_are_substantial(self):
        """Search and semantic tool docstrings should be at least 400 chars."""
        from footprinter.mcp.tools.search import footprinter_search
        from footprinter.mcp.tools.semantic import footprinter_semantic

        search_len = len(footprinter_search.__doc__ or "")
        semantic_len = len(footprinter_semantic.__doc__ or "")
        assert search_len >= 400, f"footprinter_search docstring too short ({search_len} chars)"
        assert semantic_len >= 400, f"footprinter_semantic docstring too short ({semantic_len} chars)"


# ---------------------------------------------------------------------------
# TestDocsToolReferences — 2 tests
# ---------------------------------------------------------------------------
class TestDocsToolReferences:
    """Shipped docs must reference only the consolidated 7-tool surface."""

    REMOVED_TOOLS = [
        "footprinter_sources",
        "footprinter_stats",
        "footprinter_emails",
        "footprinter_drive_files",
        "footprinter_similar_chats",
        "footprinter_similar_files",
        "footprinter_classify",
        "footprinter_sensitive_in",
        "footprinter_pipeline_status",
        "footprinter_chat_duplicates",
    ]

    def test_cli_reference_no_removed_tools(self):
        """reference/interfaces.md must not reference removed tool names."""
        from pathlib import Path

        content = Path("reference/interfaces.md").read_text()
        for tool in self.REMOVED_TOOLS:
            assert tool not in content, f"reference/interfaces.md still references removed tool '{tool}'"

    def test_readme_no_removed_tools(self):
        """footprinter/mcp/README.md must not reference removed tool names."""
        from pathlib import Path

        content = Path("footprinter/mcp/README.md").read_text()
        for tool in self.REMOVED_TOOLS:
            assert tool not in content, f"footprinter/mcp/README.md still references removed tool '{tool}'"


# ---------------------------------------------------------------------------
# TestReadmeModuleStructure — 1 test
# ---------------------------------------------------------------------------
class TestReadmeModuleStructure:
    """README module structure must match actual files on disk."""

    def test_readme_references_navigation_not_entities(self):
        """README should list navigation.py, not the old entities.py."""
        from pathlib import Path

        content = Path("footprinter/mcp/README.md").read_text()
        assert "navigation.py" in content, "README should reference navigation.py"
        assert "entities.py" not in content, "README still references old entities.py (renamed to navigation.py)"


# ---------------------------------------------------------------------------
# TestResourceRegistration — MCP resources
# ---------------------------------------------------------------------------
class TestResourceRegistration:
    """Tests for resource registration on the server."""

    def _get_resource_uris(self, server):
        """Get registered resource URIs from FastMCP server.

        Note: Uses private API (_resource_manager._resources) — may break
        on FastMCP upgrades. Mirrors the approach in TestToolRegistration.
        """
        return {str(uri) for uri in server._resource_manager._resources}

    def test_at_least_one_resource_registered(self):
        """Acceptance: server registers >=1 resource."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        if server is None:
            pytest.skip("MCP library not available")
        assert len(self._get_resource_uris(server)) >= 1

    def test_expected_resource_uris(self):
        """Server ships ambient-context resources plus the discoverability surface."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        if server is None:
            pytest.skip("MCP library not available")
        expected = {
            "footprinter://context/summary",
            "footprinter://context/guidance",
            "footprinter://status",
            "footprinter://projects",
            "footprinter://access-policies",
        }
        assert self._get_resource_uris(server) == expected

    def test_tool_count_unchanged(self):
        """Adding resources must not perturb the tool registry — still 7 tools."""
        from footprinter.mcp.server import _build_server

        server = _build_server()
        if server is None:
            pytest.skip("MCP library not available")
        assert len(server._tool_manager._tools) == 7


# ---------------------------------------------------------------------------
# TestMainEntryPoint — 2 tests
# ---------------------------------------------------------------------------
class TestMainEntryPoint:
    """Tests for the main() entry point."""

    def test_main_calls_server_run(self):
        """main() should call _server.run() when MCP is available."""
        mock_server = MagicMock()

        with patch("footprinter.mcp.server._build_server", return_value=mock_server):
            # Also patch the global _server reference that main() uses
            with patch("footprinter.mcp.server._server", mock_server):
                from footprinter.mcp.server import main

                main()

        mock_server.run.assert_called_once()
