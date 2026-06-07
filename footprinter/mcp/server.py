"""Footprinter MCP server — permission-gated access to indexed data and content."""

from mcp.server.fastmcp import FastMCP

from footprinter.mcp.resources.context import SERVER_INSTRUCTIONS, context_guidance, context_summary
from footprinter.mcp.resources.discoverability import access_policies, projects_list, system_status
from footprinter.mcp.tools.navigation import footprinter_client, footprinter_folder, footprinter_project
from footprinter.mcp.tools.read import footprinter_read
from footprinter.mcp.tools.search import footprinter_search
from footprinter.mcp.tools.status import footprinter_status

try:
    from footprinter.mcp.tools.semantic import _SEMANTIC_AVAILABLE, footprinter_semantic
except ImportError:
    _SEMANTIC_AVAILABLE = False

_server = None


def _build_server():
    global _server
    _server = FastMCP("footprinter", instructions=SERVER_INSTRUCTIONS)
    _server.tool()(footprinter_status)
    _server.tool()(footprinter_search)
    _server.tool()(footprinter_project)
    _server.tool()(footprinter_client)
    _server.tool()(footprinter_folder)
    if _SEMANTIC_AVAILABLE:
        _server.tool()(footprinter_semantic)
    _server.tool()(footprinter_read)
    _server.resource("footprinter://context/summary")(context_summary)
    _server.resource("footprinter://context/guidance")(context_guidance)
    _server.resource("footprinter://status")(system_status)
    _server.resource("footprinter://projects")(projects_list)
    _server.resource("footprinter://access-policies")(access_policies)
    return _server


def main():
    """Launch the Footprinter MCP server."""
    _build_server()
    _server.run()


if __name__ == "__main__":
    main()
