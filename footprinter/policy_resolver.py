"""
Shared policy resolution engine for permissions and visibility.

Both domains share identical resolution mechanics — dispatch, hierarchy chains,
batch optimization, winner functions — differing only in:
  1. Policy table (permission_policies vs visibility_policies)
  2. Value parser (allow/deny→bool vs hidden/opaque/full→VisibilityState)
  3. Winner function (deny-wins vs most-restrictive-wins)
  4. Baseline value (True vs "opaque")

This module provides the parameterized resolver that both domains delegate to.
"""

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from footprinter.db.policies import is_folder_path_scope
from footprinter.db.sql_utils import chunked_query as _chunked_query


def deny_wins(
    policies: List[Tuple[Optional[bool], str]],
    fallback: Tuple[bool, str],
) -> Tuple[bool, str]:
    for value, source in policies:
        if value is False:
            return (False, source)
    for value, source in policies:
        if value is True:
            return (True, source)
    return fallback


def most_restrictive_wins(
    policies: List[Tuple[Optional[Any], str]],
    fallback: Tuple[Any, str],
) -> Tuple[Any, str]:
    for value, source in policies:
        if value == "hidden":
            return ("hidden", source)
    for value, source in policies:
        if value == "opaque":
            return ("opaque", source)
    for value, source in policies:
        if value == "full":
            return ("full", source)
    return fallback


@dataclass(frozen=True)
class ItemSpec:
    entity_name: str
    source_scope: str
    single_fetch_sql: str
    batch_fetch_sql: Optional[str]
    parent_refs: tuple  # ((entity_type, id_field, batch_recursive), ...)
    not_found_value: Any
    not_found_on_missing: bool = True
    has_path: bool = False
    has_folder_fk: bool = False
    has_account: bool = False
    has_ancestor_walk: bool = False


class PolicyResolver:
    __slots__ = ("policy_table", "parse_value", "pick_winner", "baseline")

    def __init__(
        self,
        policy_table: str,
        parse_value: Callable[[Optional[str]], Any],
        pick_winner: Callable[..., Tuple[Any, str]],
        baseline: Any,
    ):
        self.policy_table = policy_table
        self.parse_value = parse_value
        self.pick_winner = pick_winner
        self.baseline = baseline

    def get_policy(self, cursor: sqlite3.Cursor, scope: str) -> Any:
        cursor.execute(
            f"SELECT setting FROM {self.policy_table} WHERE scope = ?",
            (scope,),
        )
        row = cursor.fetchone()
        if row:
            return self.parse_value(row["setting"])
        return None

    def get_global_baseline(self, cursor: sqlite3.Cursor) -> Tuple[Any, str]:
        row = cursor.execute(
            f"SELECT setting FROM {self.policy_table} WHERE scope = 'global'"
        ).fetchone()
        if row:
            return (self.parse_value(row["setting"]), "global")
        return (self.baseline, "baseline")

    def prefetch_all(self, cursor: sqlite3.Cursor) -> Dict[str, str]:
        cursor.execute(f"SELECT scope, setting FROM {self.policy_table}")
        return {row["scope"]: row["setting"] for row in cursor.fetchall()}

    def global_baseline_from_prefetch(
        self, all_policies: Dict[str, str]
    ) -> Tuple[Any, str]:
        if "global" in all_policies:
            return (self.parse_value(all_policies["global"]), "global")
        return (self.baseline, "baseline")

    def folder_path_policies(
        self, all_policies: Dict[str, str]
    ) -> List[Tuple[str, str]]:
        result = [
            (scope, setting)
            for scope, setting in all_policies.items()
            if scope.startswith("folder:") and is_folder_path_scope(scope)
        ]
        result.sort(key=lambda x: len(x[0]), reverse=True)
        return result

    def match_folder_prefix_single(
        self, cursor: sqlite3.Cursor, path: str
    ) -> Optional[Tuple[Any, str]]:
        cursor.execute(
            f"""
            SELECT scope, setting FROM {self.policy_table}
            WHERE scope LIKE 'folder:%'
            ORDER BY LENGTH(scope) DESC
        """
        )
        for row in cursor.fetchall():
            if not is_folder_path_scope(row["scope"]):
                continue
            prefix = row["scope"][len("folder:"):]
            if prefix.startswith("~"):
                prefix = os.path.expanduser(prefix)
            if path.startswith(prefix):
                return (self.parse_value(row["setting"]), row["scope"])
        return None

    def resolve_parent(
        self,
        conn: sqlite3.Connection,
        item_type: str,
        item_id: int,
        resolve_with_source_fn: Callable,
    ) -> Optional[Tuple[Any, str]]:
        value, source = resolve_with_source_fn(conn, item_type, item_id)
        if source == "baseline":
            return None
        return (value, source)


def walk_ancestor_policies(
    cursor: sqlite3.Cursor,
    folder_id: int,
    parent_folder_id: Optional[int],
    lookup_policy: Callable[[str], Any],
) -> Optional[Tuple[Any, str]]:
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


def resolve_single(
    resolver: PolicyResolver,
    conn: sqlite3.Connection,
    spec: ItemSpec,
    item_id: int,
    resolve_with_source_fn: Callable,
) -> Tuple[Any, str]:
    cursor = conn.cursor()
    cursor.execute(spec.single_fetch_sql, (item_id,))
    row = cursor.fetchone()
    if not row:
        return (spec.not_found_value, "not_found")

    policies: list = []

    # 1. Item-level policy
    item_policy = resolver.get_policy(cursor, f"{spec.entity_name}:{item_id}")
    if item_policy is not None:
        policies.append((item_policy, f"{spec.entity_name}:{item_id}"))
    elif spec.has_ancestor_walk:
        parent_folder_id = row["parent_folder_id"]
        result = walk_ancestor_policies(
            cursor,
            item_id,
            parent_folder_id,
            lambda scope: resolver.get_policy(cursor, scope),
        )
        if result:
            policies.append(result)

    # 2. Folder prefix match
    if spec.has_path:
        path = row["path"] or ""
        if path:
            match = resolver.match_folder_prefix_single(cursor, path)
            if match:
                policies.append(match)

    # 3. Folder FK (visibility files only)
    if spec.has_folder_fk:
        folder_id = row["folder_id"]
        if folder_id:
            result = resolver.resolve_parent(
                conn, "folder", folder_id, resolve_with_source_fn
            )
            if result:
                val, src = result
                policies.append((val, f"folder:{folder_id} (via {src})"))

    # 4. Parent resolution
    for parent_type, id_field, _ in spec.parent_refs:
        parent_id = row[id_field]
        if parent_id:
            result = resolver.resolve_parent(
                conn, parent_type, parent_id, resolve_with_source_fn
            )
            if result:
                val, src = result
                policies.append((val, f"{parent_type}:{parent_id} (via {src})"))

    # 5. Account-level policy
    if spec.has_account:
        account = row["account"] or ""
        if account:
            acct_policy = resolver.get_policy(cursor, f"account:{account}")
            if acct_policy is not None:
                policies.append((acct_policy, f"account:{account}"))

    # 6. Source policy
    source_policy = resolver.get_policy(cursor, spec.source_scope)
    if source_policy is not None:
        policies.append((source_policy, spec.source_scope))

    return resolver.pick_winner(policies, resolver.get_global_baseline(cursor))


def resolve_batch(
    resolver: PolicyResolver,
    conn: sqlite3.Connection,
    spec: ItemSpec,
    item_ids: List[int],
    batch_resolve_fn: Callable,
) -> Dict[int, Tuple[Any, str]]:
    if not item_ids:
        return {}

    cursor = conn.cursor()
    all_policies = resolver.prefetch_all(cursor)
    global_baseline = resolver.global_baseline_from_prefetch(all_policies)

    folder_policies = (
        resolver.folder_path_policies(all_policies) if spec.has_path else []
    )

    # Fetch item data
    if spec.batch_fetch_sql:
        rows = _chunked_query(cursor, spec.batch_fetch_sql, item_ids)
        items: Optional[Dict[int, dict]] = {row["id"]: dict(row) for row in rows}
    else:
        items = None

    # Collect parent entity IDs for batch resolution
    parent_id_sets: Dict[str, set] = {}
    if items is not None:
        for parent_type, id_field, recursive in spec.parent_refs:
            if recursive:
                ids = {
                    row[id_field]
                    for row in items.values()
                    if row.get(id_field)
                }
                if ids:
                    parent_id_sets[parent_type] = ids

        if spec.has_folder_fk:
            folder_ids = {
                row["folder_id"]
                for row in items.values()
                if row.get("folder_id")
            }
            if folder_ids:
                parent_id_sets["folder"] = folder_ids

    # Batch-resolve parent entities
    parent_results: Dict[str, Dict[int, Tuple]] = {}
    for parent_type, ids in parent_id_sets.items():
        parent_results[parent_type] = batch_resolve_fn(
            conn, parent_type, list(ids)
        )

    results: Dict[int, Tuple[Any, str]] = {}
    for item_id in item_ids:
        # Determine the row for this item
        if items is not None:
            if item_id not in items:
                if spec.not_found_on_missing:
                    results[item_id] = (spec.not_found_value, "not_found")
                    continue
                else:
                    row: dict = {}
            else:
                row = items[item_id]
        else:
            row = {}

        policies: list = []

        # 1. Item-level policy
        item_scope = f"{spec.entity_name}:{item_id}"
        if item_scope in all_policies:
            policies.append(
                (resolver.parse_value(all_policies[item_scope]), item_scope)
            )
        elif spec.has_ancestor_walk:
            parent_folder_id = row.get("parent_folder_id")
            if parent_folder_id is not None:
                result = walk_ancestor_policies(
                    cursor,
                    item_id,
                    parent_folder_id,
                    lambda scope: resolver.parse_value(all_policies.get(scope)),
                )
                if result:
                    policies.append(result)

        # 2. Folder prefix match
        if spec.has_path:
            path = row.get("path") or ""
            if path:
                for scope, setting in folder_policies:
                    prefix = scope[len("folder:"):]
                    if prefix.startswith("~"):
                        prefix = os.path.expanduser(prefix)
                    if path.startswith(prefix):
                        policies.append(
                            (resolver.parse_value(setting), scope)
                        )
                        break

        # 3. Folder FK (visibility files only)
        if spec.has_folder_fk:
            fk_folder_id = row.get("folder_id")
            if fk_folder_id and fk_folder_id in parent_results.get("folder", {}):
                val, src = parent_results["folder"][fk_folder_id]
                if src != "baseline":
                    policies.append(
                        (val, f"folder:{fk_folder_id} (via {src})")
                    )

        # 4. Parent resolution
        for parent_type, id_field, recursive in spec.parent_refs:
            parent_id = row.get(id_field)
            if not parent_id:
                continue
            if recursive:
                if parent_id in parent_results.get(parent_type, {}):
                    val, src = parent_results[parent_type][parent_id]
                    if src != "baseline":
                        policies.append(
                            (val, f"{parent_type}:{parent_id} (via {src})")
                        )
            else:
                parent_scope = f"{parent_type}:{parent_id}"
                if parent_scope in all_policies:
                    policies.append(
                        (
                            resolver.parse_value(all_policies[parent_scope]),
                            parent_scope,
                        )
                    )

        # 5. Account-level policy
        if spec.has_account:
            account = row.get("account") or ""
            if account:
                acct_scope = f"account:{account}"
                if acct_scope in all_policies:
                    policies.append(
                        (
                            resolver.parse_value(all_policies[acct_scope]),
                            acct_scope,
                        )
                    )

        # 6. Source policy
        if spec.source_scope in all_policies:
            policies.append(
                (
                    resolver.parse_value(all_policies[spec.source_scope]),
                    spec.source_scope,
                )
            )

        results[item_id] = resolver.pick_winner(policies, global_baseline)

    return results
