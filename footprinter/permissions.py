"""
Permission resolution for MCP client read access.

Two-tier model:
  - Policies: Explicit rules (file:*, folder:*, source:*, project:*, client:*)
  - Baseline: Hardcoded fallback (BASELINE_PERMISSION = True)

Deny-wins semantics applies ONLY among matching policies.
If no policies match, the baseline is used.

Hierarchy layers (checked for policies):
  file:{id} → folder prefix → project:{id} → client:{id} → source:*
  email:{id} → project:{id} → client:{id} → account:{acct} → source:emails
  chat:{id} → project:{id} → client:{id} → account:{acct} → source:chats

Resolution:
  1. Collect explicit values from matching policies only
  2. If any policy is 'deny' → return False
  3. If any policy is 'allow' → return True
  4. No policies matched → return BASELINE_PERMISSION
"""

import sqlite3
from typing import Dict, List, Optional, Tuple

from footprinter.db.sql_utils import chunked_query as _chunked_query
from footprinter.policy_resolver import (
    ItemSpec,
    PolicyResolver,
    deny_wins,
    resolve_batch,
    resolve_single,
)

BASELINE_PERMISSION = True


def _resolve(value: Optional[str]) -> Optional[bool]:
    """Convert a permission value to bool or None (no policy)."""
    if value == "allow":
        return True
    if value == "deny":
        return False
    return None


_RESOLVER = PolicyResolver(
    policy_table="permission_policies",
    parse_value=_resolve,
    pick_winner=deny_wins,
    baseline=BASELINE_PERMISSION,
)


_PARENT_PROJECT_CLIENT = (
    ("project", "project_id", True),
    ("client", "client_id", True),
)

_FILE_SPEC = ItemSpec(
    entity_name="file",
    source_scope="source:files",
    single_fetch_sql="""
        SELECT file.path, file.project_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id = ?
    """,
    batch_fetch_sql="""
        SELECT file.id, file.path, file.project_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id IN ({placeholders})
    """,
    parent_refs=_PARENT_PROJECT_CLIENT,
    not_found_value=False,
    has_path=True,
)

_EMAIL_SPEC = ItemSpec(
    entity_name="email",
    source_scope="source:emails",
    single_fetch_sql="""
        SELECT email.account, email.project_id,
               COALESCE(email.client_id, project.client_id) AS client_id
        FROM emails email
        LEFT JOIN projects project ON email.project_id = project.id
        WHERE email.id = ?
    """,
    batch_fetch_sql="""
        SELECT email.id, email.account, email.project_id,
               COALESCE(email.client_id, project.client_id) AS client_id
        FROM emails email
        LEFT JOIN projects project ON email.project_id = project.id
        WHERE email.id IN ({placeholders})
    """,
    parent_refs=_PARENT_PROJECT_CLIENT,
    not_found_value=False,
    has_account=True,
)

_CHAT_SPEC = ItemSpec(
    entity_name="chat",
    source_scope="source:chats",
    single_fetch_sql="""
        SELECT chat.account, chat.project_id,
               COALESCE(chat.client_id, project.client_id) AS client_id
        FROM chats chat
        LEFT JOIN projects project ON chat.project_id = project.id
        WHERE chat.id = ?
    """,
    batch_fetch_sql="""
        SELECT chat.id, chat.account, chat.project_id,
               COALESCE(chat.client_id, project.client_id) AS client_id
        FROM chats chat
        LEFT JOIN projects project ON chat.project_id = project.id
        WHERE chat.id IN ({placeholders})
    """,
    parent_refs=_PARENT_PROJECT_CLIENT,
    not_found_value=False,
    has_account=True,
)

_FOLDER_SPEC = ItemSpec(
    entity_name="folder",
    source_scope="source:folders",
    single_fetch_sql="""
        SELECT folder.path, folder.project_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id = ?
    """,
    batch_fetch_sql="""
        SELECT folder.id, folder.path, folder.project_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id IN ({placeholders})
    """,
    parent_refs=_PARENT_PROJECT_CLIENT,
    not_found_value=BASELINE_PERMISSION,
    has_path=True,
)

_PROJECT_SPEC = ItemSpec(
    entity_name="project",
    source_scope="source:projects",
    single_fetch_sql="SELECT client_id FROM projects WHERE id = ?",
    batch_fetch_sql="SELECT id, client_id FROM projects WHERE id IN ({placeholders})",
    parent_refs=(("client", "client_id", False),),
    not_found_value=BASELINE_PERMISSION,
    not_found_on_missing=False,
)

_CLIENT_SPEC = ItemSpec(
    entity_name="client",
    source_scope="source:clients",
    single_fetch_sql="SELECT id FROM clients WHERE id = ?",
    batch_fetch_sql=None,
    parent_refs=(),
    not_found_value=BASELINE_PERMISSION,
    not_found_on_missing=False,
)

_SPECS = {
    "file": _FILE_SPEC,
    "email": _EMAIL_SPEC,
    "chat": _CHAT_SPEC,
    "folder": _FOLDER_SPEC,
    "project": _PROJECT_SPEC,
    "client": _CLIENT_SPEC,
}

_CAN_READ_TYPES = {"file", "email", "chat"}


def can_read(conn: sqlite3.Connection, item_type: str, item_id: int) -> bool:
    """Resolve whether the MCP client can read this item."""
    if item_type == "visit":
        resolved, _ = _resolve_browser_with_source(conn.cursor(), item_id)
        return resolved
    spec = _SPECS.get(item_type)
    if spec and item_type in _CAN_READ_TYPES:
        resolved, _ = resolve_single(
            _RESOLVER, conn, spec, item_id, resolve_permission_with_source
        )
        return resolved
    return False


def resolve_permission_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Tuple[bool, str]:
    """Resolve permission and return the source that determined it."""
    if item_type == "visit":
        return _resolve_browser_with_source(conn.cursor(), item_id)
    spec = _SPECS.get(item_type)
    if spec:
        return resolve_single(
            _RESOLVER, conn, spec, item_id, resolve_permission_with_source
        )
    return (False, "baseline")


def batch_resolve_permissions(
    conn: sqlite3.Connection, item_type: str, item_ids: List[int]
) -> Dict[int, Tuple[bool, str]]:
    """Resolve permissions for multiple items efficiently."""
    if not item_ids:
        return {}
    if item_type == "visit":
        return _batch_resolve_browser(conn.cursor(), item_ids)
    spec = _SPECS.get(item_type)
    if spec:
        return resolve_batch(
            _RESOLVER, conn, spec, item_ids, batch_resolve_permissions
        )
    return {id_: (False, "baseline") for id_ in item_ids}


def _resolve_browser_with_source(
    cursor: sqlite3.Cursor, browser_id: int
) -> Tuple[bool, str]:
    cursor.execute("SELECT id FROM visits WHERE id = ?", (browser_id,))
    row = cursor.fetchone()
    if not row:
        return (False, "not_found")
    source_policy = _get_policy(cursor, "source:browser")
    if source_policy is not None:
        return (source_policy, "source:browser")
    return _get_global_baseline(cursor)


def _batch_resolve_browser(
    cursor: sqlite3.Cursor, item_ids: List[int]
) -> Dict[int, Tuple[bool, str]]:
    cursor.execute(
        "SELECT scope, setting FROM permission_policies "
        "WHERE scope IN ('source:browser', 'global')"
    )
    rows = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    if "source:browser" in rows:
        source_permission = _resolve(rows["source:browser"])
        source = "source:browser"
    else:
        source_permission = None

    if "global" in rows:
        global_baseline = (_resolve(rows["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

    if item_ids:
        existing_rows = _chunked_query(
            cursor,
            "SELECT id FROM visits WHERE id IN ({placeholders})",
            item_ids,
        )
        existing_ids = {row["id"] for row in existing_rows}
    else:
        existing_ids = set()

    results = {}
    for item_id in item_ids:
        if item_id not in existing_ids:
            results[item_id] = (False, "not_found")
        elif source_permission is not None:
            results[item_id] = (source_permission, source)
        else:
            results[item_id] = global_baseline

    return results


def _get_policy(cursor: sqlite3.Cursor, scope: str) -> Optional[bool]:
    return _RESOLVER.get_policy(cursor, scope)


def _get_global_baseline(cursor: sqlite3.Cursor) -> Tuple[bool, str]:
    return _RESOLVER.get_global_baseline(cursor)


def _resolve_parent_permission_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Optional[Tuple[bool, str]]:
    return _RESOLVER.resolve_parent(
        conn, item_type, item_id, resolve_permission_with_source
    )
