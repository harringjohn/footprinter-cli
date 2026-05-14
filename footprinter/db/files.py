"""File queries and write operations.

Provides list, detail, status-update, and insert functions for indexed files.
All functions take a raw ``sqlite3.Connection`` and return plain dicts.
"""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response

VALID_FILE_STATUSES = frozenset({"listed", "unlisted", "removed"})


def list_files(
    conn: sqlite3.Connection,
    *,
    project_id: Optional[int] = None,
    source: Optional[list[str]] = None,
    status: Optional[str | list[str]] = None,
    content_type: Optional[str] = None,
    limit: int = 50,
    page: int = 1,
) -> dict:
    """List files with optional filters and pagination.

    Parameters
    ----------
    conn : sqlite3.Connection
    project_id : int, optional
        Filter to a single project.
    source : list[str], optional
        Filter by source names (e.g. ``["local"]``, ``["workdrive"]``).
    status : str, list[str], or None
        ``None`` → exclude removed (default).
        ``"all"`` → no status filter.
        Single string → exact match (``"listed"``, ``"unlisted"``, ``"removed"``).
        List of strings → ``WHERE status IN (...)``.
    content_type : str, optional
        Exact match on ``files.content_type``.
    limit, page : int
        Pagination.

    Returns
    -------
    dict
        ``{"files": [...], "pagination": {page, limit, total, total_pages}}``
    """
    base = """
        SELECT file.id, file.name, file.path, file.source, file.status, file.content_type,
               file.size_bytes, file.modified_at, project.project_name,
               file.mcp_view, file.mcp_read
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
    """
    conditions: list[str] = []
    params: list = []

    # Status filter
    status_conds, status_params = build_status_filter(
        status,
        column="file.status",
        default_exclude=["removed"],
    )
    conditions.extend(status_conds)
    params.extend(status_params)

    if project_id is not None:
        conditions.append("file.project_id = ?")
        params.append(project_id)

    if source:
        placeholders = ",".join("?" * len(source))
        conditions.append(f"file.source IN ({placeholders})")
        params.extend(source)

    if content_type:
        conditions.append("file.content_type = ?")
        params.append(content_type)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = base + where

    count_sql = f"SELECT COUNT(*) FROM ({query}) _c"
    fetch_sql = query + " ORDER BY file.id LIMIT ? OFFSET ?"
    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    files = [
        {
            "id": r["id"],
            "name": r["name"],
            "path": r["path"],
            "source": r["source"],
            "status": r["status"],
            "content_type": r["content_type"] or "",
            "size_bytes": r["size_bytes"],
            "modified_at": r["modified_at"],
            "project_name": r["project_name"] or "",
            "mcp_view": r["mcp_view"],
            "mcp_read": r["mcp_read"],
        }
        for r in rows
    ]

    return paginated_response("files", files, pagination)


def get_file(
    conn: sqlite3.Connection,
    file_id: int,
) -> Optional[dict]:
    """Return full detail for a single file, or None if not found.

    Joins ``projects`` for project_name.
    """
    row = conn.execute(
        """
        SELECT file.id, file.name, file.path, file.source, file.status, file.status_reason,
               file.content_type, file.mime_type, file.size_bytes, file.created_at,
               file.modified_at, file.indexed_at, file.project_id, file.md5_hash,
               file.external_id, file.account,
               file.mcp_view, file.mcp_read,
               project.project_name
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id = ?
        """,
        (file_id,),
    ).fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "name": row["name"],
        "path": row["path"],
        "source": row["source"],
        "status": row["status"],
        "status_reason": row["status_reason"],
        "content_type": row["content_type"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
        "modified_at": row["modified_at"],
        "indexed_at": row["indexed_at"],
        "project_id": row["project_id"],
        "md5_hash": row["md5_hash"],
        "external_id": row["external_id"],
        "account": row["account"],
        "project_name": row["project_name"],
        "mcp_view": row["mcp_view"] or "inherit",
        "mcp_read": row["mcp_read"] or "inherit",
    }


def update_file_status(
    conn: sqlite3.Connection,
    file_id: int,
    status: str,
    reason: Optional[str] = None,
) -> Optional[bool]:
    """Change a file's status.

    Returns True on success, None if not found.
    Raises ValueError for invalid status values.
    """
    if status not in VALID_FILE_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_FILE_STATUSES))}")

    cursor = conn.execute("SELECT id FROM files WHERE id = ?", (file_id,))
    if cursor.fetchone() is None:
        return None

    conn.execute(
        """
        UPDATE files
        SET status = ?, status_reason = ?, status_changed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, reason, file_id),
    )
    conn.commit()
    return True


def update_file_relationships(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    project_id: Optional[int] = None,
    client_id: Optional[int] = None,
) -> Optional[bool]:
    """Update project and/or client assignment on a file.

    Only updates fields that are passed (not None). Pass ``0`` to clear
    a field (set to NULL). Stamps ``assignment_source = 'user'``
    when the column exists (app-scope DBs only), so auto-detection
    won't overwrite manual assignments.
    Returns True on success, None if file not found.
    """
    cursor = conn.execute("SELECT id FROM files WHERE id = ?", (file_id,))
    if cursor.fetchone() is None:
        return None

    if project_id is not None and project_id != 0:
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise ValueError(f"No project with id {project_id}")
    if client_id is not None and client_id != 0:
        cli = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not cli:
            raise ValueError(f"No client with id {client_id}")

    sets: list[str] = []
    params: list = []
    if project_id is not None:
        if project_id == 0:
            sets.append("project_id = NULL")
        else:
            sets.append("project_id = ?")
            params.append(project_id)
    if client_id is not None:
        if client_id == 0:
            sets.append("client_id = NULL")
        else:
            sets.append("client_id = ?")
            params.append(client_id)
    if not sets:
        return True

    sets.append("assignment_source = 'user'")
    params.append(file_id)
    try:
        conn.execute(f"UPDATE files SET {', '.join(sets)} WHERE id = ?", params)
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e):
            raise
        # assignment_source not present (tool-only DB)
        sets.pop()
        conn.execute(f"UPDATE files SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return True


def list_file_ids_under_path(
    conn: sqlite3.Connection,
    folder_path: str,
) -> List[int]:
    """Return IDs of all non-removed files whose path is under *folder_path*."""
    escaped = folder_path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    cursor = conn.execute(
        "SELECT id FROM files WHERE path LIKE ? ESCAPE '\\' AND status = 'listed'",
        (escaped + "/%",),
    )
    return [row["id"] for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def _determine_file_status(name: str, path: str) -> tuple:
    """Determine status for a file based on name/path.

    Returns:
        Tuple of (status, status_reason)
    """
    if name.startswith("."):
        return "unlisted", "dot_file"

    path_parts = path.split("/")
    for part in path_parts:
        if part.startswith(".") and part not in ("", "."):
            return "unlisted", "in_dot_folder"

    return "listed", None


def _find_project_for_path(
    conn: sqlite3.Connection,
    file_path: str,
    project_prefix_map: Optional[List[Tuple[str, int]]] = None,
) -> Optional[int]:
    """Find most specific project by root_path prefix match."""
    if project_prefix_map is not None:
        for root_path, project_id in project_prefix_map:
            if file_path.startswith(root_path):
                return project_id
        return None

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id FROM projects
        WHERE root_path IS NOT NULL
        AND ? LIKE root_path || '%'
        ORDER BY LENGTH(root_path) DESC
        LIMIT 1
    """,
        (file_path,),
    )
    row = cursor.fetchone()
    return row["id"] if row else None


def _get_folder_project_id(
    conn: sqlite3.Connection,
    folder_id: int,
    folder_project_map: Optional[Dict[int, int]] = None,
) -> Optional[int]:
    """Look up project_id from a folder row."""
    if folder_project_map is not None:
        return folder_project_map.get(folder_id)

    cursor = conn.cursor()
    cursor.execute("SELECT project_id FROM folders WHERE id = ?", (folder_id,))
    row = cursor.fetchone()
    return row["project_id"] if row else None


def _is_remote_source(conn: sqlite3.Connection, source: str) -> bool:
    """Check if a source name is a remote source via the sources table."""
    row = conn.execute("SELECT source_type FROM sources WHERE name = ?", (source,)).fetchone()
    return row is not None and row["source_type"] == "remote"


def _find_folder_in_map(
    conn: sqlite3.Connection,
    source: str,
    path: str,
    folder_path_map: Dict[Tuple[str, str], int],
    remote_source_names: Optional[frozenset] = None,
) -> Optional[int]:
    """Resolve folder_id using in-memory map with ancestor walk."""
    parent_dir = os.path.dirname(path)

    if remote_source_names is not None:
        is_remote = source in remote_source_names
    else:
        is_remote = _is_remote_source(conn, source)

    if is_remote:
        folder_path = f"{source}:{parent_dir}"
    else:
        folder_path = parent_dir

    folder_id = folder_path_map.get((source, folder_path))
    if folder_id is not None:
        return folder_id

    while parent_dir and parent_dir != "/" and len(parent_dir) > 1:
        parent_dir = os.path.dirname(parent_dir)
        if is_remote:
            folder_path = f"{source}:{parent_dir}"
        else:
            folder_path = parent_dir

        folder_id = folder_path_map.get((source, folder_path))
        if folder_id is not None:
            return folder_id

    return None


def _find_folder_for_path(
    conn: sqlite3.Connection,
    source: str,
    path: str,
    folder_path_map: Optional[Dict[Tuple[str, str], int]] = None,
    remote_source_names: Optional[frozenset] = None,
) -> Optional[int]:
    """Find folder_id for a file by matching path to folders."""
    if not path:
        return None

    if folder_path_map is not None:
        return _find_folder_in_map(
            conn,
            source,
            path,
            folder_path_map,
            remote_source_names=remote_source_names,
        )

    is_remote = _is_remote_source(conn, source)
    cursor = conn.cursor()
    parent_dir = os.path.dirname(path)

    if is_remote:
        folder_path = f"{source}:{parent_dir}"
    else:
        folder_path = parent_dir

    cursor.execute(
        "SELECT id FROM folders WHERE source = ? AND path = ?",
        (source, folder_path),
    )
    row = cursor.fetchone()
    if row:
        return row["id"]

    while parent_dir and parent_dir != "/" and len(parent_dir) > 1:
        parent_dir = os.path.dirname(parent_dir)
        if is_remote:
            folder_path = f"{source}:{parent_dir}"
        else:
            folder_path = parent_dir

        cursor.execute(
            "SELECT id FROM folders WHERE source = ? AND path = ?",
            (source, folder_path),
        )
        row = cursor.fetchone()
        if row:
            return row["id"]

    return None


def build_project_prefix_map(conn: sqlite3.Connection) -> List[Tuple[str, int]]:
    """Load project prefix map sorted by path length descending.

    Returns:
        List of (root_path, project_id) tuples, longest path first.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, root_path FROM projects
        WHERE root_path IS NOT NULL
        ORDER BY LENGTH(root_path) DESC
        """
    )
    return [(row["root_path"], row["id"]) for row in cursor.fetchall()]


def build_folder_maps(
    conn: sqlite3.Connection,
) -> Tuple[Dict[Tuple[str, str], int], Dict[int, int]]:
    """Load folder path->id and folder->project maps.

    Returns:
        Tuple of (folder_path_map, folder_project_map).
    """
    cursor = conn.cursor()
    cursor.execute("SELECT id, source, path, project_id FROM folders")
    path_map: Dict[Tuple[str, str], int] = {}
    project_map: Dict[int, int] = {}
    for row in cursor.fetchall():
        path_map[(row["source"], row["path"])] = row["id"]
        if row["project_id"] is not None:
            project_map[row["id"]] = row["project_id"]
    return path_map, project_map


def insert_file(
    conn: sqlite3.Connection,
    file_data: Dict[str, Any],
    relationship_maps: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, int]]:
    """Insert or update local file with project auto-linking and status assignment.

    Returns:
        ('inserted', file_id) on new insert or reactivation,
        ('updated', file_id) on content/metadata change,
        ('unchanged', file_id) when the existing active row's sha256 and size match
            the incoming payload (no SQL UPDATE is issued)
    """
    cursor = conn.cursor()

    file_path = file_data.get("file_path") or file_data.get("path")

    cursor.execute(
        "SELECT id, status, sha256_hash, size_bytes, project_id FROM files WHERE source = 'local' AND path = ?",
        (file_path,),
    )
    existing = cursor.fetchone()

    proj_map = relationship_maps.get("project_prefix_map") if relationship_maps else None
    fpath_map = relationship_maps.get("folder_path_map") if relationship_maps else None
    fproj_map = relationship_maps.get("folder_project_map") if relationship_maps else None
    dsn = relationship_maps.get("remote_source_names") if relationship_maps else None

    # Fast path: unchanged active row → skip the UPDATE.
    # Requires a non-None sha256 on both sides so missing hashes never short-circuit.
    # When existing.project_id IS NULL we still consult _find_project_for_path: if a
    # project would resolve, fall through so the UPDATE's `CASE WHEN project_id IS NULL
    # THEN ?` backfill can run. If no project matches, NULL→NULL — fast-path is safe.
    if existing is not None and existing["status"] != "removed":
        incoming_sha = file_data.get("sha256_hash")
        incoming_size = file_data.get("file_size")
        if incoming_size is None:
            incoming_size = file_data.get("size_bytes")
        if (
            incoming_sha is not None
            and existing["sha256_hash"] is not None
            and incoming_sha == existing["sha256_hash"]
            and incoming_size == existing["size_bytes"]
        ):
            if existing["project_id"] is not None or _find_project_for_path(
                conn, file_path, project_prefix_map=proj_map
            ) is None:
                return ("unchanged", existing["id"])

    project_id = _find_project_for_path(conn, file_path, project_prefix_map=proj_map)
    folder_id = _find_folder_for_path(
        conn,
        "local",
        file_path,
        folder_path_map=fpath_map,
        remote_source_names=dsn,
    )
    if project_id is None and folder_id is not None:
        project_id = _get_folder_project_id(conn, folder_id, folder_project_map=fproj_map)

    name = file_data.get("file_name") or file_data.get("name")
    content_type = file_data.get("file_type") or file_data.get("content_type")
    size_bytes = file_data.get("file_size") or file_data.get("size_bytes")

    status, status_reason = _determine_file_status(name, file_path)

    try:
        cursor.execute(
            """
            INSERT INTO files (
                source, name, path, content_type, mime_type, size_bytes,
                created_at, modified_at, accessed_at,
                content_preview, sha256_hash, md5_hash, project_id, folder_id, metadata,
                status, status_reason, status_changed_at
            ) VALUES ('local', ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?,
                      ?, ?, CURRENT_TIMESTAMP)
        """,
            (
                name,
                file_path,
                content_type,
                file_data.get("mime_type"),
                size_bytes,
                file_data.get("created_at"),
                file_data.get("modified_at"),
                file_data.get("accessed_at"),
                file_data.get("content_preview"),
                file_data.get("sha256_hash"),
                file_data.get("md5_hash"),
                project_id,
                folder_id,
                json.dumps(file_data.get("metadata", {})),
                status,
                status_reason,
            ),
        )
    except sqlite3.IntegrityError:
        cursor.execute(
            """
            UPDATE files SET
                name = ?,
                content_type = ?,
                size_bytes = ?,
                modified_at = ?,
                accessed_at = ?,
                updated_at = CURRENT_TIMESTAMP,
                content_preview = ?,
                sha256_hash = ?,
                md5_hash = ?,
                project_id = CASE WHEN project_id IS NULL THEN ? ELSE project_id END,
                folder_id = ?,
                -- FPR-1721: any UPDATE invalidates the prior embedding. The fast-path
                -- `unchanged` branch returns earlier without issuing this UPDATE, so
                -- only genuinely changed rows reach here.
                vectorized_at = NULL,
                vectorized_chunks = 0,
                status = CASE
                    WHEN status = 'removed' THEN ?
                    WHEN status IS NULL THEN ?
                    ELSE status
                END,
                status_reason = CASE
                    WHEN status = 'removed' THEN ?
                    WHEN status IS NULL THEN ?
                    ELSE status_reason
                END,
                status_changed_at = CASE
                    WHEN status = 'removed' OR status IS NULL THEN CURRENT_TIMESTAMP
                    ELSE status_changed_at
                END
            WHERE source = 'local' AND path = ?
        """,
            (
                name,
                content_type,
                size_bytes,
                file_data.get("modified_at"),
                file_data.get("accessed_at"),
                file_data.get("content_preview"),
                file_data.get("sha256_hash"),
                file_data.get("md5_hash"),
                project_id,
                folder_id,
                status,
                status,
                status_reason,
                status_reason,
                file_path,
            ),
        )
    if existing:
        action = "updated" if existing["status"] != "removed" else "inserted"
        return (action, existing["id"])
    return ("inserted", cursor.lastrowid)


def insert_drive_file(
    conn: sqlite3.Connection,
    data: Dict[str, Any],
    relationship_maps: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Insert or update a Drive file with folder auto-linking.

    Returns:
        File ID on success
    """
    cursor = conn.cursor()

    fpath_map = relationship_maps.get("folder_path_map") if relationship_maps else None
    fproj_map = relationship_maps.get("folder_project_map") if relationship_maps else None
    dsn = relationship_maps.get("remote_source_names") if relationship_maps else None

    folder_id = _find_folder_for_path(
        conn,
        data["source"],
        data["path"],
        folder_path_map=fpath_map,
        remote_source_names=dsn,
    )
    project_id = _get_folder_project_id(conn, folder_id, folder_project_map=fproj_map) if folder_id else None

    cursor.execute(
        "SELECT id, status FROM files WHERE source = ? AND external_id = ? AND account = ?",
        (data["source"], data["external_id"], data["account"]),
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            """
            UPDATE files SET
                name = ?,
                path = ?,
                content_type = ?,
                mime_type = ?,
                size_bytes = ?,
                created_at = ?,
                modified_at = ?,
                md5_hash = ?,
                metadata = ?,
                folder_id = ?,
                project_id = CASE WHEN project_id IS NULL THEN ? ELSE project_id END,
                updated_at = CURRENT_TIMESTAMP,
                -- FPR-1721: any UPDATE invalidates the prior embedding.
                vectorized_at = NULL,
                vectorized_chunks = 0
            WHERE id = ?
        """,
            (
                data["name"],
                data["path"],
                data.get("content_type"),
                data.get("mime_type"),
                data.get("size_bytes"),
                data.get("created_at"),
                data.get("modified_at"),
                data.get("md5_hash"),
                data.get("metadata"),
                folder_id,
                project_id,
                existing["id"],
            ),
        )
        return existing["id"]
    else:
        cursor.execute(
            """
            INSERT INTO files (
                source, external_id, account, name, path,
                content_type, mime_type, size_bytes,
                created_at, modified_at, md5_hash, metadata,
                folder_id, project_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                data["source"],
                data["external_id"],
                data["account"],
                data["name"],
                data["path"],
                data.get("content_type"),
                data.get("mime_type"),
                data.get("size_bytes"),
                data.get("created_at"),
                data.get("modified_at"),
                data.get("md5_hash"),
                data.get("metadata"),
                folder_id,
                project_id,
            ),
        )
        return cursor.lastrowid


def mark_removed_files(conn: sqlite3.Connection, indexed_paths: set) -> List[int]:
    """Mark local files as 'removed' if path not in indexed_paths.

    Returns:
        List of file IDs that were marked as removed
    """
    if not indexed_paths:
        return []

    cursor = conn.cursor()
    cursor.execute("SELECT id, path FROM files WHERE source = 'local' AND status = 'listed'")

    removed_ids = []
    for row in cursor.fetchall():
        if row["path"] not in indexed_paths:
            removed_ids.append(row["id"])

    if removed_ids:
        for i in range(0, len(removed_ids), 500):
            batch = removed_ids[i : i + 500]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(
                f"""
                UPDATE files
                SET status = 'removed',
                    status_reason = 'file_deleted',
                    status_changed_at = CURRENT_TIMESTAMP,
                    vectorized_at = NULL,
                    vectorized_chunks = 0
                WHERE id IN ({placeholders})
            """,
                batch,
            )
        conn.commit()

    return removed_ids
