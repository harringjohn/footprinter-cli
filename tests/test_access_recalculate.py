"""Tests for footprinter.access — recalculation engine write-back."""

import sqlite3

import pytest


@pytest.fixture
def conn(tool_db):
    """Full-schema database for recalculation tests."""
    yield tool_db


def _seed_entities(conn):
    """Insert minimal rows across all entity tables for testing."""
    cur = conn.cursor()

    # Clients
    cur.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (5, 'Acme', 'acme', 'external')")

    # Projects
    cur.execute(
        "INSERT INTO projects (id, project_name, root_path, client_id) VALUES (3, 'Widget', '/Users/me/Work/widget', 5)"
    )

    # Files
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id) "
        "VALUES (1, 'local', 'a.py', '/Users/me/Work/widget/a.py', 'work', 3)"
    )
    cur.execute(
        "INSERT INTO files (id, source, name, path, account, project_id) "
        "VALUES (2, 'local', 'b.py', '/Users/me/Personal/b.py', 'personal', NULL)"
    )

    # Emails
    cur.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id, client_id) "
        "VALUES (10, 'msg1', 't1', 'personal', 'Hello', '2024-01-01', 3, 5)"
    )
    cur.execute(
        "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id, client_id) "
        "VALUES (11, 'msg2', 't2', 'work', 'Meeting', '2024-01-02', NULL, NULL)"
    )

    # Chats
    cur.execute(
        "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
        "VALUES (20, 'chat1', 'claude', 'Debug session', 3, 5)"
    )

    # Folders
    cur.execute(
        "INSERT INTO folders (id, path, relative_path, name, project_id) "
        "VALUES (30, '/Users/me/Work/widget', 'Work/widget', 'widget', 3)"
    )
    cur.execute(
        "INSERT INTO folders (id, path, relative_path, name, project_id) "
        "VALUES (31, '/Users/me/Personal', 'Personal', 'Personal', NULL)"
    )

    conn.commit()


class TestRecalculateGlobal:
    def test_stamps_all_tables(self, conn):
        """recalculate_access(conn, 'global') stamps all entity tables.

        Global-only resolution writes 'inherit' — the MCP layer resolves
        to the global policy at query time.
        """
        _seed_entities(conn)
        # Set global=hidden policy
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        stats = recalculate_access(conn, "global")

        # All rows should have mcp_view='inherit' (resolved at query time)
        for table in ["files", "emails", "chats", "folders", "projects", "clients"]:
            rows = conn.execute(f"SELECT mcp_view FROM {table}").fetchall()
            for row in rows:
                assert row["mcp_view"] == "inherit", f"{table} row not stamped inherit"

        assert stats  # non-empty dict


class TestRecalculateSourceScope:
    def test_only_stamps_target_source(self, conn):
        """source:files scope only stamps files, not emails."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "source:files")

        # Files should be stamped
        rows = conn.execute("SELECT mcp_view FROM files").fetchall()
        assert all(r["mcp_view"] == "hidden" for r in rows)

        # Emails should be unchanged (inherit)
        rows = conn.execute("SELECT mcp_view FROM emails").fetchall()
        assert all(r["mcp_view"] == "inherit" for r in rows)


class TestRecalculateAccountScope:
    def test_only_stamps_matching_account(self, conn):
        """account:personal only stamps emails/chats with that account."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('account:personal', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "account:personal")

        # Personal email stamped
        row = conn.execute("SELECT mcp_view FROM emails WHERE id = 10").fetchone()
        assert row["mcp_view"] == "hidden"

        # Work email unchanged
        row = conn.execute("SELECT mcp_view FROM emails WHERE id = 11").fetchone()
        assert row["mcp_view"] == "inherit"


class TestRecalculateFolderPrefix:
    def test_matches_files_by_path_prefix(self, conn):
        """folder: scope matches files/folders by path prefix."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('folder:/Users/me/Work/', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "folder:/Users/me/Work/")

        # File under /Work/ stamped
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"

        # File under /Personal/ unchanged
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 2").fetchone()
        assert row["mcp_view"] == "inherit"

        # Folder under /Work/ stamped
        row = conn.execute("SELECT mcp_view FROM folders WHERE id = 30").fetchone()
        assert row["mcp_view"] == "hidden"


class TestRecalculateFolderTildeExpansion:
    def test_tilde_scope_expands_and_matches(self, conn, monkeypatch):
        """folder:~/Work/ expands ~ via os.path.expanduser before matching."""
        monkeypatch.setattr(
            "footprinter.access.os.path.expanduser",
            lambda p: p.replace("~", "/Users/me"),
        )
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('folder:~/Work/', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "folder:~/Work/")

        # File under /Work/ stamped
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"

        # File under /Personal/ unchanged
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 2").fetchone()
        assert row["mcp_view"] == "inherit"

        # Folder under /Work/ stamped
        row = conn.execute("SELECT mcp_view FROM folders WHERE id = 30").fetchone()
        assert row["mcp_view"] == "hidden"

        # Folder under /Personal/ unchanged
        row = conn.execute("SELECT mcp_view FROM folders WHERE id = 31").fetchone()
        assert row["mcp_view"] == "inherit"


class TestRecalculateFolderLikeEscaping:
    def test_underscore_in_path_is_literal(self, conn):
        """folder: scope treats _ as literal, not LIKE wildcard."""
        conn.execute("INSERT INTO files (id, source, name, path) VALUES (100, 'local', 'x.py', '/Users/me/W_rk/x.py')")
        conn.execute("INSERT INTO files (id, source, name, path) VALUES (101, 'local', 'y.py', '/Users/me/Work/y.py')")
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('folder:/Users/me/W_rk/', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "folder:/Users/me/W_rk/")

        # Only the literal match should be stamped
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 100").fetchone()
        assert row["mcp_view"] == "hidden"

        # /Work/ should NOT match _ wildcard
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 101").fetchone()
        assert row["mcp_view"] == "inherit"

    def test_percent_in_path_is_literal(self, conn):
        """folder: scope treats % as literal, not LIKE wildcard."""
        conn.execute(
            "INSERT INTO files (id, source, name, path) VALUES (100, 'local', 'x.py', '/Users/me/50%done/x.py')"
        )
        conn.execute("INSERT INTO files (id, source, name, path) VALUES (101, 'local', 'y.py', '/Users/me/other/y.py')")
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('folder:/Users/me/50%done/', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "folder:/Users/me/50%done/")

        row = conn.execute("SELECT mcp_view FROM files WHERE id = 100").fetchone()
        assert row["mcp_view"] == "hidden"

        # Should NOT match via % wildcard
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 101").fetchone()
        assert row["mcp_view"] == "inherit"


class TestRecalculateProjectCascades:
    def test_stamps_project_and_children(self, conn):
        """project:3 stamps project + files/emails/chats/folders with project_id=3."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('project:3', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "project:3")

        # Project itself
        row = conn.execute("SELECT mcp_view FROM projects WHERE id = 3").fetchone()
        assert row["mcp_view"] == "hidden"

        # Child file (project_id=3)
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"

        # Non-child file (project_id IS NULL)
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 2").fetchone()
        assert row["mcp_view"] == "inherit"

        # Child email
        row = conn.execute("SELECT mcp_view FROM emails WHERE id = 10").fetchone()
        assert row["mcp_view"] == "hidden"

        # Child chat
        row = conn.execute("SELECT mcp_view FROM chats WHERE id = 20").fetchone()
        assert row["mcp_view"] == "hidden"

        # Child folder
        row = conn.execute("SELECT mcp_view FROM folders WHERE id = 30").fetchone()
        assert row["mcp_view"] == "hidden"


class TestRecalculateClientCascades:
    def test_cascades_through_projects_to_children(self, conn):
        """client:5 stamps client + projects + all children."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:5', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "client:5")

        # Client
        row = conn.execute("SELECT mcp_view FROM clients WHERE id = 5").fetchone()
        assert row["mcp_view"] == "hidden"

        # Project under client
        row = conn.execute("SELECT mcp_view FROM projects WHERE id = 3").fetchone()
        assert row["mcp_view"] == "hidden"

        # Children of that project
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"

        # File NOT under client's project
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 2").fetchone()
        assert row["mcp_view"] == "inherit"


class TestRecalculateClientDirectAssignment:
    """_get_ids_for_scope('client:X') must enumerate files/folders/emails/chats
    tagged directly via their own client_id, not just those reached through
    the project cascade.

    Scope is limited to enumeration (change-triggered recalc membership).
    Policy resolution for direct-client entities is a separate concern —
    see unified-dataset-discrepancy-ledger.md D01–D04.
    """

    def test_file_direct_client_id_no_project_included(self, conn):
        """(a) file with client_id=X, project_id=NULL is enumerated by client:X."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO files (id, source, name, path, account, project_id, client_id) "
            "VALUES (50, 'local', 'direct.py', '/Users/me/direct.py', 'personal', NULL, 5)"
        )
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert 50 in ids_by_type.get("file", [])

    def test_folder_direct_client_id_no_project_included(self, conn):
        """(b) folder with client_id=X, project_id=NULL is enumerated by client:X."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO folders (id, path, relative_path, name, project_id, client_id) "
            "VALUES (40, '/Users/me/direct-folder', 'direct-folder', 'direct-folder', NULL, 5)"
        )
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert 40 in ids_by_type.get("folder", [])

    def test_file_direct_client_id_overrides_project_client(self, conn):
        """(c) file in a project whose client_id!=X is still enumerated via its direct client_id."""
        _seed_entities(conn)
        conn.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (6, 'Other', 'other', 'external')")
        conn.execute(
            "INSERT INTO projects (id, project_name, root_path, client_id) "
            "VALUES (4, 'OtherProj', '/Users/me/Work/other', 6)"
        )
        conn.execute(
            "INSERT INTO files (id, source, name, path, account, project_id, client_id) "
            "VALUES (51, 'local', 'cross.py', '/Users/me/Work/other/cross.py', 'work', 4, 5)"
        )
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert 51 in ids_by_type.get("file", [])
        # File 51's project (4) belongs to client 6 — must not leak into client:5 enumeration
        assert 4 not in ids_by_type.get("project", [])

    def test_dedup_file_reachable_via_both_paths(self, conn):
        """(d) file reached via both project cascade and direct client_id appears once."""
        _seed_entities(conn)
        # Project 3 already has client_id=5; tag file 1 directly to client 5 too
        conn.execute("UPDATE files SET client_id = 5 WHERE id = 1")
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert ids_by_type["file"].count(1) == 1

    def test_email_direct_client_id_no_project_included(self, conn):
        """(e) email with client_id=X, project_id=NULL is enumerated by client:X."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id, client_id) "
            "VALUES (60, 'msg-direct', 't-direct', 'work', 'Direct', '2024-02-01', NULL, 5)"
        )
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert 60 in ids_by_type.get("email", [])

    def test_chat_direct_client_id_no_project_included(self, conn):
        """(f) chat with client_id=X, project_id=NULL is enumerated by client:X."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
            "VALUES (70, 'chat-direct', 'claude', 'Direct chat', NULL, 5)"
        )
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert 70 in ids_by_type.get("chat", [])

    def test_email_direct_client_id_overrides_project_client(self, conn):
        """(g) email in a project whose client_id!=X is still enumerated via its direct client_id."""
        _seed_entities(conn)
        conn.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (7, 'Cross', 'cross', 'external')")
        conn.execute(
            "INSERT INTO projects (id, project_name, root_path, client_id) "
            "VALUES (8, 'CrossProj', '/Users/me/Work/cross', 7)"
        )
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, project_id, client_id) "
            "VALUES (61, 'msg-cross', 't-cross', 'work', 'Cross', '2024-02-02', 8, 5)"
        )
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert 61 in ids_by_type.get("email", [])
        assert 8 not in ids_by_type.get("project", [])

    def test_dedup_chat_reachable_via_both_paths(self, conn):
        """(h) chat reached via both project cascade and direct client_id appears once."""
        _seed_entities(conn)
        # Chat 20 is already under project 3 (client 5); tag it directly to client 5 too
        conn.execute("UPDATE chats SET client_id = 5 WHERE id = 20")
        conn.commit()

        from footprinter.access import _get_ids_for_scope

        ids_by_type = _get_ids_for_scope(conn, "client:5")

        assert ids_by_type["chat"].count(20) == 1


class TestRecalculateClientStampsDirectClientEntities:
    """End-to-end check that recalculate_access('client:N') stamps direct-client
    entities with the policy state, not 'inherit'. Companion to
    TestRecalculateClientDirectAssignment which only verifies enumeration.
    """

    def test_stamps_direct_client_file_visibility(self, conn):
        """File with client_id=5, project_id=NULL gets mcp_view='hidden' under client:5."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO files (id, source, name, path, account, project_id, client_id) "
            "VALUES (50, 'local', 'direct.py', '/Users/me/direct.py', 'personal', NULL, 5)"
        )
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:5', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "client:5")

        row = conn.execute("SELECT mcp_view FROM files WHERE id = 50").fetchone()
        assert row["mcp_view"] == "hidden", (
            f"Direct-client file should be stamped 'hidden', got {row['mcp_view']!r}"
        )

    def test_stamps_direct_client_file_permission(self, conn):
        """File with client_id=5, project_id=NULL gets mcp_read='deny' under client:5."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO files (id, source, name, path, account, project_id, client_id) "
            "VALUES (50, 'local', 'direct.py', '/Users/me/direct.py', 'personal', NULL, 5)"
        )
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('client:5', 'deny')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "client:5")

        row = conn.execute("SELECT mcp_read FROM files WHERE id = 50").fetchone()
        assert row["mcp_read"] == "deny", (
            f"Direct-client file should be stamped 'deny', got {row['mcp_read']!r}"
        )

    def test_stamps_direct_client_email_visibility(self, conn):
        """Email with client_id=5, project_id=NULL gets mcp_view='hidden' under client:5."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO emails (id, message_id, thread_id, account, subject, received_at, "
            "project_id, client_id) "
            "VALUES (60, 'msg-direct', 't-direct', 'work', 'Direct', '2024-02-01', NULL, 5)"
        )
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:5', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "client:5")

        row = conn.execute("SELECT mcp_view FROM emails WHERE id = 60").fetchone()
        assert row["mcp_view"] == "hidden"

    def test_stamps_direct_client_chat_visibility(self, conn):
        """Chat with client_id=5, project_id=NULL gets mcp_view='hidden' under client:5."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO chats (id, external_id, account, title, project_id, client_id) "
            "VALUES (70, 'chat-direct', 'claude', 'Direct chat', NULL, 5)"
        )
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:5', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "client:5")

        row = conn.execute("SELECT mcp_view FROM chats WHERE id = 70").fetchone()
        assert row["mcp_view"] == "hidden"

    def test_stamps_direct_client_folder_visibility(self, conn):
        """Folder with client_id=5, project_id=NULL gets mcp_view='hidden' under client:5."""
        _seed_entities(conn)
        conn.execute(
            "INSERT INTO folders (id, path, relative_path, name, project_id, client_id) "
            "VALUES (40, '/Users/me/direct-folder', 'direct-folder', 'direct-folder', NULL, 5)"
        )
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('client:5', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        recalculate_access(conn, "client:5")

        row = conn.execute("SELECT mcp_view FROM folders WHERE id = 40").fetchone()
        assert row["mcp_view"] == "hidden"


class TestRecalculateSingleEntity:
    def test_stamps_single_file(self, conn):
        """recalculate_entity(conn, 'file', 1) stamps only that file."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_entity

        result = recalculate_entity(conn, "file", 1)

        # File 1 stamped — global source writes 'inherit'
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "inherit"

        # File 2 unchanged (only entity 1 was recalculated)
        row = conn.execute("SELECT mcp_view FROM files WHERE id = 2").fetchone()
        assert row["mcp_view"] == "inherit"

        assert result == {"file": 1}

    def test_nonexistent_entity_returns_zero(self, conn):
        """recalculate_entity for a missing ID returns count 0."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_entity

        result = recalculate_entity(conn, "file", 9999)
        assert result == {"file": 0}


class TestStatsDict:
    def test_returns_per_table_counts(self, conn):
        """Stats dict has counts per entity type."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access

        stats = recalculate_access(conn, "global")

        assert "file" in stats
        assert "email" in stats
        assert "chat" in stats
        assert "folder" in stats
        assert "project" in stats
        assert "client" in stats
        assert stats["file"] == 2
        assert stats["email"] == 2


class TestCountAffectedEntities:
    def test_count_global_returns_all_tables(self, conn):
        """count_affected_entities('global') returns counts for all entity types."""
        _seed_entities(conn)

        from footprinter.access import count_affected_entities

        counts = count_affected_entities(conn, "global")

        assert counts["file"] == 2
        assert counts["email"] == 2
        assert counts["chat"] == 1
        assert counts["folder"] == 2
        assert counts["project"] == 1
        assert counts["client"] == 1
        assert sum(counts.values()) == 9

    def test_count_single_entity_returns_one(self, conn):
        """count_affected_entities('file:1') returns {'file': 1}."""
        _seed_entities(conn)

        from footprinter.access import count_affected_entities

        counts = count_affected_entities(conn, "file:1")

        assert counts == {"file": 1}
        assert sum(counts.values()) == 1

    def test_count_source_scope(self, conn):
        """count_affected_entities('source:files') returns file count only."""
        _seed_entities(conn)

        from footprinter.access import count_affected_entities

        counts = count_affected_entities(conn, "source:files")

        assert "file" in counts
        assert counts["file"] == 2
        assert "email" not in counts
        assert "chat" not in counts


class TestRecalculateBatched:
    """Tests for recalculate_access_batched() — chunked processing with callbacks."""

    def test_produces_same_results_as_unbatched(self, conn):
        """Batched recalculation produces identical column values to unbatched."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'deny')")
        conn.commit()

        from footprinter.access import recalculate_access, recalculate_access_batched

        # Run batched with tiny batch_size to force multiple chunks
        stats = recalculate_access_batched(conn, "global", batch_size=2)

        # Read back all column values
        batched_vis = {r["id"]: r["mcp_view"] for r in conn.execute("SELECT id, mcp_view FROM files").fetchall()}
        batched_perm = {r["id"]: r["mcp_read"] for r in conn.execute("SELECT id, mcp_read FROM files").fetchall()}

        # Reset columns to default, run unbatched
        conn.execute("UPDATE files SET mcp_view = 'inherit', mcp_read = 'inherit'")
        conn.execute("UPDATE emails SET mcp_view = 'inherit', mcp_read = 'inherit'")
        conn.execute("UPDATE chats SET mcp_view = 'inherit', mcp_read = 'inherit'")
        conn.execute("UPDATE folders SET mcp_view = 'inherit'")
        conn.execute("UPDATE projects SET mcp_view = 'inherit', mcp_read = 'inherit'")
        conn.execute("UPDATE clients SET mcp_view = 'inherit', mcp_read = 'inherit'")
        conn.commit()

        recalculate_access(conn, "global")

        unbatched_vis = {r["id"]: r["mcp_view"] for r in conn.execute("SELECT id, mcp_view FROM files").fetchall()}
        unbatched_perm = {r["id"]: r["mcp_read"] for r in conn.execute("SELECT id, mcp_read FROM files").fetchall()}

        assert batched_vis == unbatched_vis
        assert batched_perm == unbatched_perm
        assert stats  # non-empty

    def test_calls_on_batch_callback(self, conn):
        """on_batch callback is called with correct counts for each batch."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access_batched

        batch_counts = []
        recalculate_access_batched(conn, "global", batch_size=1, on_batch=lambda n: batch_counts.append(n))

        # Each callback should report exactly 1 entity (batch_size=1)
        assert all(c == 1 for c in batch_counts)
        # Total processed should equal total entities
        assert sum(batch_counts) == 9  # 2 files + 2 emails + 1 chat + 2 folders + 1 project + 1 client

    def test_commits_per_batch(self, conn):
        """on_batch is called multiple times, proving per-batch processing."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access_batched

        # With batch_size=1 and 9 entities, on_batch should be called 9 times
        # (once per entity = once per batch commit).
        batch_calls = []
        recalculate_access_batched(conn, "global", batch_size=1, on_batch=lambda n: batch_calls.append(n))

        # 9 entities across 6 types, batch_size=1 → 9 batch commits
        assert len(batch_calls) == 9

    def test_returns_same_stats_shape(self, conn):
        """Stats dict from batched has same keys and counts as unbatched."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import recalculate_access_batched

        stats = recalculate_access_batched(conn, "global", batch_size=2)

        assert stats["file"] == 2
        assert stats["email"] == 2
        assert stats["chat"] == 1
        assert stats["folder"] == 2
        assert stats["project"] == 1
        assert stats["client"] == 1


class TestRoundTripMatchesBatchResolve:
    def test_column_values_match_write_back_logic(self, conn):
        """After recalculate_access, column values reflect write-back logic.

        Global/baseline sources write 'inherit'; specific sources write
        the resolved value.
        """
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'hidden')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'deny')")
        conn.commit()

        from footprinter.access import recalculate_access
        from footprinter.permissions import batch_resolve_permissions
        from footprinter.visibility import batch_resolve_visibility

        recalculate_access(conn, "source:files")

        from footprinter.access import _is_inherit_source

        # Check files visibility — specific source writes resolved value
        file_ids = [r["id"] for r in conn.execute("SELECT id FROM files").fetchall()]
        vis_results = batch_resolve_visibility(conn, "file", file_ids)
        for fid in file_ids:
            row = conn.execute("SELECT mcp_view FROM files WHERE id = ?", (fid,)).fetchone()
            expected_state, source = vis_results[fid]
            if _is_inherit_source(source):
                assert row["mcp_view"] == "inherit"
            else:
                assert row["mcp_view"] == expected_state

        # Check files permissions — specific source writes resolved value
        perm_results = batch_resolve_permissions(conn, "file", file_ids)
        for fid in file_ids:
            row = conn.execute("SELECT mcp_read FROM files WHERE id = ?", (fid,)).fetchone()
            expected_bool, source = perm_results[fid]
            if _is_inherit_source(source):
                assert row["mcp_read"] == "inherit"
            else:
                expected_val = "allow" if expected_bool else "deny"
                assert row["mcp_read"] == expected_val


class TestStampEntities:
    """Tests for stamp_entities() — public API for stamping a subset of entities."""

    def test_stamps_subset_of_entities(self, conn):
        """stamp_entities stamps only the specified IDs, leaves others at default."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('global', 'deny')")
        conn.commit()

        from footprinter.access import stamp_entities

        stats = stamp_entities(conn, {"file": [1]})

        # File 1 stamped — global source writes 'inherit'
        row = conn.execute("SELECT mcp_view, mcp_read FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "inherit"
        assert row["mcp_read"] == "inherit"

        # File 2 unchanged (not in the ids list)
        row = conn.execute("SELECT mcp_view, mcp_read FROM files WHERE id = 2").fetchone()
        assert row["mcp_view"] == "inherit"
        assert row["mcp_read"] == "inherit"

        assert stats == {"file": 1}

    def test_stamps_multiple_entity_types(self, conn):
        """stamp_entities handles multiple entity types in one call."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        from footprinter.access import stamp_entities

        stats = stamp_entities(conn, {"file": [1, 2], "email": [10]})

        for fid in [1, 2]:
            row = conn.execute("SELECT mcp_view FROM files WHERE id = ?", (fid,)).fetchone()
            assert row["mcp_view"] == "inherit"

        row = conn.execute("SELECT mcp_view FROM emails WHERE id = 10").fetchone()
        assert row["mcp_view"] == "inherit"

        # Email 11 not stamped
        row = conn.execute("SELECT mcp_view FROM emails WHERE id = 11").fetchone()
        assert row["mcp_view"] == "inherit"

        assert stats == {"file": 2, "email": 1}

    def test_empty_ids_is_noop(self, conn):
        """stamp_entities with empty ID lists returns empty dict, no crash."""
        _seed_entities(conn)

        from footprinter.access import stamp_entities

        stats = stamp_entities(conn, {"file": []})
        assert stats == {}

    def test_commits_results(self, conn):
        """stamp_entities commits — values persist after rollback attempt."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'hidden')")
        conn.commit()

        from footprinter.access import stamp_entities

        stamp_entities(conn, {"file": [1]})

        # Rollback should be a no-op since stamp_entities already committed
        conn.rollback()

        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "hidden"

    def test_exception_mid_loop_does_not_commit_partial_writes(self, conn, monkeypatch):
        """If resolve raises for entity type B, partial writes are uncommitted and rollback-able."""
        _seed_entities(conn)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('global', 'hidden')")
        conn.commit()

        import footprinter.access as access_mod

        original_batch_vis = access_mod.batch_resolve_visibility

        def raise_on_email(conn, entity_type, ids):
            if entity_type == "email":
                raise RuntimeError("email vis crash")
            return original_batch_vis(conn, entity_type, ids)

        monkeypatch.setattr(
            "footprinter.access.batch_resolve_visibility",
            raise_on_email,
        )

        from footprinter.access import stamp_entities

        with pytest.raises(RuntimeError, match="email vis crash"):
            stamp_entities(conn, {"file": [1, 2], "email": [10]})

        # Partial writes are uncommitted — rollback reverts them
        conn.rollback()

        row = conn.execute("SELECT mcp_view FROM files WHERE id = 1").fetchone()
        assert row["mcp_view"] == "inherit"


class TestSqliteVariableLimit:
    """Tests for chunked queries when ID lists exceed SQLite's 999-variable limit.

    Uses setlimit() to enforce a 999-variable cap (matching older SQLite builds
    where the production crash was observed).
    """

    @pytest.fixture(autouse=True)
    def _lower_var_limit(self, conn):
        """Set SQLite variable limit to 999 for the duration of each test."""
        old = conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        yield
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, old)

    def _seed_many_files(self, conn, count):
        """Insert *count* file rows with sequential IDs."""
        cur = conn.cursor()
        for i in range(1, count + 1):
            cur.execute(
                "INSERT INTO files (id, source, name, path, account) VALUES (?, 'local', ?, ?, 'work')",
                (i, f"f{i}.py", f"/Users/me/Work/f{i}.py"),
            )
        conn.commit()

    def test_stamp_entities_large_id_list(self, conn):
        """stamp_entities handles >999 IDs without OperationalError."""
        count = 1200
        self._seed_many_files(conn, count)
        conn.execute("INSERT INTO visibility_policies (scope, setting) VALUES ('source:files', 'hidden')")
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('source:files', 'deny')")
        conn.commit()

        from footprinter.access import stamp_entities

        ids = list(range(1, count + 1))
        stats = stamp_entities(conn, {"file": ids})

        assert stats["file"] == count
        rows = conn.execute("SELECT COUNT(*) FROM files WHERE mcp_view = 'hidden'").fetchone()[0]
        assert rows == count

    def test_batch_resolve_visibility_chunks_correctly(self, conn):
        """batch_resolve_visibility returns results for >999 IDs."""
        count = 1200
        self._seed_many_files(conn, count)

        from footprinter.visibility import batch_resolve_visibility

        ids = list(range(1, count + 1))
        results = batch_resolve_visibility(conn, "file", ids)

        assert len(results) == count
        for i in range(1, count + 1):
            assert i in results

    def test_batch_resolve_permissions_chunks_correctly(self, conn):
        """batch_resolve_permissions returns results for >999 IDs."""
        count = 1200
        self._seed_many_files(conn, count)

        from footprinter.permissions import batch_resolve_permissions

        ids = list(range(1, count + 1))
        results = batch_resolve_permissions(conn, "file", ids)

        assert len(results) == count
        for i in range(1, count + 1):
            assert i in results
