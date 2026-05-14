"""Shared SQL helper functions for building dynamic CASE/WHEN clauses,
pagination utilities, and chunked query execution."""

import sqlite3

# Stay well under SQLite's variable limit (999 on older builds, 32766 on newer).
_SQLITE_VAR_LIMIT = 500


def chunked_query(cursor, sql_template: str, item_ids: list[int]) -> list:
    """Execute a query in chunks to stay under SQLite's variable limit.

    The *sql_template* must contain a ``{placeholders}`` marker where the
    ``IN (?, ?, ...)`` list will be inserted.
    """
    results = []
    for i in range(0, len(item_ids), _SQLITE_VAR_LIMIT):
        chunk = item_ids[i : i + _SQLITE_VAR_LIMIT]
        placeholders = ",".join("?" * len(chunk))
        sql = sql_template.format(placeholders=placeholders)
        cursor.execute(sql, chunk)
        results.extend(cursor.fetchall())
    return results


def paginate(
    conn: sqlite3.Connection,
    count_sql: str,
    fetch_sql: str,
    params,
    *,
    page: int = 1,
    limit: int = 50,
) -> tuple[list, dict]:
    """Execute a count + paginated fetch and return (rows, pagination_dict)."""
    total = conn.execute(count_sql, list(params)).fetchone()[0]
    total_pages = max(1, (total + limit - 1) // limit)
    offset = (page - 1) * limit
    rows = conn.execute(fetch_sql, list(params) + [limit, offset]).fetchall()
    return rows, {"page": page, "limit": limit, "total": total, "total_pages": total_pages}


def paginated_response(entity_key: str, items, pagination: dict, **extras) -> dict:
    """Build a standard paginated response envelope."""
    return {entity_key: items, "pagination": pagination, **extras}


def build_location_case_clauses(
    config: dict, home: str, path_col: str = "path", prefix: str = ""
) -> tuple[list[str], list]:
    """Build CASE/WHEN SQL clauses from config directories.
    Returns (case_lines: list[str], params: list)."""
    dirs = config.get("directories", [])
    case_lines = []
    params = []
    for d in dirs:
        expanded = d.replace("~", home)
        label = d.rstrip("/").split("/")[-1]
        if not label.startswith("."):
            label = label.title()
        case_lines.append(f"WHEN {path_col} LIKE ? THEN ?")
        params.extend([expanded + "/%", prefix + label])
    return case_lines, params


def build_remote_source_label_clauses(
    sources_data: list[dict],
) -> tuple[list[str], list]:
    """Build CASE/WHEN clauses for remote source labels.
    Returns (case_lines: list[str], params: list)."""
    case_lines = []
    params = []
    for s in sources_data:
        if s["source_type"] == "remote":
            case_lines.append("WHEN source = ? THEN ?")
            params.extend([s["name"], f"Drive (indexed): {s['account']}"])
    return case_lines, params


def build_status_filter(
    status: "str | list[str] | None",
    *,
    column: str,
    default_exclude: "list[str] | None" = None,
    default_include: "list[str] | None" = None,
) -> tuple[list[str], list]:
    """Build a status filter clause for dynamic WHERE construction.

    Returns (conditions, params) where conditions is a list of 0 or 1
    SQL fragments suitable for extending a WHERE clause.

    Parameters
    ----------
    status : str, list[str], or None
        ``None`` → apply default filter.
        ``"all"`` → no filter (bypass defaults).
        Single string → exact match.
        List of strings → IN clause.  Empty list → no filter.
    column : str
        Fully qualified column reference (e.g. ``"file.status"``).
    default_exclude : list[str], optional
        Statuses to exclude when ``status is None``.
    default_include : list[str], optional
        Statuses to include when ``status is None``.

    ``default_exclude`` and ``default_include`` are mutually exclusive.
    If both are provided, ``default_exclude`` takes precedence.
    """
    if status == "all":
        return [], []

    if status is None:
        if default_exclude:
            placeholders = ",".join("?" for _ in default_exclude)
            return [f"{column} NOT IN ({placeholders})"], list(default_exclude)
        if default_include:
            placeholders = ",".join("?" for _ in default_include)
            return [f"{column} IN ({placeholders})"], list(default_include)
        return [], []

    if isinstance(status, list):
        if not status:
            return [], []
        placeholders = ",".join("?" for _ in status)
        return [f"{column} IN ({placeholders})"], list(status)

    # Single string — exact match
    return [f"{column} = ?"], [status]


def split_query_terms(query: str) -> list[str]:
    """Split query on whitespace, dropping terms shorter than 2 chars."""
    return [t for t in query.split() if len(t) >= 2]


def build_fts5_query(terms: list[str]) -> str:
    """Build an FTS5 query with AND semantics and prefix matching."""
    sanitized = [term.replace('"', "") for term in terms]
    return " ".join(f'"{term}"*' for term in sanitized if len(term) >= 2)


def build_term_conditions(
    columns: list[str],
    terms: list[str],
) -> tuple[str, list[str]]:
    """Build AND-ed LIKE conditions: every term must appear in at least one column."""
    groups = []
    params: list[str] = []
    for term in terms:
        like = f"%{term}%"
        col_parts = [f"{col} LIKE ?" for col in columns]
        groups.append(f"({' OR '.join(col_parts)})")
        params.extend([like] * len(columns))
    return " AND ".join(groups), params


def build_remote_account_case_clauses(
    sources_data: list[dict],
) -> tuple[list[str], list]:
    """Build CASE/WHEN clauses mapping remote source names to accounts.
    Returns (case_lines: list[str], params: list)."""
    case_lines = []
    params = []
    for s in sources_data:
        if s["source_type"] == "remote":
            case_lines.append("WHEN ? THEN ?")
            params.extend([s["name"], s["account"]])
    return case_lines, params


_RELATIONSHIP_TABLES = frozenset({"visits", "files", "chats", "emails", "folders"})


def update_entity_relationships(
    conn: sqlite3.Connection,
    table: str,
    entity_id: int,
    *,
    project_id: int | None = None,
    client_id: int | None = None,
) -> bool | None:
    """Update project and/or client assignment on any entity row.

    Only updates fields that are passed (not None). Pass ``0`` to clear
    a field (set to NULL). Stamps ``assignment_source = 'user'``
    when the column exists (app-scope DBs only).
    Returns True on success, None if entity not found.
    """
    if table not in _RELATIONSHIP_TABLES:
        raise ValueError(f"Unsupported table: {table}")
    cursor = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (entity_id,))
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
    params.append(entity_id)
    try:
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e):
            raise
        # assignment_source not present (tool-only DB)
        sets.pop()
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    return True
