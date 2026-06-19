"""Navigation tools: projects, clients, folders.

Thin MCP adapters — all query/filtering logic lives in the service layer.
"""

import os

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.mcp.errors import mcp_error
from footprinter.services import client_service, folder_service, project_service
from footprinter.services.roles import Role
from footprinter.utils.paths import abbreviate_home as _shorten


def _shorten_curated_context(result: dict) -> None:
    """Abbreviate ``$HOME`` in the curated-context pointer, like sibling paths.

    The curated-context block carries a fully-resolved absolute ``context_path``;
    abbreviate it to ``~`` for VIEWER display so it matches the folder/subfolder
    paths in the same payload (which are already run through ``_shorten``).
    """
    block = result.get("curated_context")
    if block and "context_path" in block:
        block["context_path"] = _shorten(block["context_path"])


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
        for f in result.get("folders", []):
            if "path" in f:
                f["path"] = _shorten(f["path"])
        _shorten_curated_context(result)
        return result


@handle_db_errors
def footprinter_client(client_name: str) -> dict:
    """Get group info with all projects and aggregate stats."""
    with get_db() as conn:
        result = client_service.resolve_by_name(conn, client_name, role=Role.VIEWER)
        if result is None:
            return mcp_error("NOT_FOUND", internal_message=f"client search: {client_name}")
        _shorten_curated_context(result)
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
        path = os.path.expanduser("~") + path[1:]

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
        _shorten_curated_context(result)
        return result
