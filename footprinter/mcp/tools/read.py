"""Read tool: fetch content for permitted items."""

from typing import Literal

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.mcp.errors import mcp_error
from footprinter.services import access_service, content_service
from footprinter.services.roles import Role

_IDENTITY_FIELDS = {
    "file": ("name", "path", "source", "created_at", "modified_at", "project_name"),
    "email": ("subject", "from_address", "from_name", "account", "received_at", "project_name"),
    "chat": ("title", "account", "created_at", "project_name"),
}


def _with_identity(item_type: str, content: str, metadata: dict) -> dict:
    identity = {k: metadata.get(k) for k in _IDENTITY_FIELDS.get(item_type, ())}
    return {**identity, "content": content, "metadata": metadata}


# Map service status codes to MCP error codes
_STATUS_TO_MCP = {
    "hidden": "NOT_FOUND",
    "removed": "NOT_FOUND",
    "unlisted": "NOT_FOUND",
    "not_found": "NOT_FOUND",
    "opaque": "VISIBILITY_RESTRICTED",
    "denied": "PERMISSION_DENIED",
    "invalid_type": "INVALID_TYPE",
    "read_failed": "READ_FAILED",
    "decode_failed": "DECODE_FAILED",
    "extraction_failed": "EXTRACTION_FAILED",
}


@handle_db_errors
def footprinter_read(
    item_type: str,
    item_id: int,
    format: Literal["text", "raw"] = "text",
    source: Literal["disk", "vectors"] = "disk",
    query: str = "",
) -> dict:
    """Read content for a file, email, or chat if the MCP client has permission.

    Args:
        item_type: 'file', 'email', or 'chat'
        item_id: Row ID of the item.
        format: 'text' (default) extracts plaintext from documents,
                'raw' returns raw decoded content without extraction.
        source: 'disk' (default) reads file content from disk; 'vectors' (files
                only) reassembles the matched chunk + neighbors straight from the
                indexed vectors — a far smaller token payload, usable where the
                disk read truncates. Requires ``query`` to locate the chunk(s).
        query: Query used to rank a file's chunks when ``source='vectors'``.

    Returns:
        Dict with per-type identity fields, 'content', and 'metadata' on success,
        or 'error', 'error_code', and 'metadata' on denial/failure.
    """
    with get_db() as conn:
        result = access_service.gate_access(
            conn,
            item_type,
            item_id,
            role=Role.VIEWER,
        )
        status = result.get("status")

        if status != "ok":
            return mcp_error(
                _STATUS_TO_MCP.get(status, "READ_FAILED"),
                metadata=result.get("metadata"),
                internal_message=result.get("message"),
            )

        # Email/chat: content already in result
        if item_type != "file":
            return _with_identity(item_type, result.get("content", ""), result["metadata"])

        # File: read content via service I/O. The D2 gate above runs first for
        # both paths — the vector branch only executes after gate_access == ok.
        if source == "vectors":
            file_result = content_service.read_file_from_vectors(
                conn, result["metadata"], query
            )
        else:
            file_result = content_service.read_file(conn, result["metadata"], format=format)
        if file_result.get("status") != "ok":
            return mcp_error(
                _STATUS_TO_MCP.get(file_result["status"], "READ_FAILED"),
                metadata=file_result.get("metadata", result["metadata"]),
                internal_message=file_result.get("message"),
            )
        return _with_identity(item_type, file_result["content"], file_result["metadata"])
