"""Navigation tools: projects, clients, folders.

Thin MCP adapters — all query/filtering logic lives in the service layer.
"""

from pathlib import Path

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.mcp.errors import mcp_error
from footprinter.services import client_service, folder_service, project_service
from footprinter.services.roles import Role

HOME = str(Path.home())


def _shorten(path: str) -> str:
    if path and path.startswith(HOME):
        return "~" + path[len(HOME) :]
    return path or ""


@handle_db_errors
def footprinter_project(project_name: str) -> dict:
    """Get project metadata, file counts, and basic stats."""
    with get_db() as conn:
        result = project_service.resolve_by_name(conn, project_name, role=Role.VIEWER)
        if result is None:
            return mcp_error("NOT_FOUND", internal_message=f"project search: {project_name}")
        if result.get("disambiguation"):
            return result
        # Shorten paths for MCP display
        if "root_path" in result:
            result["root_path"] = _shorten(result["root_path"])
        for f in result.get("folders", []):
            if "path" in f:
                f["path"] = _shorten(f["path"])
        return result


@handle_db_errors
def footprinter_client(client_name: str) -> dict:
    """Get group info with all projects and aggregate stats."""
    with get_db() as conn:
        result = client_service.resolve_by_name(conn, client_name, role=Role.VIEWER)
        if result is None:
            return mcp_error("NOT_FOUND", internal_message=f"client search: {client_name}")
        if result.get("disambiguation"):
            return result
        # Shorten paths for MCP display
        for p in result.get("projects", []):
            if "root_path" in p:
                p["root_path"] = _shorten(p["root_path"])
        return result


@handle_db_errors
def footprinter_folder(
    path: str,
    *,
    include_unlisted: bool = False,
    include_removed: bool = False,
) -> dict:
    """Get folder contents and metadata.

    ``include_unlisted`` / ``include_removed`` are ADMIN-only. VIEWER (the
    default for MCP) accepts the flags but always sees listed-only children.
    """
    if path.startswith("~"):
        path = HOME + path[1:]

    with get_db() as conn:
        result = folder_service.get_by_path(
            conn,
            path,
            role=Role.VIEWER,
            include_unlisted=include_unlisted,
            include_removed=include_removed,
        )
        if result is None:
            return mcp_error("NOT_FOUND", internal_message=f"folder: {path}")
        if "path" in result:
            result["path"] = _shorten(result["path"])
        for sf in result.get("subfolders", []):
            sf["path"] = _shorten(sf.get("path", ""))
        return result
