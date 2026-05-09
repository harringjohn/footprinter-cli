"""Folder queries and write operations."""

import sqlite3
from typing import Any, Dict, List

from footprinter.db.sql_utils import build_status_filter, paginate, paginated_response


def list_folders(
    conn: sqlite3.Connection,
    *,
    project_id: int | None = None,
    depth: int | None = None,
    include_hidden: bool = False,
    status: "str | list[str] | None" = None,
    sort_by: str = "size",
    limit: int = 50,
    page: int = 1,
) -> dict:
    """Return indexed folders with project info.

    Parameters
    ----------
    conn : sqlite3.Connection
    project_id : int or None
        Filter by project. ``0`` means 'no project assigned'.
    depth : int or None
        Max path depth (segments below home).
        ``None`` = no filter (default; reads pre-computed counts and scales
        to 100k+ folders). ``1`` = top-level + one below; any explicit value
        triggers descendant rollup via correlated subqueries.
    include_hidden : bool
        If False, exclude folders with hidden segments (``/.``).
    status : str, list[str], or None
        ``None`` → exclude removed (default).
        ``"all"`` → no status filter.
        Single string → exact match (``"listed"``, ``"unlisted"``, ``"removed"``).
        List of strings → ``WHERE status IN (...)``.
    sort_by : str
        ``'size'`` (DESC), ``'files'`` (DESC), or ``'path'`` (ASC).
    limit : int
        Maximum rows per page (default 50).
    page : int
        1-based page number (default 1).

    Returns
    -------
    dict
        ``{"folders": [...], "pagination": {page, limit, total, total_pages}}``
    """
    where = "1=1"
    params: list = []

    status_conds, status_params = build_status_filter(
        status, column="folder.status", default_exclude=["removed"]
    )
    for cond in status_conds:
        where += f" AND {cond}"
    params.extend(status_params)

    if project_id is not None:
        if project_id == 0:
            where += " AND folder.project_id IS NULL"
        else:
            where += " AND folder.project_id = ?"
            params.append(project_id)

    if depth is not None:
        where += " AND (LENGTH(folder.relative_path) - LENGTH(REPLACE(folder.relative_path, '/', '')) - 1) <= ?"
        params.append(depth)

    if not include_hidden:
        where += " AND folder.relative_path NOT LIKE '%/.%'"

    # When depth is explicitly set, roll up descendant files via correlated
    # subqueries. When depth is None, read the pre-computed columns the
    # folders table already maintains (refresh_folder_counts), which scales
    # to 100k+ rows where the per-row subqueries do not.
    if depth is not None:
        count_expr = """(
            SELECT COUNT(*) FROM files file
            JOIN folders ancestor_folder ON file.folder_id = ancestor_folder.id
            WHERE file.status = 'listed'
              AND (ancestor_folder.id = folder_cte.id
                   OR ancestor_folder.relative_path LIKE folder_cte.relative_path || '/%')
        )"""
        size_expr = """(
            SELECT COALESCE(SUM(file.size_bytes), 0) FROM files file
            JOIN folders ancestor_folder ON file.folder_id = ancestor_folder.id
            WHERE file.status = 'listed'
              AND (ancestor_folder.id = folder_cte.id
                   OR ancestor_folder.relative_path LIKE folder_cte.relative_path || '/%')
        )"""
    else:
        count_expr = "COALESCE(folder_cte.direct_file_count, 0)"
        size_expr = "COALESCE(folder_cte.total_size_bytes, 0)"

    sort_map = {
        "size": "live_size_bytes DESC",
        "files": "live_file_count DESC",
        "path": "folder_cte.relative_path ASC",
    }
    order_clause = sort_map.get(sort_by, "live_size_bytes DESC")

    count_sql = f"SELECT COUNT(*) FROM folders folder WHERE {where}"
    fetch_sql = f"""
        WITH folder_cte AS (
            SELECT folder.id, folder.path, folder.relative_path, folder.name, folder.source,
                   folder.project_id, folder.mcp_view, folder.mcp_read,
                   folder.direct_file_count, folder.total_size_bytes
            FROM folders folder
            WHERE {where}
        )
        SELECT
            folder_cte.*,
            project.project_name AS project_name,
            {count_expr} AS live_file_count,
            {size_expr} AS live_size_bytes
        FROM folder_cte
        LEFT JOIN projects project ON folder_cte.project_id = project.id
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
    """
    rows, pagination = paginate(conn, count_sql, fetch_sql, params, page=page, limit=limit)

    folders = [
        {
            "id": row["id"],
            "path": row["path"],
            "relative_path": row["relative_path"],
            "name": row["name"],
            "source": row["source"] or "local",
            "direct_files": row["live_file_count"],
            "total_size_bytes": row["live_size_bytes"],
            "project_id": row["project_id"],
            "project_name": row["project_name"] or "",
            "mcp_view": row["mcp_view"],
            "mcp_read": row["mcp_read"],
        }
        for row in rows
    ]

    return paginated_response("folders", folders, pagination)


def get_folder_by_path(conn: sqlite3.Connection, path: str) -> dict | None:
    """Look up a folder by exact path. Returns dict or None."""
    row = conn.execute(
        """SELECT id, path, relative_path, name, source,
                  direct_file_count, total_size_bytes, scanned_at,
                  project_id, external_id, account, mcp_view, mcp_read
           FROM folders WHERE path = ?""",
        (path,),
    ).fetchone()
    return dict(row) if row else None


def get_folder_navigation(conn: sqlite3.Connection, folder_id: int, path: str) -> dict:
    """Return navigation data for a folder: files, subfolders, recursive file count.

    All results include ``mcp_view`` so the service layer can filter by visibility.
    """
    # Files in this folder (limit 200, hidden NOT pre-filtered — service does it)
    files = conn.execute(
        """SELECT id, name, content_type, size_bytes, modified_at, source, status,
                  mcp_view, mcp_read
           FROM files
           WHERE folder_id = ? AND status = 'listed'
           ORDER BY name
           LIMIT 200""",
        (folder_id,),
    ).fetchall()
    file_results = [dict(r) for r in files]

    # Immediate subfolders (one level deeper)
    subfolders = conn.execute(
        """SELECT id, path, relative_path, name, direct_file_count, total_size_bytes,
                  source, mcp_view, mcp_read
           FROM folders
           WHERE path LIKE ? AND path != ? AND path NOT LIKE ?""",
        (path + "/%", path, path + "/%/%"),
    ).fetchall()
    subfolder_results = [dict(sf) for sf in subfolders]

    # Recursive file count across all descendants (excludes hidden files)
    recursive = conn.execute(
        """WITH RECURSIVE descendants(id) AS (
               SELECT id FROM folders WHERE id = ?
               UNION ALL
               SELECT f.id FROM folders f
               JOIN descendants d ON f.parent_folder_id = d.id
           )
           SELECT COUNT(*) as total
           FROM files
           WHERE folder_id IN (SELECT id FROM descendants)
             AND status = 'listed'
             AND COALESCE(mcp_view, 'inherit') != 'hidden'""",
        (folder_id,),
    ).fetchone()

    return {
        "files": file_results,
        "subfolders": subfolder_results,
        "recursive_file_count": recursive["total"],
    }


def resolve_folder(conn: sqlite3.Connection, identifier: str) -> int:
    """Resolve folder ID or relative_path to row ID.

    Tries numeric ID first, then falls back to relative_path match.

    Raises ValueError if not found.
    """
    # Try numeric ID
    try:
        folder_id = int(identifier)
        row = conn.execute("SELECT id FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row:
            return row["id"]
        raise ValueError(f"No folder with id {folder_id}")
    except ValueError as exc:
        if "No folder" in str(exc):
            raise

    # Fall back to relative_path
    row = conn.execute("SELECT id FROM folders WHERE relative_path = ?", (identifier,)).fetchone()
    if row:
        return row["id"]

    raise ValueError(f"No folder matching '{identifier}'")


def get_folder(conn: sqlite3.Connection, folder_id: int) -> dict | None:
    """Return folder detail with child files and project info.

    Returns None if the folder does not exist.
    """
    row = conn.execute(
        """
        SELECT
            folder.id, folder.path, folder.relative_path, folder.name, folder.source,
            folder.project_id, folder.mcp_view, folder.mcp_read,
            project.project_name,
            (SELECT COUNT(*) FROM files file
             WHERE file.folder_id = folder.id AND file.status = 'listed'
            ) AS live_file_count,
            (SELECT COALESCE(SUM(file.size_bytes), 0) FROM files file
             WHERE file.folder_id = folder.id AND file.status = 'listed'
            ) AS live_size_bytes
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id = ?
        """,
        (folder_id,),
    ).fetchone()

    if not row:
        return None

    # Child files (limit 20)
    child_files = conn.execute(
        """
        SELECT id, name, content_type, size_bytes
        FROM files
        WHERE folder_id = ? AND status = 'listed'
        LIMIT 20
        """,
        (folder_id,),
    ).fetchall()

    return {
        "id": row["id"],
        "path": row["path"],
        "relative_path": row["relative_path"],
        "name": row["name"],
        "source": row["source"] or "local",
        "direct_files": row["live_file_count"],
        "total_size_bytes": row["live_size_bytes"],
        "project_id": row["project_id"],
        "project": {
            "id": row["project_id"],
            "name": row["project_name"] or "",
        }
        if row["project_id"]
        else None,
        "mcp_view": row["mcp_view"],
        "mcp_read": row["mcp_read"],
        "files": [
            {
                "id": a["id"],
                "name": a["name"],
                "content_type": a["content_type"] or "",
                "size_bytes": a["size_bytes"] or 0,
            }
            for a in child_files
        ],
    }


def cascade_project_id(
    conn: sqlite3.Connection,
    folder_id: int,
    project_id: int | None,
    *,
    clear: bool = False,
) -> dict:
    """Walk the folder tree from *folder_id* and set/clear project_id.

    Uses a recursive CTE on ``parent_folder_id`` to find all descendant
    folders, then updates both folders and their files.

    If *clear* is True, sets ``project_id = NULL`` on all descendants
    (the *project_id* argument is ignored).

    Returns ``{"folders_updated": int, "files_updated": int}``.
    """
    cursor = conn.cursor()
    value = None if clear else project_id

    # Validate project exists (when setting, not clearing)
    if not clear:
        row = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise ValueError(f"No project with id {project_id}")

    # Find all descendant folders (including the root itself)
    descendants_cte = """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM folders WHERE id = ?
            UNION ALL
            SELECT folder.id FROM folders folder
            JOIN descendants descendant ON folder.parent_folder_id = descendant.id
        )
    """

    cursor.execute(
        f"{descendants_cte} SELECT id FROM descendants",
        (folder_id,),
    )
    desc_ids = [row["id"] for row in cursor.fetchall()]

    if not desc_ids:
        return {"folders_updated": 0, "files_updated": 0}

    ph = ",".join("?" * len(desc_ids))

    # Update folders
    cursor.execute(
        f"UPDATE folders SET project_id = ? WHERE id IN ({ph})",
        [value] + desc_ids,
    )
    folders_updated = cursor.rowcount

    # Update files (skip removed)
    cursor.execute(
        f"UPDATE files SET project_id = ? WHERE folder_id IN ({ph}) AND status = 'listed'",
        [value] + desc_ids,
    )
    files_updated = cursor.rowcount
    conn.commit()

    return {
        "folders_updated": folders_updated,
        "files_updated": files_updated,
    }


def update_folder_relationships(
    conn: sqlite3.Connection,
    folder_id: int,
    *,
    project_id: int | None = None,
    client_id: int | None = None,
) -> bool | None:
    """Update project and/or client assignment on a single folder (no cascade).

    Only updates fields that are passed (not None). Pass ``0`` to clear
    a field (set to NULL). Stamps ``assignment_source = 'user'``
    when the column exists (app-scope DBs only), so auto-detection
    won't overwrite manual assignments.
    Returns True on success, or None if the folder does not exist.
    Raises ValueError if *project_id* is given (and not 0) but doesn't exist.
    """
    row = conn.execute("SELECT id FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if not row:
        return None

    if project_id is not None and project_id != 0:
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise ValueError(f"No project with id {project_id}")

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
    params.append(folder_id)
    try:
        conn.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", params)
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e):
            raise
        # assignment_source not present (tool-only DB)
        sets.pop()
        conn.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return True


def cascade_client_id(
    conn: sqlite3.Connection,
    folder_id: int,
    client_id: int | None,
    *,
    clear: bool = False,
) -> dict:
    """Walk the folder tree from *folder_id* and set/clear client_id.

    Uses a recursive CTE on ``parent_folder_id`` to find all descendant
    folders, then updates both folders and their files.

    If *clear* is True, sets ``client_id = NULL`` on all descendants
    (the *client_id* argument is ignored). Pass ``client_id=0`` as a
    sentinel to clear (equivalent to ``clear=True``).

    Returns ``{"folders_updated": int, "files_updated": int}``.
    """
    cursor = conn.cursor()

    # Treat 0 as a clear sentinel
    if client_id == 0:
        clear = True

    value = None if clear else client_id

    # Validate client exists (when setting, not clearing)
    if not clear:
        row = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not row:
            raise ValueError(f"No client with id {client_id}")

    # Find all descendant folders (including the root itself)
    descendants_cte = """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM folders WHERE id = ?
            UNION ALL
            SELECT folder.id FROM folders folder
            JOIN descendants descendant ON folder.parent_folder_id = descendant.id
        )
    """

    cursor.execute(
        f"{descendants_cte} SELECT id FROM descendants",
        (folder_id,),
    )
    desc_ids = [row["id"] for row in cursor.fetchall()]

    if not desc_ids:
        return {"folders_updated": 0, "files_updated": 0}

    ph = ",".join("?" * len(desc_ids))

    # Update folders
    cursor.execute(
        f"UPDATE folders SET client_id = ? WHERE id IN ({ph})",
        [value] + desc_ids,
    )
    folders_updated = cursor.rowcount

    # Update files (skip removed)
    cursor.execute(
        f"UPDATE files SET client_id = ? WHERE folder_id IN ({ph}) AND status = 'listed'",
        [value] + desc_ids,
    )
    files_updated = cursor.rowcount
    conn.commit()

    return {
        "folders_updated": folders_updated,
        "files_updated": files_updated,
    }


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def insert_drive_folder(conn: sqlite3.Connection, data: Dict[str, Any]) -> tuple:
    """Insert or update a Drive folder record in folders.

    Returns:
        Tuple of (result_type, folder_id) where result_type is 'inserted' or 'updated'
    """
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM folders WHERE source = ? AND external_id = ?",
        (data["source"], data["external_id"]),
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            """
            UPDATE folders SET
                path = ?,
                relative_path = ?,
                name = ?,
                account = ?,
                web_link = ?,
                scanned_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """,
            (
                data["path"],
                data["relative_path"],
                data["name"],
                data["account"],
                data["web_link"],
                existing["id"],
            ),
        )
        return "updated", existing["id"]
    else:
        cursor.execute(
            """
            INSERT INTO folders (
                source, external_id, account,
                path, relative_path, name,
                web_link, scanned_at, created_at,
                indexed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
            (
                data["source"],
                data["external_id"],
                data["account"],
                data["path"],
                data["relative_path"],
                data["name"],
                data["web_link"],
            ),
        )
        return "inserted", cursor.lastrowid


def mark_removed_folders(conn: sqlite3.Connection, scanned_paths: set) -> List[int]:
    """Mark local folders as 'removed' if path not in scanned_paths.

    Modeled on mark_removed_files() in db/files.py, with two intentional
    differences:
      * folders has no vectorization columns to clear
      * the caller controls the transaction — this function does not
        commit, so the cleanup can be rolled back if a later step in the
        adapter fails

    Returns:
        List of folder IDs that were marked as removed
    """
    if not scanned_paths:
        return []

    cursor = conn.cursor()
    cursor.execute("SELECT id, path FROM folders WHERE source = 'local' AND status = 'listed'")

    removed_ids = [row["id"] for row in cursor.fetchall() if row["path"] not in scanned_paths]

    for i in range(0, len(removed_ids), 500):
        batch = removed_ids[i : i + 500]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"""
            UPDATE folders
            SET status = 'removed',
                status_reason = 'folder_deleted',
                status_changed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
        """,
            batch,
        )

    return removed_ids


def update_drive_folder_parents(conn: sqlite3.Connection, source: str, folder_map: Dict[str, str]) -> int:
    """Update parent_folder_id links for Drive folders.

    Returns:
        Number of folders updated
    """
    cursor = conn.cursor()
    updated = 0

    for folder_ext_id, parent_ext_id in folder_map.items():
        cursor.execute(
            "SELECT id FROM folders WHERE source = ? AND external_id = ?",
            (source, parent_ext_id),
        )
        parent_row = cursor.fetchone()

        if parent_row:
            cursor.execute(
                """
                UPDATE folders
                SET parent_folder_id = ?
                WHERE source = ? AND external_id = ?
            """,
                (parent_row["id"], source, folder_ext_id),
            )
            updated += 1

    conn.commit()
    return updated


def refresh_folder_counts(conn: sqlite3.Connection) -> dict:
    """Refresh pre-computed file counts for all folders.

    Uses folder_id FK for direct counts, then propagates totals up
    the parent_folder_id hierarchy by processing from leaves to roots.

    Returns stats about the refresh operation.
    """
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE folders
        SET direct_file_count = COALESCE((
                SELECT COUNT(*) FROM files file
                WHERE file.folder_id = folders.id AND file.status = 'listed'
            ), 0),
            total_size_bytes = COALESCE((
                SELECT SUM(file.size_bytes) FROM files file
                WHERE file.folder_id = folders.id AND file.status = 'listed'
            ), 0)
    """
    )
    conn.commit()

    cursor.execute("UPDATE folders SET total_file_count = direct_file_count")
    conn.commit()

    cursor.execute(
        """
        SELECT id, parent_folder_id, direct_file_count, total_size_bytes
        FROM folders
        ORDER BY LENGTH(path) - LENGTH(REPLACE(path, '/', '')) DESC
    """
    )
    folders = cursor.fetchall()

    folder_counts = {row["id"]: row["direct_file_count"] or 0 for row in folders}
    folder_sizes = {row["id"]: row["total_size_bytes"] or 0 for row in folders}

    for row in folders:
        folder_id = row["id"]
        parent_id = row["parent_folder_id"]
        if parent_id and parent_id in folder_counts:
            folder_counts[parent_id] += folder_counts[folder_id]
            folder_sizes[parent_id] += folder_sizes[folder_id]

    for folder_id, total_count in folder_counts.items():
        cursor.execute(
            """
            UPDATE folders
            SET total_file_count = ?,
                total_size_bytes = ?
            WHERE id = ?
        """,
            (total_count, folder_sizes.get(folder_id, 0), folder_id),
        )

    conn.commit()

    cursor.execute(
        """
        SELECT
            COUNT(*) as folders,
            SUM(direct_file_count) as total_direct,
            MAX(total_file_count) as max_total
        FROM folders
    """
    )
    row = cursor.fetchone()

    return {
        "folders_updated": len(folders),
        "total_direct_files": row["total_direct"] or 0,
        "max_folder_total": row["max_total"] or 0,
    }
