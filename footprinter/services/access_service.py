"""access_service — access gating, visibility filtering, and permission resolution.

Combines the former ``read_service`` (3-stage gating) and ``visibility``
(list filtering, inherit resolution, opaque field sets) into one module.

Gating stages (for non-ADMIN roles):
  1. Existence — item must exist in DB
  2. Visibility — ``mcp_view`` must not be hidden/opaque
  3. Permission — ``mcp_read`` must not be deny

Visibility values: 'hidden' -> exclude, 'opaque' -> minimal fields,
'visible' -> full.  'inherit' -> resolves to the global policy at query
time (loaded by ``load_globals``).  Missing (None) -> treated as 'opaque'
(fail-closed).

Two-visibility-system: access_service reads the mcp_view and mcp_read
values that the visibility pipeline computed at ingest time. It does not
recompute visibility — it only gates access against pre-resolved columns.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from footprinter.db.chats import get_chat_detail
from footprinter.db.emails import get_email
from footprinter.db.files import get_file
from footprinter.services.roles import Role

logger = logging.getLogger(__name__)

__all__ = [
    # Gating
    "gate_access",
    "VALID_TYPES",
    # Visibility resolution
    "load_globals",
    "resolve_inherit_visibility",
    "resolve_inherit_permission",
    "has_global_permission",
    "is_global_policy_loaded",
    # List filtering
    "filter_result",
    "filter_results_list",
    "strip_content_for_denied",
    "get_opaque_metadata",
    # Opaque field sets
    "OPAQUE_FILE_FIELDS",
    "OPAQUE_EMAIL_FIELDS",
    "OPAQUE_CHAT_FIELDS",
    "OPAQUE_FOLDER_FIELDS",
    "OPAQUE_BROWSER_FIELDS",
    "OPAQUE_PROJECT_FIELDS",
    "OPAQUE_CLIENT_FIELDS",
    "_read_visibility",
    "_filter_to_opaque",
    "_CONTENT_FIELDS",
]

VALID_TYPES = frozenset({"file", "email", "chat"})

# ---------------------------------------------------------------------------
# Global policy cache — refreshed per-request by load_globals()
# ---------------------------------------------------------------------------

_global_visibility: Optional[str] = None
_global_permission: Optional[str] = None


def load_globals(conn: sqlite3.Connection) -> None:
    """Read global visibility and permission policies and cache them.

    Called once per request — from the MCP layer's ``get_db()`` and
    the CLI layer's ``open_db()``.  Two PK lookups.

    Tolerates missing policy tables (e.g. test databases with partial
    schemas) by leaving the cache as ``None`` (baseline fallback).
    """
    global _global_visibility, _global_permission

    try:
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        _global_visibility = row["setting"] if row else None
    except sqlite3.OperationalError:
        _global_visibility = None

    try:
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        _global_permission = row["setting"] if row else None
    except sqlite3.OperationalError:
        _global_permission = None


def has_global_permission() -> bool:
    """Return whether the cached global permission is 'allow'.

    Public replacement for direct ``_global_permission`` access.
    """
    return _global_permission == "allow"


def is_global_policy_loaded() -> bool:
    """Return whether a global permission policy has been loaded.

    Unlike ``has_global_permission()`` which checks if the policy is
    specifically ``'allow'``, this checks whether *any* policy exists.
    """
    return _global_permission is not None


# ---------------------------------------------------------------------------
# Inherit resolution
# ---------------------------------------------------------------------------


def resolve_inherit_visibility(value: Optional[str]) -> str:
    """Resolve a visibility value, mapping ``inherit`` to the global policy.

    - ``None`` -> ``'opaque'`` (fail-closed: truly missing data)
    - ``'inherit'`` -> cached global visibility, or ``'opaque'`` baseline
    - Explicit values (``'hidden'``, ``'opaque'``, ``'visible'``) pass through
    """
    if value is None:
        return "opaque"
    if value == "inherit":
        return _global_visibility or "opaque"
    return value


def resolve_inherit_permission(value: Optional[str]) -> str:
    """Resolve a permission value, mapping ``inherit`` to the global policy.

    - ``None`` -> ``'deny'`` (fail-closed: truly missing data)
    - ``'inherit'`` -> cached global permission, or ``'allow'`` baseline
      (``BASELINE_PERMISSION = True`` in permissions.py)
    - Explicit values (``'allow'``, ``'deny'``) pass through
    """
    if value is None:
        return "deny"
    if value == "inherit":
        return _global_permission or "allow"
    return value


# ---------------------------------------------------------------------------
# Opaque field sets
# ---------------------------------------------------------------------------

OPAQUE_FILE_FIELDS = {"id", "content_type", "source", "project_id"}
OPAQUE_EMAIL_FIELDS = {"id", "account", "project_id", "client_id"}
OPAQUE_CHAT_FIELDS = {"id", "account", "project_id", "client_id"}
OPAQUE_FOLDER_FIELDS = {"id", "direct_files", "direct_file_count", "source", "project_id"}
OPAQUE_BROWSER_FIELDS = {"id", "browser", "project_id"}
OPAQUE_PROJECT_FIELDS = {"id", "type", "project_type", "status", "client_id"}
OPAQUE_CLIENT_FIELDS = {"id", "client_type", "status"}


# ---------------------------------------------------------------------------
# List filtering
# ---------------------------------------------------------------------------


def _read_visibility(result: Dict[str, Any]) -> str:
    """Read mcp_view from a result dict, resolving ``inherit`` via global policy."""
    return resolve_inherit_visibility(result.get("mcp_view"))


def filter_result(item_type: str, full_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Filter a single result dict based on its ``mcp_view`` value.

    Returns None if hidden, minimal dict if opaque, full dict if visible.
    """
    visibility = _read_visibility(full_result)

    if visibility == "hidden":
        return None

    if visibility == "opaque":
        return _filter_to_opaque(item_type, full_result)

    return full_result  # visible


def filter_results_list(
    item_type: str, results: List[Dict[str, Any]], id_key: str = "id"
) -> Tuple[List[Dict[str, Any]], int]:
    """Filter a list of results, returning filtered list and suppressed count.

    Reads ``mcp_view`` from each result dict instead of querying the database.
    """
    filtered = []
    suppressed = 0

    for result in results:
        visibility = _read_visibility(result)

        if visibility == "hidden":
            suppressed += 1
            continue

        if visibility == "opaque":
            filtered.append(_filter_to_opaque(item_type, result))
        else:
            filtered.append(result)

    return filtered, suppressed


def _filter_to_opaque(item_type: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Filter a result dict to only include opaque-allowed fields."""
    if item_type == "file":
        allowed = OPAQUE_FILE_FIELDS
    elif item_type == "email":
        allowed = OPAQUE_EMAIL_FIELDS
    elif item_type == "chat":
        allowed = OPAQUE_CHAT_FIELDS
    elif item_type == "folder":
        allowed = OPAQUE_FOLDER_FIELDS
    elif item_type == "visit":
        allowed = OPAQUE_BROWSER_FIELDS
    elif item_type == "project":
        allowed = OPAQUE_PROJECT_FIELDS
    elif item_type == "client":
        allowed = OPAQUE_CLIENT_FIELDS
    else:
        allowed = {"id"}

    return {k: v for k, v in result.items() if k in allowed}


# Content fields that listing tools must strip when mcp_read != 'allow'
_CONTENT_FIELDS: Dict[str, List[str]] = {
    "chat": ["snippet"],
    "email": ["snippet"],
    "file": ["snippet"],
}


def strip_content_for_denied(item_type: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip content fields from results where resolved permission is not 'allow'.

    Uses ``resolve_inherit_permission`` so that ``inherit`` resolves to the
    global policy (or baseline ``'allow'`` when no global policy is loaded).
    ``NULL`` / missing values fail closed to ``'deny'``.  Items are NOT
    removed — only content keys are deleted, preserving the "you matched
    something" signal.
    """
    fields = _CONTENT_FIELDS.get(item_type, [])
    if not fields:
        return results

    for result in results:
        if resolve_inherit_permission(result.get("mcp_read")) != "allow":
            for field in fields:
                result.pop(field, None)
    return results


def get_opaque_metadata(conn: sqlite3.Connection, item_type: str, item_id: int) -> Dict[str, Any]:
    """Get opaque metadata for an item, filtered to opaque-allowed fields.

    Used by footprinter_read when returning visibility-restricted errors.
    Delegates to db/ layer for the fetch, then filters to opaque fields.
    """
    if item_type == "file":
        row = get_file(conn, item_id)
    elif item_type == "email":
        row = get_email(conn, item_id)
    elif item_type == "chat":
        row = get_chat_detail(conn, item_id)
    else:
        return {}

    if not row:
        return {}
    return _filter_to_opaque(item_type, row)


# ---------------------------------------------------------------------------
# 4-stage access gating
# ---------------------------------------------------------------------------


def gate_access(
    conn: sqlite3.Connection,
    item_type: str,
    item_id: int,
    *,
    role: Role = Role.ADMIN,
) -> dict:
    """4-stage access gating + content for a single item.

    Returns dict with ``status`` key:

    - ``ok`` — access granted; includes ``metadata`` (+ ``content`` for email/chat)
    - ``removed`` — item has ``status='removed'`` (VIEWER only)
    - ``unlisted`` — item has ``status='unlisted'`` (VIEWER only)
    - ``hidden`` — item hidden from this role
    - ``opaque`` — minimal ``metadata`` only
    - ``denied`` — permission denied
    - ``not_found`` — item doesn't exist
    - ``invalid_type`` — unrecognised item_type
    """
    if item_type not in VALID_TYPES:
        return {"status": "invalid_type"}

    # Stage 1: Existence — fetch full detail via db/ layer
    if item_type == "file":
        metadata = get_file(conn, item_id)
    elif item_type == "email":
        metadata = get_email(conn, item_id)
    else:
        metadata = get_chat_detail(conn, item_id)

    if not metadata:
        return {"status": "not_found"}

    # Stage 2: Status (ADMIN bypasses; status rides along in metadata)
    if not role.sees_all:
        row_status = metadata.get("status")
        if row_status == "removed":
            return {"status": "removed"}
        if row_status == "unlisted":
            return {"status": "unlisted"}

    # Stage 3: Visibility (ADMIN bypasses)
    if not role.sees_all:
        visibility = resolve_inherit_visibility(metadata.get("mcp_view"))
        if visibility == "hidden":
            return {"status": "hidden"}
        if visibility == "opaque":
            return {
                "status": "opaque",
                "metadata": _filter_to_opaque(item_type, metadata),
            }

    # Stage 4: Permission (ADMIN bypasses)
    if not role.sees_all:
        if resolve_inherit_permission(metadata.get("mcp_read")) == "deny":
            return {
                "status": "denied",
                "metadata": _filter_to_opaque(item_type, metadata),
            }

    # Stage 5: Return content — reuse metadata already fetched
    if item_type == "file":
        return {"status": "ok", "metadata": metadata}
    elif item_type == "email":
        content = metadata.pop("body_preview", "") or ""
        return {"status": "ok", "metadata": metadata, "content": content}
    else:
        messages = metadata.pop("messages", [])
        summary = metadata.pop("summary", None) or ""
        content_parts = []
        if summary:
            content_parts.append(f"Summary: {summary}")
        for msg in messages:
            role_name = msg.get("role") or "unknown"
            text = msg.get("content") or ""
            timestamp = msg.get("created_at") or ""
            if timestamp:
                content_parts.append(f"[{timestamp}] {role_name}: {text}")
            else:
                content_parts.append(f"{role_name}: {text}")
        content = "\n\n".join(content_parts)
        return {"status": "ok", "metadata": metadata, "content": content}
