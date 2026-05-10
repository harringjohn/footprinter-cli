"""Tests for fp mcp subcommands.

Covers:
  - Parser tree: help exits 0 for all subcommands
  - Server start: bare ``fp mcp`` calls server.main()
  - View policies: show, set, delete, reset, check (visibility layer)
  - Read policies: show, set, delete, reset, check (permission layer)
  - Combined check: path, folder, project, client, --json
  - Bulk: dry-run, validation, folder with --yes
"""

import json
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
            ("mcp", "view", "--help"),
            ("mcp", "view", "show", "--help"),
            ("mcp", "view", "set", "--help"),
            ("mcp", "view", "delete", "--help"),
            ("mcp", "view", "check", "--help"),
            ("mcp", "view", "reset", "--help"),
            ("mcp", "read", "--help"),
            ("mcp", "read", "show", "--help"),
            ("mcp", "read", "set", "--help"),
            ("mcp", "read", "delete", "--help"),
            ("mcp", "read", "check", "--help"),
            ("mcp", "read", "reset", "--help"),
            ("mcp", "check", "--help"),
            ("mcp", "bulk", "--help"),
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
# View (visibility) policies
# ---------------------------------------------------------------------------


class TestMcpViewPolicies:
    """Test fp mcp view show/set/delete/reset/check."""

    def test_view_show_empty(self, policy_db):
        stdout, stderr, code = run_fp("mcp", "view", "show")
        assert code == 0
        assert "No visibility policies" in stdout

    def test_view_set_valid(self, policy_db):
        stdout, stderr, code = run_fp("mcp", "view", "set", "global", "visible")
        assert code == 0
        assert "visibility_policies" in stdout
        assert "visible" in stdout

    def test_view_set_rejects_permission_value(self, policy_db):
        """view set should reject 'allow' — that's a permission, not visibility."""
        stdout, stderr, code = run_fp("mcp", "view", "set", "global", "allow")
        assert code != 0
        assert "Invalid visibility" in stdout + stderr

    def test_view_set_then_show(self, policy_db):
        run_fp("mcp", "view", "set", "global", "hidden")
        stdout, stderr, code = run_fp("mcp", "view", "show")
        assert code == 0
        assert "hidden" in stdout

    def test_view_delete(self, policy_db):
        run_fp("mcp", "view", "set", "global", "visible")
        stdout, stderr, code = run_fp("mcp", "view", "delete", "global")
        assert code == 0
        assert "Deleted" in stdout

    def test_view_delete_nonexistent(self, policy_db):
        stdout, stderr, code = run_fp("mcp", "view", "delete", "global")
        assert code == 0
        assert "No visibility policy" in stdout

    def test_view_delete_only_touches_visibility(self, policy_db):
        """view delete should NOT touch permission_policies."""
        import sqlite3

        # Set both tables
        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "deny")

        # Delete from view only
        run_fp("mcp", "view", "delete", "global")

        # Permission still exists
        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
        conn.close()
        assert row is not None
        assert row["setting"] == "deny"

    def test_view_reset(self, policy_db):
        """view reset clears all visibility policies and re-seeds defaults."""
        run_fp("mcp", "view", "set", "folder:~/Work", "hidden")
        stdout, stderr, code = run_fp("mcp", "view", "reset")
        assert code == 0
        assert "Cleared" in stdout
        assert "Re-seeded" in stdout

        # Only global=visible should remain
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM visibility_policies").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["scope"] == "global"
        assert rows[0]["setting"] == "visible"

    def test_view_check_no_path(self, policy_db):
        """view check with no path shows global resolution."""
        run_fp("mcp", "view", "set", "global", "visible")
        stdout, stderr, code = run_fp("mcp", "view", "check")
        assert code == 0
        assert "visible" in stdout

    def test_view_show_empty_json(self, policy_db):
        """view show --json with no policies returns empty list."""
        stdout, stderr, code = run_fp("mcp", "view", "show", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert data == []

    def test_view_show_json_with_policies(self, policy_db):
        """view show --json with policies returns list with expected keys."""
        run_fp("mcp", "view", "set", "global", "visible")
        stdout, stderr, code = run_fp("mcp", "view", "show", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert len(data) >= 1
        entry = data[0]
        assert "scope" in entry
        assert "setting" in entry
        assert "updated_at" in entry
        assert entry["scope"] == "global"
        assert entry["setting"] == "visible"

    def test_view_check_with_path(self, policy_db):
        """view check with path shows simulated visibility resolution."""
        run_fp("mcp", "view", "set", "global", "hidden")
        stdout, stderr, code = run_fp("mcp", "view", "check", "/tmp/test.txt")
        assert code == 0
        assert "hidden" in stdout

    def test_view_check_finds_folder(self, policy_db):
        """view check finds a folder in the folders table instead of falling through."""
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        run_fp("mcp", "view", "set", "global", "visible")
        stdout, stderr, code = run_fp("mcp", "view", "check", "/tmp/test-folder")
        assert code == 0
        assert "Not found in files or folders" not in stdout
        assert "visible" in stdout

    def test_view_check_finds_folder_json(self, policy_db):
        """view check --json reports found_in_db true when path is in folders table."""
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        stdout, stderr, code = run_fp("mcp", "view", "check", "--json", "/tmp/test-folder")
        assert code == 0
        data = json.loads(stdout)
        assert data["found_in_db"] is True


# ---------------------------------------------------------------------------
# Read (permission) policies
# ---------------------------------------------------------------------------


class TestMcpReadPolicies:
    """Test fp mcp read show/set/delete/reset/check."""

    def test_read_show_empty(self, policy_db):
        stdout, stderr, code = run_fp("mcp", "read", "show")
        assert code == 0
        assert "No permission policies" in stdout

    def test_read_set_valid(self, policy_db):
        stdout, stderr, code = run_fp("mcp", "read", "set", "global", "deny")
        assert code == 0
        assert "permission_policies" in stdout
        assert "deny" in stdout

    def test_read_set_rejects_visibility_value(self, policy_db):
        """read set should reject 'hidden' — that's visibility, not permission."""
        stdout, stderr, code = run_fp("mcp", "read", "set", "global", "hidden")
        assert code != 0
        assert "Invalid permission" in stdout + stderr

    def test_read_set_then_show(self, policy_db):
        run_fp("mcp", "read", "set", "global", "allow")
        stdout, stderr, code = run_fp("mcp", "read", "show")
        assert code == 0
        assert "allow" in stdout

    def test_read_delete(self, policy_db):
        run_fp("mcp", "read", "set", "global", "deny")
        stdout, stderr, code = run_fp("mcp", "read", "delete", "global")
        assert code == 0
        assert "Deleted" in stdout

    def test_read_delete_only_touches_permissions(self, policy_db):
        """read delete should NOT touch visibility_policies."""
        import sqlite3

        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "deny")

        run_fp("mcp", "read", "delete", "global")

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
        conn.close()
        assert row is not None
        assert row["setting"] == "visible"

    def test_read_reset(self, policy_db):
        run_fp("mcp", "read", "set", "folder:~/Work", "allow")
        stdout, stderr, code = run_fp("mcp", "read", "reset")
        assert code == 0
        assert "Cleared" in stdout

        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM permission_policies").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["scope"] == "global"
        assert rows[0]["setting"] == "allow"

    def test_read_show_empty_json(self, policy_db):
        """read show --json with no policies returns empty list."""
        stdout, stderr, code = run_fp("mcp", "read", "show", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert data == []

    def test_read_show_json_with_policies(self, policy_db):
        """read show --json with policies returns list with expected keys."""
        run_fp("mcp", "read", "set", "global", "deny")
        stdout, stderr, code = run_fp("mcp", "read", "show", "--json")
        assert code == 0
        data = json.loads(stdout)
        assert len(data) >= 1
        entry = data[0]
        assert "scope" in entry
        assert "setting" in entry
        assert "updated_at" in entry
        assert entry["scope"] == "global"
        assert entry["setting"] == "deny"

    def test_read_check_no_path(self, policy_db):
        run_fp("mcp", "read", "set", "global", "deny")
        stdout, stderr, code = run_fp("mcp", "read", "check")
        assert code == 0
        assert "deny" in stdout

    def test_read_check_finds_folder(self, policy_db):
        """read check finds a folder in the folders table instead of falling through."""
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        run_fp("mcp", "read", "set", "global", "allow")
        stdout, stderr, code = run_fp("mcp", "read", "check", "/tmp/test-folder")
        assert code == 0
        assert "Not found in files or folders" not in stdout
        assert "allow" in stdout

    def test_read_check_finds_folder_json(self, policy_db):
        """read check --json reports found_in_db true when path is in folders table."""
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        stdout, stderr, code = run_fp("mcp", "read", "check", "--json", "/tmp/test-folder")
        assert code == 0
        data = json.loads(stdout)
        assert data["found_in_db"] is True


# ---------------------------------------------------------------------------
# Combined check
# ---------------------------------------------------------------------------


class TestMcpCheck:
    """Test fp mcp check (combined resolution)."""

    def test_check_no_target_fails(self, policy_db):
        stdout, stderr, code = run_fp("mcp", "check")
        assert code != 0
        assert "No target specified" in stdout + stderr

    def test_check_path_no_db(self, tmp_path, monkeypatch):
        """check with path but no DB shows baseline defaults."""
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(tmp_path / "nonexistent.db"))
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test.txt")
        assert code == 0
        assert "baseline" in stdout

    def test_check_path_simulated(self, policy_db):
        """check with unindexed path shows simulated resolution."""
        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "deny")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test.txt")
        assert code == 0
        assert "deny" in stdout
        assert "visible" in stdout

    def test_check_json_output(self, policy_db):
        """check with --json produces valid JSON."""
        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "deny")
        stdout, stderr, code = run_fp("mcp", "check", "--json", "/tmp/test.txt")
        assert code == 0
        data = json.loads(stdout)
        assert "permission" in data
        assert "visibility" in data
        assert data["permission"]["resolved"] == "deny"
        assert data["visibility"]["resolved"] == "visible"

    def test_check_folder_empty(self, policy_db):
        """check --folder with no files."""
        stdout, stderr, code = run_fp("mcp", "check", "--folder", "/tmp/empty")
        assert code == 0
        assert "0 files" in stdout

    def test_check_path_includes_client_scope(self, policy_db):
        """check --json shows client scope in chain when file's project has a client."""
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row

        # Insert client, project (with client_id), and file
        conn.execute(
            "INSERT INTO clients (id, name, slug, client_type, status) "
            "VALUES (99, 'Test Client', 'test-client', 'external', 'listed')"
        )
        conn.execute(
            "INSERT INTO projects (id, project_name, client_id, status) VALUES (99, 'Client Project', 99, 'listed')"
        )
        conn.execute(
            "INSERT INTO files (id, name, path, source, status, project_id, size_bytes) "
            "VALUES (99, 'file.txt', '/tmp/client-test/file.txt', 'local', 'listed', 99, 100)"
        )
        # Add a client-level permission policy
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
        import sqlite3

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO folders (id, name, path, relative_path, source) "
            "VALUES (1, 'test-folder', '/tmp/test-folder', '/tmp/test-folder', 'local')"
        )
        conn.commit()
        conn.close()

        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "allow")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/test-folder")
        assert code == 0
        assert "simulated" not in stdout.lower()
        assert "Folder Check" in stdout

    def test_check_path_finds_folder_json(self, policy_db):
        """Positional path that resolves to a folder returns folder-typed JSON."""
        import sqlite3

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
        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "allow")
        stdout, stderr, code = run_fp("mcp", "check", "/tmp/definitely-not-indexed")
        assert code == 0
        assert "Not found in files or folders" in stdout
        assert "--folder" in stdout

    def test_check_path_not_found_json(self, policy_db):
        """Unindexed path in JSON mode shows found_in_db: false."""
        run_fp("mcp", "view", "set", "global", "visible")
        run_fp("mcp", "read", "set", "global", "allow")
        stdout, stderr, code = run_fp("mcp", "check", "--json", "/tmp/definitely-not-indexed")
        assert code == 0
        data = json.loads(stdout)
        assert data["found_in_db"] is False

    def test_check_no_args_shows_usage(self, policy_db):
        """No-args error shows usage examples."""
        stdout, stderr, code = run_fp("mcp", "check")
        assert code != 0
        combined = stdout + stderr
        assert "Usage:" in combined or "fp mcp check" in combined

    def test_no_simulated_in_view_check(self, policy_db):
        """'simulated' does not appear in view check output; messaging matches combined check."""
        run_fp("mcp", "view", "set", "global", "visible")
        stdout, stderr, code = run_fp("mcp", "view", "check", "/tmp/unindexed")
        assert code == 0
        assert "simulated" not in stdout.lower()
        assert "Not found in files or folders" in stdout

    def test_no_simulated_in_read_check(self, policy_db):
        """'simulated' does not appear in read check output; messaging matches combined check."""
        run_fp("mcp", "read", "set", "global", "allow")
        stdout, stderr, code = run_fp("mcp", "read", "check", "/tmp/unindexed")
        assert code == 0
        assert "simulated" not in stdout.lower()
        assert "Not found in files or folders" in stdout


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------


class TestMcpBulk:
    """Test fp mcp bulk."""

    def test_bulk_no_target_fails(self, policy_db):
        stdout, stderr, code = run_fp(
            "mcp",
            "bulk",
            "--permission",
            "allow",
        )
        assert code != 0
        assert "Specify a target" in stdout + stderr

    def test_bulk_no_setting_fails(self, policy_db):
        stdout, stderr, code = run_fp(
            "mcp",
            "bulk",
            "--folder",
            "~/Work",
        )
        assert code != 0
        assert "Specify at least one setting" in stdout + stderr

    def test_bulk_dry_run(self, policy_db):
        stdout, stderr, code = run_fp(
            "mcp",
            "bulk",
            "--folder",
            "~/Work",
            "--visibility",
            "hidden",
            "--dry-run",
        )
        assert code == 0
        assert "Dry run" in stdout

    def test_bulk_folder_with_yes(self, policy_db):
        """bulk with --yes applies without confirmation."""
        import sqlite3

        stdout, stderr, code = run_fp(
            "mcp",
            "bulk",
            "--folder",
            "~/Work",
            "--visibility",
            "hidden",
            "--yes",
        )
        assert code == 0
        assert "Applied" in stdout

        conn = sqlite3.connect(str(policy_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'folder:~/Work'").fetchone()
        conn.close()
        assert row is not None
        assert row["setting"] == "hidden"

    def test_bulk_invalid_permission_fails(self, policy_db):
        stdout, stderr, code = run_fp(
            "mcp",
            "bulk",
            "--folder",
            "~/Work",
            "--permission",
            "visible",
        )
        assert code != 0
        assert "Invalid permission" in stdout + stderr

    def test_bulk_invalid_visibility_fails(self, policy_db):
        stdout, stderr, code = run_fp(
            "mcp",
            "bulk",
            "--folder",
            "~/Work",
            "--visibility",
            "allow",
        )
        assert code != 0
        assert "Invalid visibility" in stdout + stderr
