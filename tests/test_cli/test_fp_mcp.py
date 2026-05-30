"""Tests for fp mcp subcommands.

Covers:
  - Parser tree: help exits 0 for all subcommands
  - Server start: bare ``fp mcp`` calls server.main()
  - Unified check: no-args (show all), path, folder, project, client, --json
  - Set: unified policy setter (--visibility / --permission / --dry-run)
  - Reset: unified policy delete / reseed
"""

import json
import sqlite3
from unittest.mock import patch

import pytest
from conftest import run_fp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def policy_db(tmp_path, monkeypatch):
    """Create an isolated DB with tool-scope schema, point FOOTPRINTER_DB_PATH at it."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# Parser tree tests
# ---------------------------------------------------------------------------


class TestMcpParserTree:
    """All fp mcp subcommands respond to --help with exit 0."""

    @pytest.mark.parametrize(
        "args",
        [
            ("mcp", "--help"),
            ("mcp", "check", "--help"),
            ("mcp", "set", "--help"),
            ("mcp", "reset", "--help"),
        ],
    )
    def test_help_exits_zero(self, args):
        stdout, stderr, code = run_fp(*args)
        assert code == 0


# ---------------------------------------------------------------------------
# Server start
# ---------------------------------------------------------------------------


class TestMcpServerStart:
    """Bare ``fp mcp`` starts the MCP server."""

    def test_bare_mcp_calls_server_main(self):
        with patch("footprinter.cli.mcp_cmd._start_server") as mock_start:
            mock_start.return_value = None
            stdout, stderr, code = run_fp("mcp")
            mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# Check: show all policies (no args)
# ---------------------------------------------------------------------------


class TestMcpCheckShowAll:
    """fp mcp check (no args) shows all policies from both tables."""

    def test_check_no_args_shows_policies(self, policy_db):
        """No-args check shows a unified policy table."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        stdout, stderr, code = run_fp("mcp", "check")
        assert code == 0
        assert "full" in stdout
        assert "allow" in stdout

    def test_check_no_args_empty_db(self, policy_db):
        """No-args check with empty policy tables shows 'no policies' message."""
        stdout, stderr, code = run_fp("mcp", "check")
        assert code == 0
        assert "no policies" in stdout.lower()

    def test_check_no_args_json(self, policy_db):
        """No-args check --json returns both policy lists."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "deny")
        stdout, stderr, code = run_fp("mcp", "check", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert "visibility" in data
        assert "permission" in data
        assert any(r["setting"] == "full" for r in data["visibility"])
        assert any(r["setting"] == "deny" for r in data["permission"])

    def test_check_no_args_shows_multiple_scopes(self, policy_db):
        """No-args check shows entity-level overrides alongside global."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        run_fp("mcp", "set", "folder:~/Work", "--visibility", "hidden")
        stdout, stderr, code = run_fp("mcp", "check")
        assert code == 0
        assert "global" in stdout
        assert "folder:~/Work" in stdout


# ---------------------------------------------------------------------------
# Check: combined resolution (with target)
# ---------------------------------------------------------------------------


class TestMcpCheck:
    """Test fp mcp check (combined resolution with target)."""

    def test_check_path_no_db(self, tmp_path, monkeypatch):
        """check with path but no DB shows baseline defaults."""
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(tmp_path / "nonexistent.db"))
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test.txt")
        assert code == 0
        assert "baseline" in stdout

    def test_check_path_simulated(self, policy_db):
        """check with unindexed path shows simulated resolution."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "deny")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test.txt")
        assert code == 0
        assert "deny" in stdout
        assert "full" in stdout

    def test_check_json_output(self, policy_db):
        """check with --json produces valid JSON."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "deny")
        stdout, stderr, code = run_fp("mcp", "check", "--json", "/tmp/test.txt")
        assert code == 0
        data = json.loads(stdout)
        assert "permission" in data
        assert "visibility" in data
        assert data["permission"]["resolved"] == "deny"
        assert data["visibility"]["resolved"] == "full"

    def test_check_folder_empty(self, policy_db):
        """check --folder with no files."""
        stdout, stderr, code = run_fp("mcp", "check", "--folder", "/tmp/empty")
        assert code == 0
        assert "0 files" in stdout

    def test_check_path_includes_client_scope(self, policy_db):
        """check --json shows client scope in chain when file's project has a client."""
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO clients (id, name, slug, client_type, status) "
            "VALUES (99, 'Test Client', 'test-client', 'external', 'listed')"
        )
        conn.execute(
            "INSERT INTO projects (id, name, client_id, status) VALUES (99, 'Client Project', 99, 'listed')"
        )
        conn.execute(
            "INSERT INTO files (id, name, path, source, status, project_id, size_bytes) "
            "VALUES (99, 'file.txt', '/tmp/client-test/file.txt', 'local', 'listed', 99, 100)"
        )
        conn.execute("INSERT INTO permission_policies (scope, setting) VALUES ('client:99', 'deny')")
        conn.commit()
        conn.close()

        stdout, stderr, code = run_fp("mcp", "check", "--json", "/tmp/client-test/file.txt")
        assert code == 0
        data = json.loads(stdout)
        chain_scopes = [entry["scope"] for entry in data["chain"]]
        assert "client:99" in chain_scopes, f"Expected 'client:99' in chain scopes, got: {chain_scopes}"

    def test_check_folder_json(self, policy_db):
        """check --folder with --json and no files."""
        stdout, stderr, code = run_fp("mcp", "check", "--folder", "/tmp/empty", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert data["file_count"] == 0

    def test_check_path_finds_folder(self, policy_db):
        """Positional path finds a folder in the folders table."""
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test-folder")
        assert code == 0
        assert "simulated" not in stdout.lower()
        assert "Folder Check" in stdout

    def test_check_path_finds_folder_json(self, policy_db):
        """Positional path that resolves to a folder returns folder-typed JSON."""
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        stdout, stderr, code = run_fp("mcp", "check", "--json", "/tmp/test-folder")
        assert code == 0
        data = json.loads(stdout)
        assert data["type"] == "folder"
        assert "folder" in data
        assert "file_count" in data

    def test_check_path_not_found_messaging(self, policy_db):
        """Unindexed path shows clear not-found message with tips."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/definitely-not-indexed")
        assert code == 0
        assert "Not found in files or folders" in stdout
        assert "--folder" in stdout

    def test_check_path_not_found_json(self, policy_db):
        """Unindexed path in JSON mode shows found_in_db: false."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        stdout, stderr, code = run_fp("mcp", "check", "--json", "/tmp/definitely-not-indexed")
        assert code == 0
        data = json.loads(stdout)
        assert data["found_in_db"] is False


# ---------------------------------------------------------------------------
# Set: unified policy setter
# ---------------------------------------------------------------------------


class TestMcpSet:
    """Test fp mcp set <scope> --visibility <val> --permission <val>."""

    def test_set_both_values(self, policy_db):
        """Set both visibility and permission in one call."""
        stdout, stderr, code = run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        assert code == 0
        assert "full" in stdout
        assert "allow" in stdout

    def test_set_visibility_only(self, policy_db):
        """Set just visibility."""
        stdout, stderr, code = run_fp("mcp", "set", "global", "--visibility", "hidden")
        assert code == 0
        assert "hidden" in stdout

    def test_set_permission_only(self, policy_db):
        """Set just permission."""
        stdout, stderr, code = run_fp("mcp", "set", "global", "--permission", "deny")
        assert code == 0
        assert "deny" in stdout

    def test_set_no_value_fails(self, policy_db):
        """Must specify at least one of --visibility or --permission."""
        stdout, stderr, code = run_fp("mcp", "set", "global")
        assert code != 0
        combined = stdout + stderr
        assert "at least one" in combined.lower() or "--visibility" in combined

    def test_set_invalid_visibility_fails(self, policy_db):
        """Invalid visibility value rejected."""
        stdout, stderr, code = run_fp("mcp", "set", "global", "--visibility", "allow")
        assert code != 0
        assert "Invalid visibility" in stdout + stderr

    def test_set_invalid_permission_fails(self, policy_db):
        """Invalid permission value rejected."""
        stdout, stderr, code = run_fp("mcp", "set", "global", "--permission", "hidden")
        assert code != 0
        assert "Invalid permission" in stdout + stderr

    def test_set_then_check(self, policy_db):
        """Set both, then verify with check."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "deny")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test.txt")
        assert code == 0
        assert "full" in stdout
        assert "deny" in stdout

    def test_set_folder_scope(self, policy_db):
        """Set with folder scope."""
        stdout, stderr, code = run_fp("mcp", "set", "folder:~/Work", "--visibility", "hidden")
        assert code == 0
        assert "folder:~/Work" in stdout

    def test_set_writes_to_both_tables(self, policy_db):
        """Verify both policy tables are written."""
        run_fp("mcp", "set", "global", "--visibility", "opaque", "--permission", "deny")
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        vis = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        perm = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()
        assert vis["setting"] == "opaque"
        assert perm["setting"] == "deny"

    def test_set_overwrites_existing(self, policy_db):
        """Setting a scope twice overwrites the previous value."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        run_fp("mcp", "set", "global", "--visibility", "hidden", "--permission", "deny")
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        vis = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        perm = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()
        assert vis["setting"] == "hidden"
        assert perm["setting"] == "deny"

    def test_set_dry_run(self, policy_db):
        """--dry-run previews without writing policies."""
        stdout, stderr, code = run_fp(
            "mcp", "set", "folder:~/Work", "--visibility", "hidden", "--dry-run",
        )
        assert code == 0
        assert "Dry run" in stdout

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT setting FROM visibility_policies WHERE scope = 'folder:~/Work'"
        ).fetchone()
        conn.close()
        assert row is None, "dry-run should not write policy rows"

    def test_set_dry_run_no_recalculate(self, policy_db):
        """--dry-run should not trigger recalculation."""
        stdout, stderr, code = run_fp(
            "mcp", "set", "global", "--permission", "allow", "--dry-run",
        )
        assert code == 0
        assert "Dry run" in stdout
        assert "Recalculated" not in stdout


# ---------------------------------------------------------------------------
# Reset: unified policy delete / reseed
# ---------------------------------------------------------------------------


class TestMcpReset:
    """Test fp mcp reset <scope> and fp mcp reset --all."""

    def test_reset_scope_deletes_from_both_tables(self, policy_db):
        """Reset a scope removes it from both visibility and permission tables."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "deny")
        stdout, stderr, code = run_fp("mcp", "reset", "global")
        assert code == 0
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        vis = conn.execute("SELECT 1 FROM visibility_policies WHERE scope = 'global'").fetchone()
        perm = conn.execute("SELECT 1 FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()
        assert vis is None
        assert perm is None

    def test_reset_scope_nonexistent(self, policy_db):
        """Reset a scope that has no policies prints informational message, exits 0."""
        stdout, stderr, code = run_fp("mcp", "reset", "folder:~/Nonexistent")
        assert code == 0
        assert "no polic" in stdout.lower() or "not found" in stdout.lower()

    def test_reset_all_with_scope_fails(self, policy_db):
        """reset --all with a positional scope is rejected to prevent accidental nuke."""
        stdout, stderr, code = run_fp("mcp", "reset", "--all", "folder:~/Work")
        assert code != 0
        assert "Cannot combine" in stdout + stderr

    def test_reset_all_clears_and_reseeds(self, policy_db):
        """reset --all clears all policies and re-seeds defaults."""
        run_fp("mcp", "set", "folder:~/Work", "--visibility", "hidden")
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        stdout, stderr, code = run_fp("mcp", "reset", "--all")
        assert code == 0
        assert "Cleared" in stdout
        assert "Re-seeded" in stdout or "re-seeded" in stdout.lower()
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        vis = conn.execute("SELECT * FROM visibility_policies").fetchall()
        perm = conn.execute("SELECT * FROM permission_policies").fetchall()
        conn.close()
        assert len(vis) == 1 and vis[0]["scope"] == "global" and vis[0]["setting"] == "full"
        assert len(perm) == 1 and perm[0]["scope"] == "global" and perm[0]["setting"] == "allow"

    def test_reset_no_args_shows_help(self):
        """reset with no args shows usage."""
        stdout, stderr, code = run_fp("mcp", "reset")
        combined = stdout + stderr
        assert code != 0 or "usage" in combined.lower() or "scope" in combined.lower()

    def test_reset_only_touches_target_scope(self, policy_db):
        """Reset one scope leaves other scopes intact."""
        run_fp("mcp", "set", "global", "--visibility", "full", "--permission", "allow")
        run_fp("mcp", "set", "folder:~/Work", "--visibility", "hidden", "--permission", "deny")
        run_fp("mcp", "reset", "folder:~/Work")
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        vis_global = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        perm_global = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        vis_folder = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'folder:~/Work'").fetchone()
        perm_folder = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'folder:~/Work'").fetchone()
        conn.close()
        assert vis_global["setting"] == "full"
        assert perm_global["setting"] == "allow"
        assert vis_folder is None
        assert perm_folder is None

    def test_reset_partial_scope(self, policy_db):
        """Reset a scope that only has a visibility policy (no permission) still works."""
        run_fp("mcp", "set", "folder:~/Work", "--visibility", "hidden")
        stdout, stderr, code = run_fp("mcp", "reset", "folder:~/Work")
        assert code == 0
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        vis = conn.execute("SELECT 1 FROM visibility_policies WHERE scope = 'folder:~/Work'").fetchone()
        conn.close()
        assert vis is None
