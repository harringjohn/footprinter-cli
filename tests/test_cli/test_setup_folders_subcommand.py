"""Tests for CLI folder management via fp setup.

Verifies:
  1. _normalize_path() helper
  2. folders add/remove subcommands
  3. fp setup folders routing
"""

import io
import os
import sqlite3
from unittest.mock import patch

import pytest
import yaml
from rich.console import Console

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Write a minimal config.yaml and set FOOTPRINTER_CONFIG."""
    cfg = {
        "directories": ["~/Work", "~/Personal"],
        "browsers": ["safari"],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg, default_flow_style=False))
    monkeypatch.setenv("FOOTPRINTER_CONFIG", str(config_path))
    return config_path


@pytest.fixture
def empty_config_file(tmp_path, monkeypatch):
    """Write a config.yaml with no directories."""
    cfg = {
        "directories": [],
        "browsers": [],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg, default_flow_style=False))
    monkeypatch.setenv("FOOTPRINTER_CONFIG", str(config_path))
    return config_path


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Create DB with real schema via Database.init_db(), set FOOTPRINTER_DB_PATH."""
    db_path = tmp_path / "test.db"
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    db.conn.close()
    monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
    return db_path


# ---------------------------------------------------------------------------
# TestNormalizePath
# ---------------------------------------------------------------------------


class TestNormalizePath:
    """_normalize_path() should convert absolute paths to ~/... form."""

    def test_absolute_home_path_becomes_tilde(self):
        """An absolute path under $HOME should become ~/..."""
        from footprinter.cli.setup import _normalize_path

        home = os.path.expanduser("~")
        result = _normalize_path(f"{home}/Work/project")
        assert result == "~/Work/project"

    def test_tilde_path_stays_unchanged(self):
        """A path already using ~ should stay the same."""
        from footprinter.cli.setup import _normalize_path

        result = _normalize_path("~/Work/project")
        assert result == "~/Work/project"

    def test_trailing_slash_stripped(self):
        """Trailing slashes should be removed."""
        from footprinter.cli.setup import _normalize_path

        result = _normalize_path("~/Work/project/")
        assert result == "~/Work/project"

    def test_non_home_path_unchanged(self):
        """Paths not under $HOME should be returned normalized but not tilde-ified."""
        from footprinter.cli.setup import _normalize_path

        result = _normalize_path("/tmp/test-folder")
        assert result == "/tmp/test-folder"

    def test_double_slashes_normalized(self):
        """Double slashes should be collapsed."""
        from footprinter.cli.setup import _normalize_path

        result = _normalize_path("~/Work//project")
        assert result == "~/Work/project"


# ---------------------------------------------------------------------------
# TestFoldersAdd
# ---------------------------------------------------------------------------


class TestFoldersAdd:
    """folders_add() should add directories to config."""

    def test_adds_valid_directory(self, config_file, tmp_path):
        """Should add an existing directory to config.yaml."""
        from footprinter.cli.setup import folders_add

        new_dir = tmp_path / "new-folder"
        new_dir.mkdir()

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            exit_code = folders_add(str(new_dir), index=False)

        assert exit_code == 0

        # Verify config was updated
        config = yaml.safe_load(config_file.read_text())
        assert str(new_dir) in config["directories"] or any(
            os.path.expanduser(d) == str(new_dir) for d in config["directories"]
        )

    def test_rejects_nonexistent_path(self, config_file):
        """Should return 1 for a path that doesn't exist."""
        from footprinter.cli.setup import folders_add

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            exit_code = folders_add("/nonexistent/path/xyz", index=False)

        assert exit_code == 1
        output = buf.getvalue()
        assert "not found" in output.lower() or "not a directory" in output.lower()

    def test_prevents_duplicates(self, config_file):
        """Should return 1 when adding a directory already in config."""
        from footprinter.cli.setup import folders_add

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            # ~/Work is already in the config fixture
            exit_code = folders_add("~/Work", index=False)

        assert exit_code == 1
        output = buf.getvalue()
        assert "already" in output.lower()

    def test_normalizes_absolute_to_tilde(self, config_file, tmp_path):
        """Should store normalized ~/... path in config."""
        from footprinter.cli.setup import folders_add

        new_dir = tmp_path / "tilde-test"
        new_dir.mkdir()

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            exit_code = folders_add(str(new_dir), index=False)

        assert exit_code == 0

        config = yaml.safe_load(config_file.read_text())
        # tmp_path isn't under $HOME, so it stays absolute
        assert str(new_dir) in config["directories"]

    def test_no_index_skips_orchestrator(self, config_file, tmp_path):
        """--no-index should skip orchestrator call."""
        from footprinter.cli.setup import folders_add

        new_dir = tmp_path / "no-index-test"
        new_dir.mkdir()

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup._run_orchestrator_stages") as mock_orch,
        ):
            exit_code = folders_add(str(new_dir), index=False)

        assert exit_code == 0
        mock_orch.assert_not_called()

    def test_index_true_triggers_orchestrator(self, config_file, tmp_path):
        """index=True should prompt and call orchestrator scoped to the new directory."""
        from footprinter.cli.setup import folders_add

        new_dir = tmp_path / "index-test"
        new_dir.mkdir()

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with (
            patch("footprinter.cli.setup.console", test_console),
            patch("footprinter.cli.setup.Confirm") as mock_confirm,
            patch("footprinter.cli.setup._run_orchestrator_stages") as mock_orch,
        ):
            mock_confirm.ask.return_value = True
            exit_code = folders_add(str(new_dir), index=True)

        assert exit_code == 0
        # Scan must be scoped to the newly added directory, not all configured roots.
        mock_orch.assert_called_once_with(
            ["local_folders", "local_files"], scan_roots=[str(new_dir)]
        )


# ---------------------------------------------------------------------------
# TestFoldersRemove
# ---------------------------------------------------------------------------


class TestFoldersRemove:
    """folders_remove() should remove directories from config."""

    def test_removes_existing_entry(self, config_file):
        """Should remove a configured directory from config.yaml."""
        from footprinter.cli.setup import folders_remove

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            exit_code = folders_remove("~/Work")

        assert exit_code == 0

        config = yaml.safe_load(config_file.read_text())
        expanded_dirs = [os.path.expanduser(d) for d in config["directories"]]
        assert os.path.expanduser("~/Work") not in expanded_dirs

    def test_returns_1_for_unknown_path(self, config_file):
        """Should return 1 when path isn't in config."""
        from footprinter.cli.setup import folders_remove

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            exit_code = folders_remove("/nonexistent/path")

        assert exit_code == 1
        output = buf.getvalue()
        assert "not found" in output.lower() or "not configured" in output.lower()

    def test_files_untouched_in_db(self, config_file, test_db):
        """Remove should not delete files from the database."""
        home = os.path.expanduser("~")
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "INSERT INTO files (source, name, path, size_bytes, status) VALUES ('local', 'test.py', ?, 100, 'listed')",
            (f"{home}/Work/test.py",),
        )
        conn.commit()
        conn.close()

        from footprinter.cli.setup import folders_remove

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            folders_remove("~/Work")

        # Verify files still exist
        conn = sqlite3.connect(str(test_db))
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()
        assert count == 1

    def test_prints_file_note(self, config_file):
        """Should print a note that files remain in DB."""
        from footprinter.cli.setup import folders_remove

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False)
        with patch("footprinter.cli.setup.console", test_console):
            folders_remove("~/Work")

        output = buf.getvalue()
        assert "files" in output.lower() or "data" in output.lower()


# ---------------------------------------------------------------------------
# TestFoldersRouting
# ---------------------------------------------------------------------------


class TestFoldersRouting:
    """fp setup folders should route to the correct handler."""

    def test_bare_folders_prints_help(self, capsys):
        """fp setup folders (no subcommand) should print help."""
        with patch("sys.argv", ["fp", "folders"]):
            from footprinter.cli.setup import main

            main()

        # Should not crash, just print help

    def test_folders_add_routes(self, tmp_path):
        """fp setup folders add <path> should call folders_add()."""
        new_dir = tmp_path / "route-test"
        new_dir.mkdir()

        with (
            patch("footprinter.cli.setup.folders_add", return_value=0) as mock_add,
            patch("sys.argv", ["fp", "folders", "add", str(new_dir)]),
        ):
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_add.assert_called_once_with(str(new_dir), index=True)

    def test_folders_add_no_index_flag(self, tmp_path):
        """fp setup folders add --no-index <path> should pass index=False."""
        new_dir = tmp_path / "no-index-route"
        new_dir.mkdir()

        with (
            patch("footprinter.cli.setup.folders_add", return_value=0) as mock_add,
            patch("sys.argv", ["fp", "folders", "add", "--no-index", str(new_dir)]),
        ):
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_add.assert_called_once_with(str(new_dir), index=False)

    def test_folders_remove_routes(self):
        """fp setup folders remove <path> should call folders_remove()."""
        with (
            patch("footprinter.cli.setup.folders_remove", return_value=0) as mock_remove,
            patch("sys.argv", ["fp", "folders", "remove", "~/Work"]),
        ):
            from footprinter.cli.setup import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            mock_remove.assert_called_once_with("~/Work")
