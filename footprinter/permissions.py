"""
Permission resolution for Claude read access.

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

import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from footprinter.db.policies import is_folder_path_scope
from footprinter.db.sql_utils import chunked_query as _chunked_query

# Hardcoded baseline - used when NO policies match
BASELINE_PERMISSION = True


def can_read(conn: sqlite3.Connection, item_type: str, item_id: int) -> bool:
    """
    Resolve whether Claude can read this item.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        item_type: 'file', 'email', 'chat'
        item_id: Row ID in the relevant table

    Returns:
        True if reading is permitted, False otherwise.
    """
    cursor = conn.cursor()

    if item_type == "file":
        return _can_read_file(cursor, item_id)
    elif item_type == "email":
        return _can_read_email(cursor, item_id)
    elif item_type == "chat":
        return _can_read_chat(cursor, item_id)
    elif item_type == "visit":
        return _can_read_browser(cursor, item_id)
    else:
        return False


def resolve_permission_with_source(conn: sqlite3.Connection, item_type: str, item_id: int) -> Tuple[bool, str]:
    """
    Resolve permission and return the source that determined it.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        item_type: 'file', 'email', 'chat'
        item_id: Row ID in the relevant table

    Returns:
        Tuple of (resolved_permission, source_scope)
        e.g., (True, "folder:~/Work") or (False, "baseline")
    """
    cursor = conn.cursor()

    if item_type == "file":
        return _resolve_file_with_source(cursor, item_id)
    elif item_type == "email":
        return _resolve_email_with_source(cursor, item_id)
    elif item_type == "chat":
        return _resolve_chat_with_source(cursor, item_id)
    elif item_type == "project":
        return _resolve_project_permission_with_source(cursor, item_id)
    elif item_type == "client":
        return _resolve_client_permission_with_source(cursor, item_id)
    elif item_type == "folder":
        return _resolve_folder_permission_with_source(cursor, item_id)
    elif item_type == "visit":
        return _resolve_browser_with_source(cursor, item_id)
    else:
        return (False, "baseline")


def batch_resolve_permissions(
    conn: sqlite3.Connection, item_type: str, item_ids: List[int]
) -> Dict[int, Tuple[bool, str]]:
    """
    Resolve permissions for multiple items efficiently.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        item_type: 'file', 'email', 'chat', 'folder', 'project', 'client', 'visit'
        item_ids: List of row IDs

    Returns:
        Dict mapping item_id to (allowed, source) tuple
    """
    if not item_ids:
        return {}

    cursor = conn.cursor()

    if item_type == "file":
        return _batch_resolve_file_permissions(cursor, item_ids)
    elif item_type == "project":
        return _batch_resolve_project_permissions(cursor, item_ids)
    elif item_type == "client":
        return _batch_resolve_client_permissions(cursor, item_ids)
    elif item_type == "email":
        return _batch_resolve_email_permissions(cursor, item_ids)
    elif item_type == "chat":
        return _batch_resolve_chat_permissions(cursor, item_ids)
    elif item_type == "folder":
        return _batch_resolve_folder_permissions(cursor, item_ids)
    elif item_type == "visit":
        return _batch_resolve_browser_permissions(cursor, item_ids)
    else:
        return {id_: (False, "baseline") for id_ in item_ids}


def _batch_resolve_file_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for files."""
    conn = cursor.connection

    # Pre-fetch all permission policies
    cursor.execute("SELECT scope, setting FROM permission_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback (used when no specific policies match)
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

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
        SELECT file.id, file.path, file.project_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id IN ({placeholders})
        """,
        item_ids,
    )
    files = {row["id"]: row for row in rows}

    # Collect unique parent entity IDs for batch resolution
    project_ids = set()
    client_ids = set()
    for row in files.values():
        if row["project_id"]:
            project_ids.add(row["project_id"])
        if row["client_id"]:
            client_ids.add(row["client_id"])

    # Batch resolve parent entities
    project_permissions = batch_resolve_permissions(conn, "project", list(project_ids)) if project_ids else {}
    client_permissions = batch_resolve_permissions(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for file_id in item_ids:
        if file_id not in files:
            results[file_id] = (False, "not_found")
            continue

        row = files[file_id]
        policies: List[Tuple[Optional[bool], str]] = []

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

        # 3. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_permissions:
            allowed, src = project_permissions[project_id]
            if src != "baseline":
                policies.append((allowed, f"project:{project_id} (via {src})"))

        # 4. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_permissions:
            allowed, src = client_permissions[client_id]
            if src != "baseline":
                policies.append((allowed, f"client:{client_id} (via {src})"))

        # 5. Source policy
        source_scope = "source:files"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: deny wins
        for value, source in policies:
            if value is False:
                results[file_id] = (False, source)
                break
        else:
            for value, source in policies:
                if value is True:
                    results[file_id] = (True, source)
                    break
            else:
                results[file_id] = global_baseline

    return results


def _batch_resolve_project_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for projects."""
    cursor.execute("SELECT scope, setting FROM permission_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

    # Pre-fetch project data for client_id (chunked)
    rows = _chunked_query(
        cursor,
        "SELECT id, client_id FROM projects WHERE id IN ({placeholders})",
        item_ids,
    )
    projects = {row["id"]: row for row in rows}

    results = {}
    for project_id in item_ids:
        policies: List[Tuple[Optional[bool], str]] = []

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

        # Resolve: deny wins
        for value, source in policies:
            if value is False:
                results[project_id] = (False, source)
                break
        else:
            for value, source in policies:
                if value is True:
                    results[project_id] = (True, source)
                    break
            else:
                results[project_id] = global_baseline

    return results


def _batch_resolve_client_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for clients."""
    cursor.execute("SELECT scope, setting FROM permission_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

    results = {}
    for client_id in item_ids:
        policies: List[Tuple[Optional[bool], str]] = []

        # 1. Client-level policy
        client_scope = f"client:{client_id}"
        if client_scope in all_policies:
            policies.append((_resolve(all_policies[client_scope]), client_scope))

        # 2. Source policy for clients
        source_scope = "source:clients"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: deny wins
        for value, source in policies:
            if value is False:
                results[client_id] = (False, source)
                break
        else:
            for value, source in policies:
                if value is True:
                    results[client_id] = (True, source)
                    break
            else:
                results[client_id] = global_baseline

    return results


def _batch_resolve_email_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for emails."""
    conn = cursor.connection

    cursor.execute("SELECT scope, setting FROM permission_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

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
    project_permissions = batch_resolve_permissions(conn, "project", list(project_ids)) if project_ids else {}
    client_permissions = batch_resolve_permissions(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for email_id in item_ids:
        if email_id not in emails:
            results[email_id] = (False, "not_found")
            continue

        row = emails[email_id]
        policies: List[Tuple[Optional[bool], str]] = []

        # 1. Item-level policy
        item_scope = f"email:{email_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))

        # 2. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_permissions:
            allowed, src = project_permissions[project_id]
            if src != "baseline":
                policies.append((allowed, f"project:{project_id} (via {src})"))

        # 3. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_permissions:
            allowed, src = client_permissions[client_id]
            if src != "baseline":
                policies.append((allowed, f"client:{client_id} (via {src})"))

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

        # Resolve: deny wins
        for value, source in policies:
            if value is False:
                results[email_id] = (False, source)
                break
        else:
            for value, source in policies:
                if value is True:
                    results[email_id] = (True, source)
                    break
            else:
                results[email_id] = global_baseline

    return results


def _batch_resolve_chat_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for chats."""
    conn = cursor.connection

    cursor.execute("SELECT scope, setting FROM permission_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

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
    project_permissions = batch_resolve_permissions(conn, "project", list(project_ids)) if project_ids else {}
    client_permissions = batch_resolve_permissions(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for chat_id in item_ids:
        if chat_id not in convs:
            results[chat_id] = (False, "not_found")
            continue

        row = convs[chat_id]
        policies: List[Tuple[Optional[bool], str]] = []

        # 1. Item-level policy
        item_scope = f"chat:{chat_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))

        # 2. Project-level via full resolution (skip baseline)
        project_id = row["project_id"]
        if project_id and project_id in project_permissions:
            allowed, src = project_permissions[project_id]
            if src != "baseline":
                policies.append((allowed, f"project:{project_id} (via {src})"))

        # 3. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_permissions:
            allowed, src = client_permissions[client_id]
            if src != "baseline":
                policies.append((allowed, f"client:{client_id} (via {src})"))

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

        # Resolve: deny wins
        for value, source in policies:
            if value is False:
                results[chat_id] = (False, source)
                break
        else:
            for value, source in policies:
                if value is True:
                    results[chat_id] = (True, source)
                    break
            else:
                results[chat_id] = global_baseline

    return results


def _batch_resolve_folder_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for folders.

    Resolution chain: folder:{id} → folder prefix → project:{id} → client:{id} → source:folders
    """
    conn = cursor.connection

    cursor.execute("SELECT scope, setting FROM permission_policies")
    all_policies = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    # Global policy fallback
    if "global" in all_policies:
        global_baseline = (_resolve(all_policies["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

    folder_policies = [
        (scope, setting)
        for scope, setting in all_policies.items()
        if scope.startswith("folder:") and is_folder_path_scope(scope)
    ]
    folder_policies.sort(key=lambda x: len(x[0]), reverse=True)

    rows = _chunked_query(
        cursor,
        """
        SELECT folder.id, folder.path, folder.project_id,
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
    project_permissions = batch_resolve_permissions(conn, "project", list(project_ids)) if project_ids else {}
    client_permissions = batch_resolve_permissions(conn, "client", list(client_ids)) if client_ids else {}

    results = {}
    for folder_id in item_ids:
        if folder_id not in folders:
            results[folder_id] = (False, "not_found")
            continue

        row = folders[folder_id]
        policies: List[Tuple[Optional[bool], str]] = []

        # 1. Item-level policy
        item_scope = f"folder:{folder_id}"
        if item_scope in all_policies:
            policies.append((_resolve(all_policies[item_scope]), item_scope))

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
        if project_id and project_id in project_permissions:
            allowed, src = project_permissions[project_id]
            if src != "baseline":
                policies.append((allowed, f"project:{project_id} (via {src})"))

        # 4. Client-level via full resolution (skip baseline)
        client_id = row["client_id"]
        if client_id and client_id in client_permissions:
            allowed, src = client_permissions[client_id]
            if src != "baseline":
                policies.append((allowed, f"client:{client_id} (via {src})"))

        # 5. Source policy
        source_scope = "source:folders"
        if source_scope in all_policies:
            policies.append((_resolve(all_policies[source_scope]), source_scope))

        # Resolve: deny wins
        for value, source in policies:
            if value is False:
                results[folder_id] = (False, source)
                break
        else:
            for value, source in policies:
                if value is True:
                    results[folder_id] = (True, source)
                    break
            else:
                results[folder_id] = global_baseline

    return results


def _resolve(value: Optional[str]) -> Optional[bool]:
    """Convert a permission value to bool or None (no policy)."""
    if value == "allow":
        return True
    if value == "deny":
        return False
    return None  # 'inherit' or NULL means no policy


def _get_policy(cursor, scope: str) -> Optional[bool]:
    """Look up a permission_policies row."""
    cursor.execute("SELECT setting FROM permission_policies WHERE scope = ?", (scope,))
    row = cursor.fetchone()
    if row:
        return _resolve(row["setting"])
    return None


def _get_global_baseline(cursor) -> Tuple[bool, str]:
    """Get global policy or fall back to hardcoded baseline."""
    row = cursor.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
    if row:
        return (_resolve(row["setting"]), "global")
    return (BASELINE_PERMISSION, "baseline")


def _resolve_parent_permission_with_source(
    conn: sqlite3.Connection, item_type: str, item_id: int
) -> Optional[Tuple[bool, str]]:
    """Resolve parent entity permission, returning None if baseline.

    This is used when resolving file permissions to check parent
    entities (project, client). If the parent resolves to baseline,
    we return None so that baseline doesn't propagate down the hierarchy.
    """
    allowed, source = resolve_permission_with_source(conn, item_type, item_id)
    if source == "baseline":
        return None
    return (allowed, source)


def _can_read_file(cursor, file_id: int) -> bool:
    """Resolve read permission for a file using policies."""
    resolved, _ = _resolve_file_with_source(cursor, file_id)
    return resolved


def _resolve_file_with_source(cursor, file_id: int) -> Tuple[bool, str]:
    """Resolve file permission with source tracking."""
    cursor.execute(
        """
        SELECT file.path, file.project_id,
               COALESCE(file.client_id, project.client_id) AS client_id
        FROM files file
        LEFT JOIN projects project ON file.project_id = project.id
        WHERE file.id = ?
    """,
        (file_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (False, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[bool], str]] = []

    # 1. Item-level policy (file:{id})
    item_policy = _get_policy(cursor, f"file:{file_id}")
    if item_policy is not None:
        policies.append((item_policy, f"file:{file_id}"))

    # 2. Folder prefix match (most specific first)
    path = row["path"] or ""
    if path:
        cursor.execute(
            """
            SELECT scope, setting FROM permission_policies
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
        result = _resolve_parent_permission_with_source(conn, "project", project_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"project:{project_id} (via {src})"))

    # 4. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "client", client_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"client:{client_id} (via {src})"))

    # 5. Source policy
    source_policy = _get_policy(cursor, "source:files")
    if source_policy is not None:
        policies.append((source_policy, "source:files"))

    # DENY-WINS RESOLUTION among matching policies only:
    # If ANY policy is deny, return deny
    for value, source in policies:
        if value is False:
            return (False, source)

    # Otherwise, first allow policy wins
    for value, source in policies:
        if value is True:
            return (True, source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _can_read_email(cursor, email_id: int) -> bool:
    """Resolve read permission for an email using policies."""
    resolved, _ = _resolve_email_with_source(cursor, email_id)
    return resolved


def _resolve_email_with_source(cursor, email_id: int) -> Tuple[bool, str]:
    """Resolve email permission with source tracking.

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
        return (False, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[bool], str]] = []

    # 1. Item-level policy (email:{id})
    item_policy = _get_policy(cursor, f"email:{email_id}")
    if item_policy is not None:
        policies.append((item_policy, f"email:{email_id}"))

    # 2. Project-level via full resolution (project:{id}) - skip baseline
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "project", project_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"project:{project_id} (via {src})"))

    # 3. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "client", client_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"client:{client_id} (via {src})"))

    # 4. Account-level policy (e.g., account:personal)
    account = row["account"] or ""
    if account:
        account_policy = _get_policy(cursor, f"account:{account}")
        if account_policy is not None:
            policies.append((account_policy, f"account:{account}"))

    # 5. Source policy
    source_policy = _get_policy(cursor, "source:emails")
    if source_policy is not None:
        policies.append((source_policy, "source:emails"))

    # DENY-WINS RESOLUTION among matching policies only:
    # If ANY policy is deny, return deny
    for value, source in policies:
        if value is False:
            return (False, source)

    # Otherwise, first allow policy wins
    for value, source in policies:
        if value is True:
            return (True, source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _can_read_chat(cursor, chat_id: int) -> bool:
    """Resolve read permission for a chat using policies."""
    resolved, _ = _resolve_chat_with_source(cursor, chat_id)
    return resolved


def _resolve_chat_with_source(cursor, chat_id: int) -> Tuple[bool, str]:
    """Resolve chat permission with source tracking.

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
        return (False, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[bool], str]] = []

    # 1. Item-level policy (chat:{id})
    item_policy = _get_policy(cursor, f"chat:{chat_id}")
    if item_policy is not None:
        policies.append((item_policy, f"chat:{chat_id}"))

    # 2. Project-level via full resolution (project:{id}) - skip baseline
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "project", project_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"project:{project_id} (via {src})"))

    # 3. Client-level via full resolution (client:{id}) - skip baseline
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "client", client_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"client:{client_id} (via {src})"))

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

    # DENY-WINS RESOLUTION among matching policies only:
    # If ANY policy is deny, return deny
    for value, source in policies:
        if value is False:
            return (False, source)

    # Otherwise, first allow policy wins
    for value, source in policies:
        if value is True:
            return (True, source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _resolve_project_permission_with_source(cursor, project_id: int) -> Tuple[bool, str]:
    """Resolve project permission with source tracking.

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
        return (BASELINE_PERMISSION, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[bool], str]] = []

    # 1. Project-level policy (project:{id})
    project_policy = _get_policy(cursor, f"project:{project_id}")
    if project_policy is not None:
        policies.append((project_policy, f"project:{project_id}"))

    # 2. Client-level policy via full resolution (skip baseline)
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "client", client_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"client:{client_id} (via {src})"))

    # 3. Source policy for projects
    source_policy = _get_policy(cursor, "source:projects")
    if source_policy is not None:
        policies.append((source_policy, "source:projects"))

    # DENY-WINS RESOLUTION among matching policies only:
    for value, source in policies:
        if value is False:
            return (False, source)

    for value, source in policies:
        if value is True:
            return (True, source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _resolve_client_permission_with_source(cursor, client_id: int) -> Tuple[bool, str]:
    """Resolve client permission with source tracking.

    Chain: client:{id} -> source:clients
    """
    cursor.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        return (BASELINE_PERMISSION, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[bool], str]] = []

    # 1. Client-level policy (client:{id})
    client_policy = _get_policy(cursor, f"client:{client_id}")
    if client_policy is not None:
        policies.append((client_policy, f"client:{client_id}"))

    # 2. Source policy for clients
    source_policy = _get_policy(cursor, "source:clients")
    if source_policy is not None:
        policies.append((source_policy, "source:clients"))

    # DENY-WINS RESOLUTION among matching policies only:
    for value, source in policies:
        if value is False:
            return (False, source)

    for value, source in policies:
        if value is True:
            return (True, source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _resolve_folder_permission_with_source(cursor, folder_id: int) -> Tuple[bool, str]:
    """Resolve folder permission with source tracking.

    Chain: folder:{id} → folder prefix → project:{id} → client:{id} → source:folders
    """
    cursor.execute(
        """
        SELECT folder.path, folder.project_id,
               COALESCE(folder.client_id, project.client_id) AS client_id
        FROM folders folder
        LEFT JOIN projects project ON folder.project_id = project.id
        WHERE folder.id = ?
    """,
        (folder_id,),
    )
    row = cursor.fetchone()
    if not row:
        return (BASELINE_PERMISSION, "not_found")

    # Collect matching policies only (not baseline)
    policies: List[Tuple[Optional[bool], str]] = []

    # 1. Item-level policy (folder:{id})
    item_policy = _get_policy(cursor, f"folder:{folder_id}")
    if item_policy is not None:
        policies.append((item_policy, f"folder:{folder_id}"))

    # 2. Folder prefix match (most specific first)
    path = row["path"] or ""
    if path:
        cursor.execute(
            """
            SELECT scope, setting FROM permission_policies
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

    # 3. Project-level via full resolution (skip baseline)
    project_id = row["project_id"]
    if project_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "project", project_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"project:{project_id} (via {src})"))

    # 4. Client-level via full resolution (skip baseline)
    client_id = row["client_id"]
    if client_id:
        conn = cursor.connection
        result = _resolve_parent_permission_with_source(conn, "client", client_id)
        if result:
            allowed, src = result
            policies.append((allowed, f"client:{client_id} (via {src})"))

    # 5. Source policy
    source_policy = _get_policy(cursor, "source:folders")
    if source_policy is not None:
        policies.append((source_policy, "source:folders"))

    # DENY-WINS RESOLUTION among matching policies only:
    for value, source in policies:
        if value is False:
            return (False, source)

    for value, source in policies:
        if value is True:
            return (True, source)

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _can_read_browser(cursor, browser_id: int) -> bool:
    """Resolve read permission for a browser history item using policies.

    Browser history only has source-level policies (no item/folder/project/client hierarchy).
    """
    resolved, _ = _resolve_browser_with_source(cursor, browser_id)
    return resolved


def _resolve_browser_with_source(cursor, browser_id: int) -> Tuple[bool, str]:
    """Resolve browser history permission with source tracking.

    Browser history has no hierarchy - only source-level policy applies.
    Chain: source:browser → baseline
    """
    # Verify item exists
    cursor.execute("SELECT id FROM visits WHERE id = ?", (browser_id,))
    row = cursor.fetchone()
    if not row:
        return (False, "not_found")

    # Only source policy applies
    source_policy = _get_policy(cursor, "source:browser")
    if source_policy is not None:
        return (source_policy, "source:browser")

    # No policies matched → use global policy or baseline
    return _get_global_baseline(cursor)


def _batch_resolve_browser_permissions(cursor, item_ids: List[int]) -> Dict[int, Tuple[bool, str]]:
    """Batch resolve permissions for browser history items.

    Since browser history only uses source-level policy, we can resolve once
    and apply to all items.
    """
    cursor.execute("SELECT scope, setting FROM permission_policies WHERE scope IN ('source:browser', 'global')")
    rows = {row["scope"]: row["setting"] for row in cursor.fetchall()}

    if "source:browser" in rows:
        source_permission = _resolve(rows["source:browser"])
        source = "source:browser"
    else:
        source_permission = None

    # Global policy fallback
    if "global" in rows:
        global_baseline = (_resolve(rows["global"]), "global")
    else:
        global_baseline = (BASELINE_PERMISSION, "baseline")

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
            results[item_id] = (False, "not_found")
        elif source_permission is not None:
            results[item_id] = (source_permission, source)
        else:
            results[item_id] = global_baseline

    return results
