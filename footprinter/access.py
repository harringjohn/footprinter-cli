"""Recalculation engine — scope-to-entity mapping + batch write-back.

Maps a policy scope (e.g. "global", "project:3", "folder:~/Work/") to affected
entity rows, calls the existing batch resolve functions, and writes resolved
values back to mcp_view / mcp_read columns.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import Any

from footprinter.db.policies import is_folder_path_scope
from footprinter.permissions import batch_resolve_permissions
from footprinter.visibility import batch_resolve_visibility

# Sources that indicate the resolution came from the global policy or the
# hardcoded baseline — not from any entity-specific or scope-specific policy.
# These entities should be stored as 'inherit' so changing the global policy
# takes effect at query time without re-running access resolution.
_INHERIT_SOURCES = frozenset({"global", "baseline"})


def _is_inherit_source(source: str) -> bool:
    """True when the resolution source traces back to global or baseline only.

    Handles both direct sources (``"global"``) and cascade paths
    (``"project:3 (via global)"``).
    """
    if source in _INHERIT_SOURCES:
        return True
    # Cascade format: "project:3 (via global)" or "folder:30 (via baseline)"
    if source.endswith(")"):
        via_idx = source.rfind("(via ")
        if via_idx != -1:
            inner = source[via_idx + 5 : -1]
            return inner in _INHERIT_SOURCES
    return False


# ---------------------------------------------------------------------------
# Entity table metadata
# ---------------------------------------------------------------------------
# Each entry describes an entity type's table and capabilities.
#   table:          SQL table name
#   has_visibility: has mcp_view column
#   has_permissions: has mcp_read column
#   has_status:     has status column (filter WHERE status = 'listed')
#   has_project_id: has project_id FK
#   has_client_id:  has client_id FK
#   has_account:    has account column
#   path_column:    column name for path-prefix matching (None if N/A)

ENTITY_META: dict[str, dict[str, Any]] = {
    "file": {
        "table": "files",
        "has_visibility": True,
        "has_permissions": True,
        "has_status": True,
        "has_project_id": True,
        "has_client_id": True,
        "has_account": True,
        "path_column": "path",
    },
    "email": {
        "table": "emails",
        "has_visibility": True,
        "has_permissions": True,
        "has_status": False,
        "has_project_id": True,
        "has_client_id": True,
        "has_account": True,
        "path_column": None,
    },
    "chat": {
        "table": "chats",
        "has_visibility": True,
        "has_permissions": True,
        "has_status": True,
        "has_project_id": True,
        "has_client_id": True,
        "has_account": True,
        "path_column": None,
    },
    "folder": {
        "table": "folders",
        "has_visibility": True,
        "has_permissions": False,
        "has_status": False,
        "has_project_id": True,
        "has_client_id": True,
        "has_account": False,
        "path_column": "path",
    },
    "project": {
        "table": "projects",
        "has_visibility": True,
        "has_permissions": True,
        "has_status": False,
        "has_project_id": False,
        "has_client_id": True,
        "has_account": False,
        "path_column": "root_path",
    },
    "client": {
        "table": "clients",
        "has_visibility": True,
        "has_permissions": True,
        "has_status": False,
        "has_project_id": False,
        "has_client_id": False,
        "has_account": False,
        "path_column": None,
    },
    "visit": {
        "table": "visits",
        "has_visibility": True,
        "has_permissions": True,
        "has_status": False,
        "has_project_id": False,
        "has_client_id": False,
        "has_account": False,
        "path_column": None,
    },
}

# Reverse map: source scope suffix → entity type (e.g. "files" → "file")
_SOURCE_TO_ENTITY = {meta["table"]: etype for etype, meta in ENTITY_META.items()}
_SOURCE_TO_ENTITY["browser"] = "visit"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_all_ids(conn: sqlite3.Connection, entity_type: str) -> list[int]:
    """Get all active IDs for an entity type."""
    meta = ENTITY_META[entity_type]
    table = meta["table"]
    if meta["has_status"]:
        rows = conn.execute(f"SELECT id FROM {table} WHERE status = 'listed'").fetchall()
    else:
        rows = conn.execute(f"SELECT id FROM {table}").fetchall()
    return [r["id"] for r in rows]


def _get_ids_for_scope(conn: sqlite3.Connection, scope: str) -> dict[str, list[int]]:
    """Map a policy scope to {entity_type: [ids]} affected by it."""
    if scope == "global":
        return {etype: _get_all_ids(conn, etype) for etype in ENTITY_META}

    if ":" not in scope:
        raise ValueError(f"Invalid scope: {scope}")

    prefix, value = scope.split(":", 1)

    if prefix == "source":
        # source:files → all files; source:emails → all emails
        entity_type = _SOURCE_TO_ENTITY.get(value)
        if entity_type is None:
            raise ValueError(f"Unknown source scope: {scope}")
        return {entity_type: _get_all_ids(conn, entity_type)}

    if prefix == "account":
        # account:{name} → emails + chats + files WHERE account = ?
        result: dict[str, list[int]] = {}
        for etype in ENTITY_META:
            meta = ENTITY_META[etype]
            if not meta["has_account"]:
                continue
            table = meta["table"]
            where = "account = ?"
            if meta["has_status"]:
                where += " AND status = 'listed'"
            rows = conn.execute(f"SELECT id FROM {table} WHERE {where}", (value,)).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                result[etype] = ids
        return result

    if prefix == "folder":
        if is_folder_path_scope(scope):
            # folder:{path} → files/folders with matching path prefix
            path = os.path.expanduser(value)
            escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            result = {}
            for etype in ENTITY_META:
                meta = ENTITY_META[etype]
                path_col = meta["path_column"]
                if path_col is None:
                    continue
                table = meta["table"]
                where = f"{path_col} LIKE ? ESCAPE '\\'"
                if meta["has_status"]:
                    where += " AND status = 'listed'"
                rows = conn.execute(
                    f"SELECT id FROM {table} WHERE {where}",
                    (escaped + "%",),
                ).fetchall()
                ids = [r["id"] for r in rows]
                if ids:
                    result[etype] = ids
            return result
        else:
            # folder:{id} → folder + all descendants via parent_folder_id
            folder_id = int(value)
            descendants_cte = """
                WITH RECURSIVE descendants(id) AS (
                    SELECT id FROM folders WHERE id = ?
                    UNION ALL
                    SELECT folder.id FROM folders folder
                    JOIN descendants descendant ON folder.parent_folder_id = descendant.id
                )
            """
            cursor = conn.cursor()
            cursor.execute(
                f"{descendants_cte} SELECT id FROM descendants",
                (folder_id,),
            )
            desc_ids = [row["id"] for row in cursor.fetchall()]
            if not desc_ids:
                return {}
            result: dict[str, list[int]] = {"folder": desc_ids}
            ph = ",".join("?" * len(desc_ids))
            file_rows = conn.execute(
                f"SELECT id FROM files WHERE folder_id IN ({ph}) AND status = 'listed'",
                desc_ids,
            ).fetchall()
            file_ids = [r["id"] for r in file_rows]
            if file_ids:
                result["file"] = file_ids
            return result

    if prefix == "project":
        project_id = int(value)
        result = {}
        # The project itself
        row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row:
            result["project"] = [row["id"]]
        # Children with project_id FK
        for etype in ENTITY_META:
            if etype == "project":
                continue
            meta = ENTITY_META[etype]
            if not meta["has_project_id"]:
                continue
            table = meta["table"]
            where = "project_id = ?"
            if meta["has_status"]:
                where += " AND status = 'listed'"
            rows = conn.execute(f"SELECT id FROM {table} WHERE {where}", (project_id,)).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                result[etype] = ids
        return result

    if prefix == "client":
        client_id = int(value)
        # Gather ids per entity type as dicts (insertion-ordered sets) so we
        # can union the project cascade with direct client_id matches without
        # double-stamping entities reachable via both paths.
        id_sets: dict[str, dict[int, None]] = {}
        # The client itself
        row = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
        if row:
            id_sets["client"] = {row["id"]: None}
        # Projects under this client
        proj_rows = conn.execute("SELECT id FROM projects WHERE client_id = ?", (client_id,)).fetchall()
        proj_ids = [r["id"] for r in proj_rows]
        if proj_ids:
            id_sets["project"] = {pid: None for pid in proj_ids}
        # Cascade: children of each project
        for pid in proj_ids:
            for etype, ids in _get_ids_for_scope(conn, f"project:{pid}").items():
                if etype in ("project", "client"):
                    continue
                id_sets.setdefault(etype, {}).update({i: None for i in ids})
        # Direct: entities with a client_id FK of their own (files, folders,
        # emails, chats). Union with the cascade; dedup via the dict keys.
        for etype, meta in ENTITY_META.items():
            if etype in ("client", "project"):
                continue
            if not meta["has_client_id"]:
                continue
            table = meta["table"]
            where = "client_id = ?"
            if meta["has_status"]:
                where += " AND status = 'listed'"
            rows = conn.execute(f"SELECT id FROM {table} WHERE {where}", (client_id,)).fetchall()
            if rows:
                id_sets.setdefault(etype, {}).update({r["id"]: None for r in rows})
        return {etype: list(ids) for etype, ids in id_sets.items()}

    # Single entity: file:42, email:10, etc.
    if prefix in ENTITY_META:
        entity_id = int(value)
        return {prefix: [entity_id]}

    raise ValueError(f"Unknown scope prefix: {prefix}")


def _write_back_visibility(conn: sqlite3.Connection, entity_type: str, results: dict[int, tuple]) -> None:
    """Batch UPDATE mcp_view from resolve results.

    Entities whose visibility comes from the global policy or the hardcoded
    baseline are written as ``'inherit'`` — the MCP layer resolves them at
    query time.  Entities with a specific policy get the resolved value.
    """
    table = ENTITY_META[entity_type]["table"]
    conn.executemany(
        f"UPDATE {table} SET mcp_view = ? WHERE id = ?",
        [("inherit" if _is_inherit_source(source) else state, eid) for eid, (state, source) in results.items()],
    )


def _write_back_permissions(conn: sqlite3.Connection, entity_type: str, results: dict[int, tuple]) -> None:
    """Batch UPDATE mcp_read from resolve results.

    Entities whose permission comes from the global policy or the hardcoded
    baseline are written as ``'inherit'`` — the MCP layer resolves them at
    query time.  Entities with a specific policy get the resolved value.
    """
    table = ENTITY_META[entity_type]["table"]
    conn.executemany(
        f"UPDATE {table} SET mcp_read = ? WHERE id = ?",
        [
            ("inherit" if _is_inherit_source(source) else ("allow" if allowed else "deny"), eid)
            for eid, (allowed, source) in results.items()
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def count_affected_entities(conn: sqlite3.Connection, scope: str) -> dict[str, int]:
    """Count entities affected by *scope* without modifying them.

    Returns:
        Dict mapping entity type to count of affected rows.
        Only includes types with count > 0.
    """
    return {etype: len(ids) for etype, ids in _get_ids_for_scope(conn, scope).items() if ids}


def stamp_entities(conn: sqlite3.Connection, ids_by_type: dict[str, list[int]]) -> dict[str, int]:
    """Resolve and write visibility + permissions for the given entity IDs.

    Used by ``recalculate_access`` (full scope resolution) and the incremental
    pipeline path in ``processing.run_access_resolution``.  The batched variant
    (``recalculate_access_batched``) uses its own loop for per-chunk commits.

    Always commits before returning, even when *ids_by_type* is empty.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        ids_by_type: Mapping of entity type to list of row IDs to stamp.

    Returns:
        Dict mapping entity type to count of rows stamped.
        Only includes types with count > 0.
    """
    stats: dict[str, int] = {}

    for entity_type, ids in ids_by_type.items():
        if not ids:
            continue
        meta = ENTITY_META[entity_type]

        if meta["has_visibility"]:
            vis_results = batch_resolve_visibility(conn, entity_type, ids)
            _write_back_visibility(conn, entity_type, vis_results)

        if meta["has_permissions"]:
            perm_results = batch_resolve_permissions(conn, entity_type, ids)
            _write_back_permissions(conn, entity_type, perm_results)

        stats[entity_type] = len(ids)

    conn.commit()
    return stats


def recalculate_access(conn: sqlite3.Connection, scope: str) -> dict[str, int]:
    """Recalculate visibility and permissions for all entities affected by *scope*.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        scope: Policy scope string (e.g. "global", "project:3", "folder:~/Work/")

    Returns:
        Dict mapping entity type to count of rows updated.
    """
    ids_by_type = _get_ids_for_scope(conn, scope)
    return stamp_entities(conn, ids_by_type)


def recalculate_access_batched(
    conn: sqlite3.Connection,
    scope: str,
    *,
    batch_size: int = 5000,
    on_batch: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Recalculate visibility and permissions in batches with progress callback.

    Same semantics as ``recalculate_access()`` but commits after each batch
    and calls *on_batch* with the count of entities processed per chunk.
    Designed for large scopes where a progress bar is needed.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        scope: Policy scope string (e.g. "global", "folder:~/Work/")
        batch_size: Number of entity IDs per chunk (default 5000)
        on_batch: Optional callback receiving the count processed per chunk

    Returns:
        Dict mapping entity type to total count of rows updated.
    """
    ids_by_type = _get_ids_for_scope(conn, scope)
    stats: dict[str, int] = {}

    for entity_type, ids in ids_by_type.items():
        if not ids:
            continue
        meta = ENTITY_META[entity_type]

        for i in range(0, len(ids), batch_size):
            chunk = ids[i : i + batch_size]

            if meta["has_visibility"]:
                vis_results = batch_resolve_visibility(conn, entity_type, chunk)
                _write_back_visibility(conn, entity_type, vis_results)

            if meta["has_permissions"]:
                perm_results = batch_resolve_permissions(conn, entity_type, chunk)
                _write_back_permissions(conn, entity_type, perm_results)

            conn.commit()

            if on_batch is not None:
                on_batch(len(chunk))

        stats[entity_type] = len(ids)

    return stats


def recalculate_entity(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> dict[str, int]:
    """Recalculate visibility and permissions for a single entity.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        entity_type: Entity type (e.g. "file", "email")
        entity_id: Row ID

    Returns:
        Dict like {"file": 1}, or {"file": 0} if entity not found.
    """
    if entity_type not in ENTITY_META:
        raise ValueError(f"Unknown entity type: {entity_type}")

    meta = ENTITY_META[entity_type]
    # Verify the entity exists before resolving
    table = meta["table"]
    row = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return {entity_type: 0}

    if meta["has_visibility"]:
        vis_results = batch_resolve_visibility(conn, entity_type, [entity_id])
        _write_back_visibility(conn, entity_type, vis_results)

    if meta["has_permissions"]:
        perm_results = batch_resolve_permissions(conn, entity_type, [entity_id])
        _write_back_permissions(conn, entity_type, perm_results)

    conn.commit()
    return {entity_type: 1}
