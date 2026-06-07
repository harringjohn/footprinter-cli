"""Tests for the shared policy resolver engine."""

import sqlite3

import pytest

from footprinter.policy_resolver import (
    ItemSpec,
    PolicyResolver,
    deny_wins,
    most_restrictive_wins,
    resolve_batch,
    resolve_single,
    walk_ancestor_policies,
)


# ── Winner Function Tests ─────────────────────────────────────────────


class TestDenyWins:

    def test_empty_returns_fallback(self):
        assert deny_wins([], (True, "baseline")) == (True, "baseline")

    def test_single_deny(self):
        policies = [(False, "file:1")]
        assert deny_wins(policies, (True, "baseline")) == (False, "file:1")

    def test_single_allow(self):
        policies = [(True, "file:1")]
        assert deny_wins(policies, (True, "baseline")) == (True, "file:1")

    def test_deny_beats_allow(self):
        policies = [(True, "source:files"), (False, "file:1")]
        assert deny_wins(policies, (True, "baseline")) == (False, "file:1")

    def test_deny_beats_allow_regardless_of_order(self):
        policies = [(False, "file:1"), (True, "source:files")]
        assert deny_wins(policies, (True, "baseline")) == (False, "file:1")

    def test_multiple_allows_first_wins(self):
        policies = [(True, "file:1"), (True, "source:files")]
        assert deny_wins(policies, (True, "baseline")) == (True, "file:1")

    def test_none_values_skipped(self):
        policies = [(None, "file:1"), (True, "source:files")]
        assert deny_wins(policies, (True, "baseline")) == (True, "source:files")


class TestMostRestrictiveWins:

    def test_empty_returns_fallback(self):
        assert most_restrictive_wins([], ("opaque", "baseline")) == ("opaque", "baseline")

    def test_hidden_wins_all(self):
        policies = [("full", "file:1"), ("hidden", "project:1"), ("opaque", "source:files")]
        assert most_restrictive_wins(policies, ("opaque", "baseline")) == ("hidden", "project:1")

    def test_opaque_beats_full(self):
        policies = [("full", "file:1"), ("opaque", "project:1")]
        assert most_restrictive_wins(policies, ("opaque", "baseline")) == ("opaque", "project:1")

    def test_full_alone(self):
        policies = [("full", "file:1")]
        assert most_restrictive_wins(policies, ("opaque", "baseline")) == ("full", "file:1")

    def test_order_irrelevant_hidden_wins(self):
        policies = [("opaque", "source:files"), ("full", "file:1"), ("hidden", "client:1")]
        assert most_restrictive_wins(policies, ("opaque", "baseline")) == ("hidden", "client:1")

    def test_none_values_skipped(self):
        policies = [(None, "file:1"), ("full", "source:files")]
        assert most_restrictive_wins(policies, ("opaque", "baseline")) == ("full", "source:files")


# ── Helper Tests ──────────────────────────────────────────────────────


def _perm_resolve(value):
    if value == "allow":
        return True
    if value == "deny":
        return False
    return None


def _vis_resolve(value):
    if value in ("hidden", "opaque", "full"):
        return value
    return None


@pytest.fixture
def resolver_db(tool_db):
    """Database with seed data for resolver tests."""
    cursor = tool_db.cursor()
    cursor.execute(
        "INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Client A', 'a', 'external')"
    )
    cursor.execute(
        "INSERT INTO projects (id, name, client_id) VALUES (1, 'Project A', 1)"
    )
    cursor.execute(
        "INSERT INTO folders (id, name, path, relative_path, source, project_id) "
        "VALUES (1, 'folder', '/test/folder', 'test/folder', 'local', 1)"
    )
    cursor.execute(
        "INSERT INTO files (id, path, name, source, content_type, project_id, client_id) "
        "VALUES (1, '/test/folder/a.txt', 'a.txt', 'local', 'text/plain', 1, 1)"
    )
    tool_db.commit()
    return tool_db


class TestPolicyResolverHelpers:

    def test_get_policy_found(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('file:1', 'deny')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)
        assert r.get_policy(cursor, "file:1") is False

    def test_get_policy_missing(self, resolver_db):
        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)
        cursor = resolver_db.cursor()
        assert r.get_policy(cursor, "file:999") is None

    def test_get_global_baseline_with_global(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('global', 'deny')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)
        assert r.get_global_baseline(cursor) == (False, "global")

    def test_get_global_baseline_without_global(self, resolver_db):
        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)
        cursor = resolver_db.cursor()
        assert r.get_global_baseline(cursor) == (True, "baseline")

    def test_visibility_baseline(self, resolver_db):
        r = PolicyResolver("visibility_policies", _vis_resolve, most_restrictive_wins, "opaque")
        cursor = resolver_db.cursor()
        assert r.get_global_baseline(cursor) == ("opaque", "baseline")


# ── Single-Item Resolution Tests ──────────────────────────────────────


_CLIENT_SPEC = ItemSpec(
    entity_name="client",
    source_scope="source:clients",
    single_fetch_sql="SELECT id FROM clients WHERE id = ?",
    batch_fetch_sql=None,
    parent_refs=(),
    not_found_value=True,
    not_found_on_missing=False,
)

_PROJECT_SPEC = ItemSpec(
    entity_name="project",
    source_scope="source:projects",
    single_fetch_sql="SELECT client_id FROM projects WHERE id = ?",
    batch_fetch_sql="SELECT id, client_id FROM projects WHERE id IN ({placeholders})",
    parent_refs=(("client", "client_id", False),),
    not_found_value=True,
    not_found_on_missing=False,
)


class TestResolveSingle:

    def test_client_deny_wins(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('client:1', 'deny')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        def resolve_fn(conn, item_type, item_id):
            return resolve_single(r, conn, specs[item_type], item_id, resolve_fn)

        specs = {"client": _CLIENT_SPEC}
        result = resolve_single(r, resolver_db, _CLIENT_SPEC, 1, resolve_fn)
        assert result == (False, "client:1")

    def test_client_source_fallback(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('source:clients', 'allow')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        def resolve_fn(conn, item_type, item_id):
            return resolve_single(r, conn, specs[item_type], item_id, resolve_fn)

        specs = {"client": _CLIENT_SPEC}
        result = resolve_single(r, resolver_db, _CLIENT_SPEC, 1, resolve_fn)
        assert result == (True, "source:clients")

    def test_client_baseline_fallback(self, resolver_db):
        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        def resolve_fn(conn, item_type, item_id):
            return resolve_single(r, conn, specs[item_type], item_id, resolve_fn)

        specs = {"client": _CLIENT_SPEC}
        result = resolve_single(r, resolver_db, _CLIENT_SPEC, 1, resolve_fn)
        assert result == (True, "baseline")

    def test_client_not_found(self, resolver_db):
        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        def resolve_fn(conn, item_type, item_id):
            return resolve_single(r, conn, specs[item_type], item_id, resolve_fn)

        specs = {"client": _CLIENT_SPEC}
        result = resolve_single(r, resolver_db, _CLIENT_SPEC, 999, resolve_fn)
        assert result == (True, "not_found")

    def test_project_resolves_client_parent(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('client:1', 'deny')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        specs = {"client": _CLIENT_SPEC, "project": _PROJECT_SPEC}

        def resolve_fn(conn, item_type, item_id):
            return resolve_single(r, conn, specs[item_type], item_id, resolve_fn)

        result = resolve_single(r, resolver_db, _PROJECT_SPEC, 1, resolve_fn)
        assert result[0] is False
        assert "client:1" in result[1]

    def test_visibility_most_restrictive(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO visibility_policies (scope, setting) VALUES ('client:1', 'full')"
        )
        cursor.execute(
            "INSERT INTO visibility_policies (scope, setting) VALUES ('source:clients', 'hidden')"
        )
        resolver_db.commit()

        r = PolicyResolver("visibility_policies", _vis_resolve, most_restrictive_wins, "opaque")
        vis_client_spec = ItemSpec(
            entity_name="client",
            source_scope="source:clients",
            single_fetch_sql="SELECT id FROM clients WHERE id = ?",
            batch_fetch_sql=None,
            parent_refs=(),
            not_found_value="opaque",
            not_found_on_missing=False,
        )

        def resolve_fn(conn, item_type, item_id):
            return resolve_single(r, conn, vis_client_spec, item_id, resolve_fn)

        result = resolve_single(r, resolver_db, vis_client_spec, 1, resolve_fn)
        assert result == ("hidden", "source:clients")


# ── Batch Resolution Tests ────────────────────────────────────────────


class TestResolveBatch:

    def test_batch_empty_ids(self, resolver_db):
        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)
        result = resolve_batch(r, resolver_db, _CLIENT_SPEC, [], lambda *a: {})
        assert result == {}

    def test_batch_client_deny(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('client:1', 'deny')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        specs = {"client": _CLIENT_SPEC}

        def batch_fn(conn, item_type, ids):
            return resolve_batch(r, conn, specs[item_type], ids, batch_fn)

        result = resolve_batch(r, resolver_db, _CLIENT_SPEC, [1], batch_fn)
        assert result[1] == (False, "client:1")

    def test_batch_client_baseline(self, resolver_db):
        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        def batch_fn(conn, item_type, ids):
            return resolve_batch(r, conn, _CLIENT_SPEC, ids, batch_fn)

        result = resolve_batch(r, resolver_db, _CLIENT_SPEC, [1], batch_fn)
        assert result[1] == (True, "baseline")

    def test_batch_project_flat_client_lookup(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO permission_policies (scope, setting) VALUES ('client:1', 'deny')"
        )
        resolver_db.commit()

        r = PolicyResolver("permission_policies", _perm_resolve, deny_wins, True)

        specs = {"client": _CLIENT_SPEC, "project": _PROJECT_SPEC}

        def batch_fn(conn, item_type, ids):
            return resolve_batch(r, conn, specs[item_type], ids, batch_fn)

        result = resolve_batch(r, resolver_db, _PROJECT_SPEC, [1], batch_fn)
        assert result[1][0] is False
        assert result[1][1] == "client:1"


# ── Ancestor Walk Tests ───────────────────────────────────────────────


class TestAncestorWalk:

    def test_finds_nearest_ancestor(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, parent_folder_id) "
            "VALUES (2, 'sub', '/test/sub', 'test/sub', 'local', 1, 1)"
        )
        cursor.execute(
            "INSERT INTO folders (id, name, path, relative_path, source, project_id, parent_folder_id) "
            "VALUES (3, 'deep', '/test/sub/deep', 'test/sub/deep', 'local', 1, 2)"
        )
        resolver_db.commit()

        policies = {"folder:1": "hidden", "folder:2": "opaque"}
        result = walk_ancestor_policies(
            cursor, 3, 2, lambda scope: _vis_resolve(policies.get(scope))
        )
        assert result == ("opaque", "folder:2")

    def test_no_ancestor_policy(self, resolver_db):
        cursor = resolver_db.cursor()
        result = walk_ancestor_policies(
            cursor, 1, None, lambda scope: None
        )
        assert result is None

    def test_skips_visited_prevents_cycle(self, resolver_db):
        cursor = resolver_db.cursor()
        cursor.execute("UPDATE folders SET parent_folder_id = 1 WHERE id = 1")
        resolver_db.commit()

        result = walk_ancestor_policies(
            cursor, 1, 1, lambda scope: None
        )
        assert result is None
