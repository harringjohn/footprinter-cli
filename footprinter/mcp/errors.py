"""Standardized MCP error responses.

Provides consistent error handling that:
- Logs detailed info internally (paths, IDs, exception details)
- Returns generic messages externally (closes information oracles)
- Enforces consistent response structure
"""

import logging

logger = logging.getLogger(__name__)

# Error codes mapped to user-facing messages (intentionally vague)
ERROR_MESSAGES = {
    "NOT_FOUND": "Nothing here",
    "VISIBILITY_RESTRICTED": "Veiled",
    "PERMISSION_DENIED": "Forbidden",
    "INVALID_TYPE": "Unknown kind",
    "INVALID_INPUT": "Unclear",
    "MISSING_REQUIRED": "Incomplete",
    "READ_FAILED": "Illegible",
    "EXTRACTION_FAILED": "Resists extraction",
    "DECODE_FAILED": "Indecipherable",
    "CONFIG_ERROR": "Unconfigured",
    "DEPENDENCY_MISSING": "Unequipped",
    "DATABASE_ERROR": "Unreachable",
    "DB_NOT_INITIALIZED": "Unpopulated",
    "SEARCH_FAILED": "Fruitless",
    "QUERY_INVALID": "Too brief",
}

# Agent-friendly hints paired with each error code.
# Personality stays in ERROR_MESSAGES; these give actionable next steps.
ERROR_HINTS = {
    "NOT_FOUND": "Check the ID or type and retry",
    "VISIBILITY_RESTRICTED": "This item's metadata is included — content requires a visibility change",
    "PERMISSION_DENIED": "Access policy blocks this item — request a permission change",
    "INVALID_TYPE": "Check the type parameter — see tool description for valid values",
    "INVALID_INPUT": "Review the required parameters and retry",
    "MISSING_REQUIRED": "A required parameter is missing — check the tool schema",
    "READ_FAILED": "The item exists but could not be read — retry or try a different format",
    "EXTRACTION_FAILED": "Text extraction failed — retry with format='raw'",
    "DECODE_FAILED": "Content is not valid text — this may be a binary item",
    "CONFIG_ERROR": "A required service is not configured — check setup",
    "DEPENDENCY_MISSING": "A required dependency is not installed",
    "DATABASE_ERROR": "Storage is temporarily unavailable — retry shortly",
    "DB_NOT_INITIALIZED": "No data indexed yet — run 'fp ingest' to populate",
    "SEARCH_FAILED": "Search could not complete — try a different query or retry",
    "QUERY_INVALID": "Query is too short — provide at least a few words",
}


def mcp_error(
    code: str,
    *,
    detail: str = None,
    metadata: dict = None,
    hint: str = None,
    internal_message: str = None,
    level: str = "warning",
) -> dict:
    """Create a standardized MCP error response.

    Args:
        code: Error code from ERROR_MESSAGES (e.g., "NOT_FOUND", "INVALID_TYPE")
        detail: Override default message (use sparingly - may leak info)
        metadata: Pre-filtered metadata to include in response
        hint: Override default hint (actionable guidance for agents)
        internal_message: Logged only, never exposed to client
        level: Logging level ("debug", "info", "warning", "error")

    Returns:
        Dict with 'error', 'error_code', and optionally 'metadata' and 'hint'

    Example:
        >>> mcp_error("NOT_FOUND", internal_message=f"file {id} missing")
        {"error": "Nothing here", "error_code": "NOT_FOUND",
         "hint": "Check the ID or type and retry"}

        >>> mcp_error("NOT_FOUND", hint="Try searching by name instead")
        {"error": "Nothing here", "error_code": "NOT_FOUND",
         "hint": "Try searching by name instead"}
    """
    # Get user-facing message (or use detail override)
    message = detail if detail else ERROR_MESSAGES.get(code, "Error")

    # Log internal details (never exposed)
    if internal_message:
        log_func = getattr(logger, level, logger.warning)
        log_func(f"[{code}] {internal_message}")

    # Build response
    result = {"error": message, "error_code": code}
    if metadata:
        result["metadata"] = metadata

    # Resolve hint: explicit override > default lookup > omit
    resolved_hint = hint if hint else ERROR_HINTS.get(code)
    if resolved_hint:
        result["hint"] = resolved_hint

    return result
