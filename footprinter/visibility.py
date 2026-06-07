"""
Visibility resolution for MCP client metadata access.

Two-tier model:
  - Policies: Explicit rules (file:*, folder:*, source:*, project:*, client:*)
  - Baseline: Hardcoded fallback (BASELINE_VISIBILITY = 'opaque')

Most-restrictive-wins semantics applies ONLY among matching policies.
  hidden > opaque > full

Hierarchy layers (checked for policies):
  file:{id} → folder prefix → folder FK → project:{id} → client:{id} → source:*
  email:{id} → project:{id} → client:{id} → account:{acct} → source:emails
  chat:{id} → project:{id} → client:{id} → account:{acct} → source:chats

Resolution:
  1. Collect explicit values from matching policies only
  2. If any policy is 'hidden' → return 'hidden'
  3. If any policy is 'opaque' → return 'opaque'
  4. If any policy is 'full' → return 'full'
  5. No policies matched → return BASELINE_VISIBILITY

Visibility states:
  - 'hidden'  - item doesn't exist to MCP (excluded from all results and counts)
  - 'opaque'  - appears with minimal info (id, content_type, source)
  - 'full'    - full metadata returned

Hard Rule: Content read access can never exceed metadata visibility.
If an item is hidden or opaque, it cannot be read regardless of permission policy.
"""

import sqlite3
from typing import Dict, List, Literal, Optional, Tuple

from footprinter.db.sql_utils import chunked_query as _chunked_query
from footprinter.policy_resolver import (
    ItemSpec,
    PolicyResolver,
    most_restrictive_wins,
    resolve_batch,
    resolve_single,
    walk_ancestor_policies,
)

VisibilityState = Literal["hidden", "opaque", "full"]

BASELINE_VISIBILITY: VisibilityState = "opaque"


# ── Value parser ──────────────────────────────────────────────────────


def _resolve(value: Optional[str]) -> Optional[VisibilityState]:
    """Convert a visibility value to state or None (no policy)."""
    if value in ("hidden", "opaque", "full"):
        return value
    return None


# ── Resolver instance ─────────────────────────────────────────────────

_RESOLVER = PolicyResolver(
    policy_table="visibility_policies",
    parse_value=_resolve,
    pick_winner=most_restrictive_wins,
    baseline=BASELINE_VISIBILITY,
)


# ── Item-type specs ───────────────────────────────────────────────────

_PARENT_PROJECT_CLIENT = (
    ("project", "project_id", True),
    ("client", "client_id", True),
)

_FILE_SPEC = ItemSpec(
    entity_name="file",
    source_scope="source:files",
    single_fetch_sql="""
        SELECT file.path, file.project_id, file.folder_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id = ?
    """,
    batch_fetch_sql="""
        SELECT file.id, file.path, file.project_id, file.folder_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id IN ({placeholders})
    """,
    parent_refs=_PARENT_PROJECT_CLIENT,
    not_found_value=BASELINE_VISIBILITY,
    has_path=True,
    has_folder_fk=True,
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
    not_found_value=BASELINE_VISIBILITY,
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
    not_found_value=BASELINE_VISIBILITY,
    has_account=True,
)

_FOLDER_SPEC = ItemSpec(
    entity_name="folder",
    source_scope="source:folders",
    single_fetch_sql="""
        SELECT folder.path, folder.project_id, folder.parent_folder_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id = ?
    """,
    batch_fetch_sql="""
        SELECT folder.id, folder.path, folder.project_id, folder.parent_folder_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id IN ({placeholders})
    """,
    parent_refs=_PARENT_PROJECT_CLIENT,
    not_found_value=BASELINE_VISIBILITY,
    has_path=True,
    has_ancestor_walk=True,
)

_PROJECT_SPEC = ItemSpec(
    entity_name="project",
    source_scope="source:projects",
    single_fetch_sql="SELECT client_id FROM projects WHERE id = ?",
    batch_fetch_sql="SELECT id, client_id FROM projects WHERE id IN ({placeholders})",
    parent_refs=(("client", "client_id", False),),
    not_found_value=BASELINE_VISIBILITY,
    not_found_on_missing=False,
)

_CLIENT_SPEC = ItemSpec(
    entity_name="client",
    source_scope="source:clients",
    single_fetch_sql="SELECT id FROM clients WHERE id = ?",
    batch_fetch_sql=None,
    parent_refs=(),
    not_found_value=BASELINE_VISIBILITY,
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


# ── Public API ────────────────────────────────────────────────────────


def get_visibility(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> VisibilityState:
    """Resolve visibility for an item."""
    resolved, _ = resolve_visibility_with_source(conn, item_type, item_id)
    return resolved


def resolve_visibility_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Tuple[VisibilityState, str]:
    """Resolve visibility and return the source that determined it."""
    if item_type == "visit":
        return _resolve_browser_visibility_with_source(conn.cursor(), item_id)
    spec = _SPECS.get(item_type)
    if spec:
        return resolve_single(
            _RESOLVER, conn, spec, item_id, resolve_visibility_with_source
        )
    return (BASELINE_VISIBILITY, "baseline")


def batch_resolve_visibility(
    conn: sqlite3.Connection, item_type: str, item_ids: List[int]
) -> Dict[int, Tuple[VisibilityState, str]]:
    """Resolve visibility for multiple items efficiently."""
    if not item_ids:
        return {}
    if item_type == "visit":
        return _batch_resolve_browser_visibility(conn.cursor(), item_ids)
    spec = _SPECS.get(item_type)
    if spec:
        return resolve_batch(
            _RESOLVER, conn, spec, item_ids, batch_resolve_visibility
        )
    return {id_: (BASELINE_VISIBILITY, "baseline") for id_ in item_ids}


# ── Visibility-only extras ────────────────────────────────────────────


def get_source_visibility(
    conn: sqlite3.Connection, scope: str
) -> VisibilityState:
    """Get the visibility state for a source scope (e.g. 'source:browser')."""
    cursor = conn.cursor()
    result = _get_policy(cursor, scope)
    if result is not None:
        return result
    state, _ = _get_global_baseline(cursor)
    return state


def is_readable(visibility: VisibilityState) -> bool:
    """Check if an item with this visibility can be read.

    Only full-visibility items can have their content read.
    Hidden and opaque items are blocked at the visibility layer.
    """
    return visibility == "full"


# ── Browser (no hierarchy — kept as special case) ─────────────────────


def _resolve_browser_visibility_with_source(
    cursor: sqlite3.Cursor, browser_id: int
) -> Tuple[VisibilityState, str]:
    cursor.execute("SELECT id FROM visits WHERE id = ?", (browser_id,))
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")
    source_policy = _get_policy(cursor, "source:browser")
    if source_policy is not None:
        return (source_policy, "source:browser")
    return _get_global_baseline(cursor)


def _batch_resolve_browser_visibility(
    cursor: sqlite3.Cursor, item_ids: List[int]
) -> Dict[int, Tuple[VisibilityState, str]]:
    cursor.execute(
        "SELECT scope, setting FROM visibility_policies "
        "WHERE scope IN ('source:browser', 'global')"
    )
    rows = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    if "source:browser" in rows:
        source_visibility = _resolve(rows["source:browser"])
        source = "source:browser"
    else:
        source_visibility = None

    if "global" in rows:
        global_baseline = (_resolve(rows["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

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
            results[item_id] = (BASELINE_VISIBILITY, "not_found")
        elif source_visibility is not None:
            results[item_id] = (source_visibility, source)
        else:
            results[item_id] = global_baseline

    return results


# ── Backward-compatible private helpers (used by tests) ───────────────


def _get_policy(
    cursor: sqlite3.Cursor, scope: str
) -> Optional[VisibilityState]:
    return _RESOLVER.get_policy(cursor, scope)


def _get_global_baseline(
    cursor: sqlite3.Cursor,
) -> Tuple[VisibilityState, str]:
    return _RESOLVER.get_global_baseline(cursor)


def _resolve_parent_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Optional[Tuple[VisibilityState, str]]:
    return _RESOLVER.resolve_parent(
        conn, item_type, item_id, resolve_visibility_with_source
    )


def _walk_ancestor_policies(cursor, folder_id, parent_folder_id, lookup_policy):
    return walk_ancestor_policies(cursor, folder_id, parent_folder_id, lookup_policy)
