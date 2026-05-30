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

import os
import sqlite3
from typing import Callable, Dict, List, Literal, Optional, Tuple

from footprinter.db.policies import is_folder_path_scope
from footprinter.db.sql_utils import chunked_query as _chunked_query

VisibilityState = Literal["hidden", "opaque", "full"]

# Hardcoded baseline - used when NO policies match
BASELINE_VISIBILITY: VisibilityState = "opaque"


def get_visibility(conn: sqlite3.Connection, item_type: str, item_id: int) -> VisibilityState:
    """
    Resolve visibility for an item.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        item_type: 'file', 'email', 'chat', 'folder'
        item_id: Row ID in the relevant table

    Returns:
        'hidden', 'opaque', or 'full'
    """
    cursor = conn.cursor()

    if item_type == "file":
        return _get_file_visibility(cursor, item_id)
    elif item_type == "email":
        return _get_email_visibility(cursor, item_id)
    elif item_type == "chat":
        return _get_chat_visibility(cursor, item_id)
    elif item_type == "folder":
        return _get_folder_visibility(cursor, item_id)
    elif item_type == "visit":
        return _get_browser_visibility(cursor, item_id)
    elif item_type == "project":
        resolved, _ = _resolve_project_visibility_with_source(cursor, item_id)
        return resolved
    elif item_type == "client":
        resolved, _ = _resolve_client_visibility_with_source(cursor, item_id)
        return resolved
    else:
        return BASELINE_VISIBILITY  # Baseline for unknown types


def resolve_visibility_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Tuple[VisibilityState, str]:
    """
    Resolve visibility and return the source that determined it.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        item_type: 'file', 'email', 'chat', 'folder'
        item_id: Row ID in the relevant table

    Returns:
        Tuple of (resolved_visibility, source_scope)
        e.g., ('full', "folder:~/Work") or ('opaque', "baseline")
    """
    cursor = conn.cursor()

    if item_type == "file":
        return _resolve_file_visibility_with_source(cursor, item_id)
    elif item_type == "email":
        return _resolve_email_visibility_with_source(cursor, item_id)
    elif item_type == "chat":
        return _resolve_chat_visibility_with_source(cursor, item_id)
    elif item_type == "folder":
        return _resolve_folder_visibility_with_source(cursor, item_id)
    elif item_type == "project":
        return _resolve_project_visibility_with_source(cursor, item_id)
    elif item_type == "client":
        return _resolve_client_visibility_with_source(cursor, item_id)
    elif item_type == "visit":
        return _resolve_browser_visibility_with_source(cursor, item_id)
    else:
        return (BASELINE_VISIBILITY, "baseline")


def batch_resolve_visibility(
    conn: sqlite3.Connection, item_type: str, item_ids: List[int]
) -> Dict[int, Tuple[VisibilityState, str]]:
    """
    Resolve visibility for multiple items efficiently.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        item_type: 'file', 'email', 'chat', 'folder', 'project', 'client', 'visit'
        item_ids: List of row IDs

    Returns:
        Dict mapping item_id to (visibility_state, source) tuple
    """
    if not item_ids:
        return {}

    cursor = conn.cursor()

    if item_type == "file":
        return _batch_resolve_file_visibility(cursor, item_ids)
    elif item_type == "project":
        return _batch_resolve_project_visibility(cursor, item_ids)
    elif item_type == "client":
        return _batch_resolve_client_visibility(cursor, item_ids)
    elif item_type == "email":
        return _batch_resolve_email_visibility(cursor, item_ids)
    elif item_type == "chat":
        return _batch_resolve_chat_visibility(cursor, item_ids)
    elif item_type == "folder":
        return _batch_resolve_folder_visibility(cursor, item_ids)
    elif item_type == "visit":
        return _batch_resolve_browser_visibility(cursor, item_ids)
    else:
        return {id_: (BASELINE_VISIBILITY, "baseline") for id_ in item_ids}


def get_source_visibility(conn, scope: str) -> VisibilityState:
    """Get the visibility state for a source scope (e.g. 'source:browser')."""
    cursor = conn.cursor()
    result = _get_policy(cursor, scope)
    if result is not None:
        return result
    state, _ = _get_global_baseline(cursor)
    return state


def _batch_resolve_file_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for files."""
    conn = cursor.connection

    # Pre-fetch all visibility policies
    cursor.execute("SELECT scope, setting FROM visibility_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    # Pre-fetch folder policies sorted by length for prefix matching
    # Exclude numeric-only suffixes (folder:{id}) — those are item-level scopes, not paths
    folder_policies = [
        (scope, setting)
        for scope, setting in all_policies.items()
        if scope.startswith("folder:") and is_folder_path_scope(scope)
    ]
    folder_policies.sort(key=lambda x: len(x[0]), reverse=True)

    # Pre-fetch file data (chunked to stay under SQLite variable limit)
    rows = _chunked_query(
        cursor,
        """
        SELECT file.id, file.path, file.project_id, file.folder_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id IN ({placeholders})
        """,
        item_ids,
    )
    files = {row["id"]: row for row in rows}

    # Collect unique parent entity IDs for batch resolution
    folder_ids = set()
    project_ids = set()
    client_ids = set()
    for row in files.values():
        if row["folder_id"]:
            folder_ids.add(row["folder_id"])
        if row["project_id"]:
            project_ids.add(row["project_id"])
        if row["client_id"]:
            client_ids.add(row["client_id"])

    # Batch resolve parent entities
    folder_visibility = batch_resolve_visibility(conn, "folder", list(folder_ids)) if folder_ids else {}
    project_visibility = batch_resolve_visibility(conn, "project", list(project_ids)) if project_ids else {}
    client_visibility = batch_resolve_visibility(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for file_id in item_ids:
        if file_id not in files:
            results[file_id] = (BASELINE_VISIBILITY, "not_found")
            continue

        row = files[file_id]
        policies: List[Tuple[Optional[VisibilityState], str]] = []

        # 1. Item-level policy
        item_scope = f"file:{file_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))

        # 2. Folder prefix match (most specific first)
        path = row["path"] or ""
        if path:
            for scope, setting in folder_policies:
                prefix = scope[len("folder:") :]
                if prefix.startswith("~"):
                    prefix = os.path.expanduser(prefix)
                if path.startswith(prefix):
                    policies.append((_resolve(setting), scope))
                    break

        # 3. Folder FK via full resolution (skip baseline)
        folder_id = row["folder_id"]
        if folder_id and folder_id in folder_visibility:
            state, src = folder_visibility[folder_id]
            if src != "baseline":
                policies.append((state, f"folder:{folder_id} (via {src})"))

        # 4. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_visibility:
            state, src = project_visibility[project_id]
            if src != "baseline":
                policies.append((state, f"project:{project_id} (via {src})"))

        # 5. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_visibility:
            state, src = client_visibility[client_id]
            if src != "baseline":
                policies.append((state, f"client:{client_id} (via {src})"))

        # 6. Source policy
        source_scope = "source:files"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: most restrictive wins (hidden > opaque > full)
        for value, source in policies:
            if value == "hidden":
                results[file_id] = ("hidden", source)
                break
        else:
            for value, source in policies:
                if value == "opaque":
                    results[file_id] = ("opaque", source)
                    break
            else:
                for value, source in policies:
                    if value == "full":
                        results[file_id] = ("full", source)
                        break
                else:
                    results[file_id] = global_baseline

    return results


def _batch_resolve_project_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for projects."""
    cursor.execute("SELECT scope, setting FROM visibility_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    # Pre-fetch project data for client_id (chunked)
    rows = _chunked_query(
        cursor,
        "SELECT id, client_id FROM projects WHERE id IN ({placeholders})",
        item_ids,
    )
    projects = {row["id"]: row for row in rows}

    results = {}
    for project_id in item_ids:
        policies: List[Tuple[Optional[VisibilityState], str]] = []

        # 1. Project-level policy
        proj_scope = f"project:{project_id}"
        if proj_scope in all_policies:
            policies.append((_resolve(all_policies[proj_scope]), proj_scope))

        # 2. Client-level policy
        if project_id in projects and projects[project_id]["client_id"]:
            client_scope = f"client:{projects[project_id]['client_id']}"
            if client_scope in all_policies:
                policies.append((_resolve(all_policies[client_scope]), client_scope))

        # 3. Source policy for projects
        source_scope = "source:projects"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: most restrictive wins
        for value, source in policies:
            if value == "hidden":
                results[project_id] = ("hidden", source)
                break
        else:
            for value, source in policies:
                if value == "opaque":
                    results[project_id] = ("opaque", source)
                    break
            else:
                for value, source in policies:
                    if value == "full":
                        results[project_id] = ("full", source)
                        break
                else:
                    results[project_id] = global_baseline

    return results


def _batch_resolve_client_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for clients."""
    cursor.execute("SELECT scope, setting FROM visibility_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    results = {}
    for client_id in item_ids:
        policies: List[Tuple[Optional[VisibilityState], str]] = []

        # 1. Client-level policy
        client_scope = f"client:{client_id}"
        if client_scope in all_policies:
            policies.append((_resolve(all_policies[client_scope]), client_scope))

        # 2. Source policy for clients
        source_scope = "source:clients"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: most restrictive wins
        for value, source in policies:
            if value == "hidden":
                results[client_id] = ("hidden", source)
                break
        else:
            for value, source in policies:
                if value == "opaque":
                    results[client_id] = ("opaque", source)
                    break
            else:
                for value, source in policies:
                    if value == "full":
                        results[client_id] = ("full", source)
                        break
                else:
                    results[client_id] = global_baseline

    return results


def _batch_resolve_email_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for emails."""
    conn = cursor.connection

    cursor.execute("SELECT scope, setting FROM visibility_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    rows = _chunked_query(
        cursor,
        """
        SELECT email.id, email.account, email.project_id,
               COALESCE(email.client_id, project.client_id) AS client_id
        FROM emails email
        LEFT JOIN projects project ON email.project_id = project.id
        WHERE email.id IN ({placeholders})
        """,
        item_ids,
    )
    emails = {row["id"]: row for row in rows}

    # Collect unique parent entity IDs for batch resolution
    project_ids = set()
    client_ids = set()
    for row in emails.values():
        if row["project_id"]:
            project_ids.add(row["project_id"])
        if row["client_id"]:
            client_ids.add(row["client_id"])

    # Batch resolve parent entities
    project_visibility = batch_resolve_visibility(conn, "project", list(project_ids)) if project_ids else {}
    client_visibility = batch_resolve_visibility(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for email_id in item_ids:
        if email_id not in emails:
            results[email_id] = (BASELINE_VISIBILITY, "not_found")
            continue

        row = emails[email_id]
        policies: List[Tuple[Optional[VisibilityState], str]] = []

        # 1. Item-level policy
        item_scope = f"email:{email_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))

        # 2. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_visibility:
            state, src = project_visibility[project_id]
            if src != "baseline":
                policies.append((state, f"project:{project_id} (via {src})"))

        # 3. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_visibility:
            state, src = client_visibility[client_id]
            if src != "baseline":
                policies.append((state, f"client:{client_id} (via {src})"))

        # 4. Account-level policy
        account = row["account"] or ""
        if account:
            acct_scope = f"account:{account}"
            if acct_scope in all_policies:
                policies.append((_resolve(all_policies[acct_scope]), acct_scope))

        # 5. Source policy
        source_scope = "source:emails"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: most restrictive wins
        for value, source in policies:
            if value == "hidden":
                results[email_id] = ("hidden", source)
                break
        else:
            for value, source in policies:
                if value == "opaque":
                    results[email_id] = ("opaque", source)
                    break
            else:
                for value, source in policies:
                    if value == "full":
                        results[email_id] = ("full", source)
                        break
                else:
                    results[email_id] = global_baseline

    return results


def _batch_resolve_chat_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for chats."""
    conn = cursor.connection

    cursor.execute("SELECT scope, setting FROM visibility_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    rows = _chunked_query(
        cursor,
        """
        SELECT chat.id, chat.account, chat.project_id,
               COALESCE(chat.client_id, project.client_id) AS client_id
        FROM chats chat
        LEFT JOIN projects project ON chat.project_id = project.id
        WHERE chat.id IN ({placeholders})
        """,
        item_ids,
    )
    convs = {row["id"]: row for row in rows}

    # Collect unique parent entity IDs for batch resolution
    project_ids = set()
    client_ids = set()
    for row in convs.values():
        if row["project_id"]:
            project_ids.add(row["project_id"])
        if row["client_id"]:
            client_ids.add(row["client_id"])

    # Batch resolve parent entities
    project_visibility = batch_resolve_visibility(conn, "project", list(project_ids)) if project_ids else {}
    client_visibility = batch_resolve_visibility(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for chat_id in item_ids:
        if chat_id not in convs:
            results[chat_id] = (BASELINE_VISIBILITY, "not_found")
            continue

        row = convs[chat_id]
        policies: List[Tuple[Optional[VisibilityState], str]] = []

        # 1. Item-level policy
        item_scope = f"chat:{chat_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))

        # 2. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_visibility:
            state, src = project_visibility[project_id]
            if src != "baseline":
                policies.append((state, f"project:{project_id} (via {src})"))

        # 3. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_visibility:
            state, src = client_visibility[client_id]
            if src != "baseline":
                policies.append((state, f"client:{client_id} (via {src})"))

        # 4. Account-level policy
        account = row["account"] or ""
        if account:
            acct_scope = f"account:{account}"
            if acct_scope in all_policies:
                policies.append((_resolve(all_policies[acct_scope]), acct_scope))

        # 5. Source policy
        source_scope = "source:chats"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: most restrictive wins
        for value, source in policies:
            if value == "hidden":
                results[chat_id] = ("hidden", source)
                break
        else:
            for value, source in policies:
                if value == "opaque":
                    results[chat_id] = ("opaque", source)
                    break
            else:
                for value, source in policies:
                    if value == "full":
                        results[chat_id] = ("full", source)
                        break
                else:
                    results[chat_id] = global_baseline

    return results


def _batch_resolve_folder_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for folders."""
    conn = cursor.connection

    cursor.execute("SELECT scope, setting FROM visibility_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    folder_policies = [
        (scope, setting)
        for scope, setting in all_policies.items()
        if scope.startswith("folder:") and is_folder_path_scope(scope)
    ]
    folder_policies.sort(key=lambda x: len(x[0]), reverse=True)

    rows = _chunked_query(
        cursor,
        """
        SELECT folder.id, folder.path, folder.project_id, folder.parent_folder_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id IN ({placeholders})
        """,
        item_ids,
    )
    folders = {row["id"]: row for row in rows}

    # Collect unique parent entity IDs for batch resolution
    project_ids = set()
    client_ids = set()
    for row in folders.values():
        if row["project_id"]:
            project_ids.add(row["project_id"])
        if row["client_id"]:
            client_ids.add(row["client_id"])

    # Batch resolve parent entities
    project_visibility = batch_resolve_visibility(conn, "project", list(project_ids)) if project_ids else {}
    client_visibility = batch_resolve_visibility(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for folder_id in item_ids:
        if folder_id not in folders:
            results[folder_id] = (BASELINE_VISIBILITY, "not_found")
            continue

        row = folders[folder_id]
        policies: List[Tuple[Optional[VisibilityState], str]] = []

        # 1. Item-level policy
        item_scope = f"folder:{folder_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))
        else:
            # 1b. Ancestor folder ID policies (nearest ancestor wins)
            result = _walk_ancestor_policies(
                cursor, folder_id, row["parent_folder_id"],
                lambda scope: _resolve(all_policies.get(scope)),
            )
            if result:
                policies.append(result)

        # 2. Folder prefix match
        path = row["path"] or ""
        if path:
            for scope, setting in folder_policies:
                prefix = scope[len("folder:") :]
                if prefix.startswith("~"):
                    prefix = os.path.expanduser(prefix)
                if path.startswith(prefix):
                    policies.append((_resolve(setting), scope))
                    break

        # 3. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_visibility:
            state, src = project_visibility[project_id]
            if src != "baseline":
                policies.append((state, f"project:{project_id} (via {src})"))

        # 4. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_visibility:
            state, src = client_visibility[client_id]
            if src != "baseline":
                policies.append((state, f"client:{client_id} (via {src})"))

        # 5. Source policy
        source_scope = "source:folders"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: most restrictive wins
        for value, source in policies:
            if value == "hidden":
                results[folder_id] = ("hidden", source)
                break
        else:
            for value, source in policies:
                if value == "opaque":
                    results[folder_id] = ("opaque", source)
                    break
            else:
                for value, source in policies:
                    if value == "full":
                        results[folder_id] = ("full", source)
                        break
                else:
                    results[folder_id] = global_baseline

    return results


def _resolve(value: Optional[str]) -> Optional[VisibilityState]:
    """Convert a visibility value to state or None (no policy)."""
    if value in ("hidden", "opaque", "full"):
        return value
    return None  # 'inherit' or NULL means no policy


def _get_policy(cursor, scope: str) -> Optional[VisibilityState]:
    """Look up a visibility_policies row."""
    cursor.execute("SELECT setting FROM visibility_policies WHERE scope = ?", (scope,))
    row = cursor.fetchone()
    if row:
        return _resolve(row["setting"])
    return None


def _walk_ancestor_policies(
    cursor: sqlite3.Cursor,
    folder_id: int,
    parent_folder_id: Optional[int],
    lookup_policy: Callable[[str], Optional[VisibilityState]],
) -> Optional[Tuple[VisibilityState, str]]:
    """Walk up parent_folder_id chain, return (state, scope) of nearest ancestor with a policy."""
    parent_id = parent_folder_id
    visited = {folder_id}
    while parent_id and parent_id not in visited:
        visited.add(parent_id)
        ancestor_scope = f"folder:{parent_id}"
        policy = lookup_policy(ancestor_scope)
        if policy is not None:
            return (policy, ancestor_scope)
        parent_row = cursor.execute(
            "SELECT parent_folder_id FROM folders WHERE id = ?", (parent_id,)
        ).fetchone()
        parent_id = parent_row["parent_folder_id"] if parent_row else None
    return None


def _get_global_baseline(cursor) -> Tuple[VisibilityState, str]:
    """Get global policy or fall back to hardcoded baseline."""
    row = cursor.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
    if row:
        return (_resolve(row["setting"]), "global")
    return (BASELINE_VISIBILITY, "baseline")


def _resolve_parent_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Optional[Tuple[VisibilityState, str]]:
    """Resolve parent entity visibility, returning None if baseline.

    This is used when resolving file/folder visibility to check parent
    entities (folder, project, client). If the parent resolves to baseline,
    we return None so that baseline doesn't propagate down the hierarchy.
    """
    state, source = resolve_visibility_with_source(conn, item_type, item_id)
    if source == "baseline":
        return None
    return (state, source)


def _get_file_visibility(cursor, file_id: int) -> VisibilityState:
    """Resolve file visibility using policies."""
    resolved, _ = _resolve_file_visibility_with_source(cursor, file_id)
    return resolved


def _resolve_file_visibility_with_source(cursor, file_id: int) -> Tuple[VisibilityState, str]:
    """Resolve file visibility with source tracking."""
    cursor.execute(
        """
        SELECT file.path, file.project_id, file.folder_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id = ?
    """,
        (file_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[VisibilityState], str]] = []

    # 1. Item-level policy (file:{id})
    item_policy = _get_policy(cursor, f"file:{file_id}")
    if item_policy is not None:
        policies.append((item_policy, f"file:{file_id}"))

    # 2. Folder prefix match (most specific first)
    path = row["path"] or ""
    if path:
        cursor.execute(
            """
            SELECT scope, setting FROM visibility_policies
            WHERE scope LIKE 'folder:%'
            ORDER BY LENGTH(scope) DESC
        """
        )
        for folder_row in cursor.fetchall():
            if not is_folder_path_scope(folder_row["scope"]):
                continue
            prefix = folder_row["scope"][len("folder:") :]
            if prefix.startswith("~"):
                prefix = os.path.expanduser(prefix)
            if path.startswith(prefix):
                policies.append((_resolve(folder_row["setting"]), folder_row["scope"]))
                break  # Only use most specific folder match

    # 3. Folder-level via full resolution (folder:{id}) - skip baseline
    folder_id = row["folder_id"]
    if folder_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "folder", folder_id)
        if result:
            state, src = result
            policies.append((state, f"folder:{folder_id} (via {src})"))

    # 4. Project-level via full resolution (project:{id}) - skip baseline
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "project", project_id)
        if result:
            state, src = result
            policies.append((state, f"project:{project_id} (via {src})"))

    # 5. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "client", client_id)
        if result:
            state, src = result
            policies.append((state, f"client:{client_id} (via {src})"))

    # 6. Source policy
    source_policy = _get_policy(cursor, "source:files")
    if source_policy is not None:
        policies.append((source_policy, "source:files"))

    # MOST-RESTRICTIVE-WINS among matching policies only:
    # hidden > opaque > full
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)

    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)

    for value, source in policies:
        if value == "full":
            return ("full", source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _get_email_visibility(cursor, email_id: int) -> VisibilityState:
    """Resolve visibility for an email using policies."""
    resolved, _ = _resolve_email_visibility_with_source(cursor, email_id)
    return resolved


def _resolve_email_visibility_with_source(cursor, email_id: int) -> Tuple[VisibilityState, str]:
    """Resolve email visibility with source tracking.

    Chain: email:{id} → project:{id} → client:{id} → account:{acct} → source:emails
    """
    cursor.execute(
        """
        SELECT email.account, email.project_id,
               COALESCE(email.client_id, project.client_id) AS client_id
        FROM emails email
        LEFT JOIN projects project ON email.project_id = project.id
        WHERE email.id = ?
    """,
        (email_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[VisibilityState], str]] = []

    # 1. Item-level policy (email:{id})
    item_policy = _get_policy(cursor, f"email:{email_id}")
    if item_policy is not None:
        policies.append((item_policy, f"email:{email_id}"))

    # 2. Project-level via full resolution (project:{id}) - skip baseline
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "project", project_id)
        if result:
            state, src = result
            policies.append((state, f"project:{project_id} (via {src})"))

    # 3. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "client", client_id)
        if result:
            state, src = result
            policies.append((state, f"client:{client_id} (via {src})"))

    # 4. Account-level policy
    account = row["account"] or ""
    if account:
        account_policy = _get_policy(cursor, f"account:{account}")
        if account_policy is not None:
            policies.append((account_policy, f"account:{account}"))

    # 5. Source policy
    source_policy = _get_policy(cursor, "source:emails")
    if source_policy is not None:
        policies.append((source_policy, "source:emails"))

    # MOST-RESTRICTIVE-WINS among matching policies only:
    # hidden > opaque > full
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)

    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)

    for value, source in policies:
        if value == "full":
            return ("full", source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _get_chat_visibility(cursor, chat_id: int) -> VisibilityState:
    """Resolve visibility for a chat using policies."""
    resolved, _ = _resolve_chat_visibility_with_source(cursor, chat_id)
    return resolved


def _resolve_chat_visibility_with_source(cursor, chat_id: int) -> Tuple[VisibilityState, str]:
    """Resolve chat visibility with source tracking.

    Chain: chat:{id} → project:{id} → client:{id} → account:{acct} → source:chats
    """
    cursor.execute(
        """
        SELECT chat.account, chat.project_id,
               COALESCE(chat.client_id, project.client_id) AS client_id
        FROM chats chat
        LEFT JOIN projects project ON chat.project_id = project.id
        WHERE chat.id = ?
    """,
        (chat_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[VisibilityState], str]] = []

    # 1. Item-level policy (chat:{id})
    item_policy = _get_policy(cursor, f"chat:{chat_id}")
    if item_policy is not None:
        policies.append((item_policy, f"chat:{chat_id}"))

    # 2. Project-level via full resolution (project:{id}) - skip baseline
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "project", project_id)
        if result:
            state, src = result
            policies.append((state, f"project:{project_id} (via {src})"))

    # 3. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "client", client_id)
        if result:
            state, src = result
            policies.append((state, f"client:{client_id} (via {src})"))

    # 4. Account-level policy (e.g., account:claude)
    account = row["account"] or ""
    if account:
        account_policy = _get_policy(cursor, f"account:{account}")
        if account_policy is not None:
            policies.append((account_policy, f"account:{account}"))

    # 5. Source policy (source:chats)
    source_policy = _get_policy(cursor, "source:chats")
    if source_policy is not None:
        policies.append((source_policy, "source:chats"))

    # MOST-RESTRICTIVE-WINS among matching policies only:
    # hidden > opaque > full
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)

    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)

    for value, source in policies:
        if value == "full":
            return ("full", source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _get_folder_visibility(cursor, folder_id: int) -> VisibilityState:
    """Resolve visibility for an indexed folder using policies."""
    resolved, _ = _resolve_folder_visibility_with_source(cursor, folder_id)
    return resolved


def _resolve_folder_visibility_with_source(cursor, folder_id: int) -> Tuple[VisibilityState, str]:
    """Resolve folder visibility with source tracking."""
    cursor.execute(
        """
        SELECT folder.path, folder.project_id, folder.parent_folder_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id = ?
    """,
        (folder_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[VisibilityState], str]] = []

    # 1. Item-level policy (folder:{id})
    item_policy = _get_policy(cursor, f"folder:{folder_id}")
    if item_policy is not None:
        policies.append((item_policy, f"folder:{folder_id}"))
    else:
        # 1b. Ancestor folder ID policies (nearest ancestor wins)
        result = _walk_ancestor_policies(
            cursor, folder_id, row["parent_folder_id"],
            lambda scope: _get_policy(cursor, scope),
        )
        if result:
            policies.append(result)

    # 2. Folder prefix match (most specific first)
    path = row["path"] or ""
    if path:
        cursor.execute(
            """
            SELECT scope, setting FROM visibility_policies
            WHERE scope LIKE 'folder:%'
            ORDER BY LENGTH(scope) DESC
        """
        )
        for folder_row in cursor.fetchall():
            if not is_folder_path_scope(folder_row["scope"]):
                continue
            prefix = folder_row["scope"][len("folder:") :]
            if prefix.startswith("~"):
                prefix = os.path.expanduser(prefix)
            if path.startswith(prefix):
                policies.append((_resolve(folder_row["setting"]), folder_row["scope"]))
                break  # Only use most specific folder match

    # 3. Project-level via full resolution (project:{id}) - skip baseline
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "project", project_id)
        if result:
            state, src = result
            policies.append((state, f"project:{project_id} (via {src})"))

    # 4. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "client", client_id)
        if result:
            state, src = result
            policies.append((state, f"client:{client_id} (via {src})"))

    # 5. Source policy
    source_policy = _get_policy(cursor, "source:folders")
    if source_policy is not None:
        policies.append((source_policy, "source:folders"))

    # MOST-RESTRICTIVE-WINS among matching policies only:
    # hidden > opaque > full
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)

    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)

    for value, source in policies:
        if value == "full":
            return ("full", source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _resolve_project_visibility_with_source(cursor, project_id: int) -> Tuple[VisibilityState, str]:
    """Resolve project visibility with source tracking.

    Chain: project:{id} -> client:{id} -> source:projects
    """
    cursor.execute(
        """
        SELECT client_id FROM projects WHERE id = ?
    """,
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[VisibilityState], str]] = []

    # 1. Project-level policy (project:{id})
    project_policy = _get_policy(cursor, f"project:{project_id}")
    if project_policy is not None:
        policies.append((project_policy, f"project:{project_id}"))

    # 2. Client-level policy via full resolution (skip baseline)
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_with_source(conn, "client", client_id)
        if result:
            state, src = result
            policies.append((state, f"client:{client_id} (via {src})"))

    # 3. Source policy for projects
    source_policy = _get_policy(cursor, "source:projects")
    if source_policy is not None:
        policies.append((source_policy, "source:projects"))

    # MOST-RESTRICTIVE-WINS among matching policies only:
    # hidden > opaque > full
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)

    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)

    for value, source in policies:
        if value == "full":
            return ("full", source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _resolve_client_visibility_with_source(cursor, client_id: int) -> Tuple[VisibilityState, str]:
    """Resolve client visibility with source tracking.

    Chain: client:{id} -> source:clients
    """
    cursor.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[VisibilityState], str]] = []

    # 1. Client-level policy (client:{id})
    client_policy = _get_policy(cursor, f"client:{client_id}")
    if client_policy is not None:
        policies.append((client_policy, f"client:{client_id}"))

    # 2. Source policy for clients
    source_policy = _get_policy(cursor, "source:clients")
    if source_policy is not None:
        policies.append((source_policy, "source:clients"))

    # MOST-RESTRICTIVE-WINS among matching policies only:
    # hidden > opaque > full
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)

    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)

    for value, source in policies:
        if value == "full":
            return ("full", source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _get_browser_visibility(cursor, browser_id: int) -> VisibilityState:
    """Resolve visibility for a browser history item using policies.

    Browser history only has source-level policies (no item/folder/project/client hierarchy).
    """
    resolved, _ = _resolve_browser_visibility_with_source(cursor, browser_id)
    return resolved


def _resolve_browser_visibility_with_source(cursor, browser_id: int) -> Tuple[VisibilityState, str]:
    """Resolve browser history visibility with source tracking.

    Browser history has no hierarchy - only source-level policy applies.
    Chain: source:browser → baseline
    """
    # Verify item exists
    cursor.execute("SELECT id FROM visits WHERE id = ?", (browser_id,))
    row = cursor.fetchone()
    if not row:
        return (BASELINE_VISIBILITY, "not_found")

    # Only source policy applies
    source_policy = _get_policy(cursor, "source:browser")
    if source_policy is not None:
        return (source_policy, "source:browser")

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _batch_resolve_browser_visibility(cursor, item_ids: List[int]) -> Dict[int, Tuple[VisibilityState, str]]:
    """Batch resolve visibility for browser history items.

    Since browser history only uses source-level policy, we can resolve once
    and apply to all items.
    """
    cursor.execute("SELECT scope, setting FROM visibility_policies WHERE scope IN ('source:browser', 'global')")
    rows = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    if "source:browser" in rows:
        source_visibility = _resolve(rows["source:browser"])
        source = "source:browser"
    else:
        source_visibility = None

    # Global policy fallback
    if "global" in rows:
        global_baseline = (_resolve(rows["global"]), "global")
    else:
        global_baseline = (BASELINE_VISIBILITY, "baseline")

    # Verify which items exist (chunked)
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


def is_readable(visibility: VisibilityState) -> bool:
    """Check if an item with this visibility can be read.

    Only full-visibility items can have their content read.
    Hidden and opaque items are blocked at the visibility layer.
    """
    return visibility == "full"
