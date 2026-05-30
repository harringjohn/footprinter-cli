"""Access control policy CRUD — visibility and permission layers."""

import sqlite3

PERMISSION_SETTINGS = frozenset({"allow", "deny"})
VISIBILITY_SETTINGS = frozenset({"full", "opaque", "hidden"})

SCOPE_PREFIXES = frozenset({"source", "account", "folder", "project", "client", "file", "email", "chat"})
VALID_SOURCE_TYPES = frozenset({"files", "emails", "chats", "folders", "browser", "projects", "clients"})
_ID_PREFIXES = frozenset({"project", "client", "file", "email", "chat"})


def is_folder_path_scope(scope: str) -> bool:
    """True if scope is a folder path prefix (not a numeric folder ID)."""
    suffix = scope[len("folder:") :]
    return not suffix.isdigit()


def validate_scope(scope: str) -> None:
    """Raise ValueError if *scope* is not a recognised scope pattern."""
    if scope == "global":
        return
    if ":" in scope:
        prefix, value = scope.split(":", 1)
        if prefix not in SCOPE_PREFIXES:
            raise ValueError(f"Invalid scope: {scope!r}. Unknown prefix {prefix!r}.")
        if not value or value.isspace():
            raise ValueError(f"Invalid scope: {scope!r}. Value after '{prefix}:' must not be empty.")
        if prefix == "source" and value not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid scope: {scope!r}. Valid source types: {', '.join(sorted(VALID_SOURCE_TYPES))}")
        if prefix in _ID_PREFIXES:
            try:
                int(value)
            except ValueError:
                raise ValueError(f"Invalid scope: {scope!r}. '{prefix}:' requires a numeric ID.") from None
        return
    raise ValueError(f"Invalid scope: {scope!r}. Expected 'global' or 'prefix:value'.")


# ---------------------------------------------------------------------------
# Visibility policies
# ---------------------------------------------------------------------------


def list_visibility_policies(conn: sqlite3.Connection) -> list[dict]:
    """Return all visibility policies as plain dicts."""
    rows = conn.execute("SELECT scope, setting, updated_at FROM visibility_policies ORDER BY scope").fetchall()
    return [{"scope": r["scope"], "setting": r["setting"], "updated_at": r["updated_at"]} for r in rows]


def set_visibility_policy(conn: sqlite3.Connection, scope: str, setting: str) -> bool:
    """Insert or update a visibility policy. Returns True."""
    validate_scope(scope)
    if setting not in VISIBILITY_SETTINGS:
        raise ValueError(f"Invalid visibility setting: {setting}. Valid: {', '.join(sorted(VISIBILITY_SETTINGS))}")
    conn.execute(
        "INSERT OR REPLACE INTO visibility_policies (scope, setting, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (scope, setting),
    )
    conn.commit()
    return True


def delete_visibility_policy(conn: sqlite3.Connection, scope: str) -> bool:
    """Delete a visibility policy. Returns True if a row was removed."""
    cur = conn.cursor()
    cur.execute("DELETE FROM visibility_policies WHERE scope = ?", (scope,))
    deleted = cur.rowcount > 0
    conn.commit()
    return deleted


def clear_visibility_policies(conn: sqlite3.Connection) -> int:
    """Delete all visibility policies. Returns count of rows removed."""
    cur = conn.cursor()
    cur.execute("DELETE FROM visibility_policies")
    count = cur.rowcount
    conn.commit()
    return count


def seed_visibility_defaults(conn: sqlite3.Connection) -> bool:
    """Seed ``global=full`` into visibility_policies. Idempotent."""
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO visibility_policies (scope, setting) VALUES ('global', 'full')")
    seeded = cur.rowcount > 0
    conn.commit()
    return seeded


# ---------------------------------------------------------------------------
# Permission policies
# ---------------------------------------------------------------------------


def list_permission_policies(conn: sqlite3.Connection) -> list[dict]:
    """Return all permission policies as plain dicts."""
    rows = conn.execute("SELECT scope, setting, updated_at FROM permission_policies ORDER BY scope").fetchall()
    return [{"scope": r["scope"], "setting": r["setting"], "updated_at": r["updated_at"]} for r in rows]


def set_permission_policy(conn: sqlite3.Connection, scope: str, setting: str) -> bool:
    """Insert or update a permission policy. Returns True."""
    validate_scope(scope)
    if setting not in PERMISSION_SETTINGS:
        raise ValueError(f"Invalid permission setting: {setting}. Valid: {', '.join(sorted(PERMISSION_SETTINGS))}")
    conn.execute(
        "INSERT OR REPLACE INTO permission_policies (scope, setting, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (scope, setting),
    )
    conn.commit()
    return True


def delete_permission_policy(conn: sqlite3.Connection, scope: str) -> bool:
    """Delete a permission policy. Returns True if a row was removed."""
    cur = conn.cursor()
    cur.execute("DELETE FROM permission_policies WHERE scope = ?", (scope,))
    deleted = cur.rowcount > 0
    conn.commit()
    return deleted


def clear_permission_policies(conn: sqlite3.Connection) -> int:
    """Delete all permission policies. Returns count of rows removed."""
    cur = conn.cursor()
    cur.execute("DELETE FROM permission_policies")
    count = cur.rowcount
    conn.commit()
    return count


def seed_permission_defaults(conn: sqlite3.Connection) -> bool:
    """Seed ``global=allow`` into permission_policies. Idempotent."""
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO permission_policies (scope, setting) VALUES ('global', 'allow')")
    seeded = cur.rowcount > 0
    conn.commit()
    return seeded


# ---------------------------------------------------------------------------
# Combined seed
# ---------------------------------------------------------------------------


def seed_access_policies(conn: sqlite3.Connection) -> dict:
    """Seed both visibility and permission defaults. Returns status dict."""
    vis = seed_visibility_defaults(conn)
    perm = seed_permission_defaults(conn)
    return {"visibility_seeded": vis, "permission_seeded": perm}
