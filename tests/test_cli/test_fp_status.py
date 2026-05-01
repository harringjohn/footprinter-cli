"""Tests for ``fp status`` routed subcommand.

Validates:
  1. fp status --help exits 0
  2. fp status runs without error with mocked DB
  3. fp status --json returns valid JSON
  4. fp status rejects removed subcommands (projects, clients, --detail)
  5. fp status with no DB prints informative message
  6. fp status totals respect Drive visibility
"""

import json
import sqlite3
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from conftest import run_fp
from rich.console import Console

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch targets — functions are imported into status_cmd at module level,
# so we patch them where they're looked up (status_cmd), not where defined.
_MOD = "footprinter.cli.status_cmd"

_MINIMAL_COUNTS = {
    "files": {"local": {"count": 10, "size_mb": 1.0}},
    "files_total": 10,
    "folders": {"local": 3},
    "visits": 5,
    "emails": 2,
    "chats": {},
    "messages": 0,
    "top_chats": [],
    "chat_date_range": {"earliest": None, "latest": None},
    "remote_source_accounts": {},
    "recent_uploads": [],
    "last_run": None,
}

_EMPTY_HEALTH = {
    "connector_rows": [],
    "remote_enabled": False,
    "semantic": {"installed": False, "available": False},
}


def _mock_db_path(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB at tmp_path and return its path."""
    db_path = tmp_path / "footprinter.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')")
    conn.execute("CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS clients (id INTEGER PRIMARY KEY, name TEXT)")
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStatusHelp:
    """fp status --help behaviour."""

    def test_status_help_exits_zero(self):
        stdout, stderr, code = run_fp("status", "--help")
        assert code == 0
        output = stdout + stderr
        assert any(word in output.lower() for word in ("data counts", "health", "status"))


class TestStatusRun:
    """fp status with mocked DB produces expected output."""

    @patch(f"{_MOD}.get_source_health", return_value=_EMPTY_HEALTH)
    @patch(f"{_MOD}.get_data_counts", return_value=_MINIMAL_COUNTS)
    @patch(f"{_MOD}.get_config", return_value={})
    def test_status_runs_without_error(
        self,
        _config,
        _counts,
        _health,
        tmp_path,
    ):
        db_path = _mock_db_path(tmp_path)
        with (
            patch(f"{_MOD}.get_db_path", return_value=db_path),
            patch(f"{_MOD}.get_config_path", return_value=tmp_path / "config.yaml"),
        ):
            stdout, stderr, code = run_fp("status")

        assert code == 0
        assert "Footprinter Status" in stdout

    @patch(f"{_MOD}.get_source_health", return_value=_EMPTY_HEALTH)
    @patch(f"{_MOD}.get_data_counts", return_value=_MINIMAL_COUNTS)
    @patch(f"{_MOD}.get_config", return_value={})
    def test_status_json_returns_valid_json(
        self,
        _config,
        _counts,
        _health,
        tmp_path,
    ):
        db_path = _mock_db_path(tmp_path)
        with (
            patch(f"{_MOD}.get_db_path", return_value=db_path),
            patch(f"{_MOD}.get_config_path", return_value=tmp_path / "config.yaml"),
        ):
            stdout, stderr, code = run_fp("status", "--json")

        assert code == 0
        data = json.loads(stdout)
        assert "database" in data
        assert "counts" in data
        assert "health" in data


class TestStatusSubcommandsRemoved:
    """fp status no longer accepts projects/clients subcommands."""

    def test_status_projects_rejected(self):
        stdout, stderr, code = run_fp("status", "projects")
        assert code != 0

    def test_status_clients_rejected(self):
        stdout, stderr, code = run_fp("status", "clients")
        assert code != 0

    def test_status_detail_rejected(self):
        stdout, stderr, code = run_fp("status", "--detail", "1")
        assert code != 0

    @patch(f"{_MOD}.get_source_health", return_value=_EMPTY_HEALTH)
    @patch(f"{_MOD}.get_data_counts", return_value=_MINIMAL_COUNTS)
    @patch(f"{_MOD}.get_config", return_value={})
    def test_status_without_subcommand_still_works(
        self,
        _config,
        _counts,
        _health,
        tmp_path,
    ):
        db_path = _mock_db_path(tmp_path)
        with (
            patch(f"{_MOD}.get_db_path", return_value=db_path),
            patch(f"{_MOD}.get_config_path", return_value=tmp_path / "config.yaml"),
        ):
            stdout, stderr, code = run_fp("status")
        assert code == 0


class TestStatusNoDb:
    """fp status when no database exists."""

    def test_status_no_db(self, tmp_path):
        with (
            patch(f"{_MOD}.get_db_path", return_value=tmp_path / "nonexistent.db"),
            patch(f"{_MOD}.get_config_path", return_value=tmp_path / "config.yaml"),
        ):
            stdout, stderr, code = run_fp("status")

        assert code == 0
        output = stdout + stderr
        assert "no database" in output.lower() or "not found" in output.lower()


# ---------------------------------------------------------------------------
# Totals respect Drive visibility
# ---------------------------------------------------------------------------

_STATUS_MOD = "footprinter.cli.status"


def _build_data(*, files: dict, folders: dict, remote_source_accounts: dict) -> dict:
    """Build a minimal ``data`` dict for ``print_status()``."""
    return {
        "database": {"path": "/tmp/test.db", "size_mb": 0.1},
        "config": {"path": "/tmp/config.yaml", "exists": True},
        "counts": {
            "files": files,
            "files_total": sum(info["count"] for info in files.values()),
            "folders": folders,
            "visits": 0,
            "emails": 0,
            "chats": {},
            "messages": 0,
            "top_chats": [],
            "chat_date_range": {"earliest": None, "latest": None},
            "remote_source_accounts": remote_source_accounts,
            "recent_files": [],
            "recent_uploads": [],
            "last_run": None,
        },
        "last_run": None,
    }


def _capture_status(data: dict, health: dict) -> str:
    """Call ``print_status`` and return plain-text output."""
    from footprinter.cli.status import print_status

    buf = StringIO()
    test_console = Console(file=buf, width=120, no_color=True)
    with patch(f"{_STATUS_MOD}.console", test_console):
        print_status(data, health)
    return buf.getvalue()


class TestStatusTotals:
    """Totals must only include sources whose rows are visible."""

    # Use production-realistic source names (seeded as "gdrive_{account}")
    _FILES = {
        "local": {"count": 10, "size_mb": 1.0},
        "gdrive_work": {"count": 50, "size_mb": 5.0},
    }
    _FOLDERS = {"local": 3, "gdrive_work": 20}
    _DRIVE_ACCOUNTS = {"gdrive_work": "work"}

    def test_totals_exclude_remote_when_disabled(self):
        data = _build_data(
            files=self._FILES,
            folders=self._FOLDERS,
            remote_source_accounts=self._DRIVE_ACCOUNTS,
        )
        health = {"connector_rows": [], "remote_enabled": False}
        output = _capture_status(data, health)

        # Totals should reflect local-only: 10 files, 3 folders
        assert "Total files" in output
        assert "60" not in output  # must NOT include Drive file counts
        assert "23" not in output  # must NOT include Drive folder counts
        lines = output.splitlines()
        total_files_line = next(l for l in lines if "Total files" in l)
        total_folders_line = next(l for l in lines if "Total folders" in l)
        assert "10" in total_files_line
        assert "3" in total_folders_line

    def test_totals_include_remote_when_enabled(self):
        data = _build_data(
            files=self._FILES,
            folders=self._FOLDERS,
            remote_source_accounts=self._DRIVE_ACCOUNTS,
        )
        health = {
            "connector_rows": [
                {"source": "Google Drive (work)", "status": "[green]authenticated[/green]"},
            ],
            "remote_enabled": True,
        }
        output = _capture_status(data, health)

        # Totals should include everything: 60 files, 23 folders
        lines = output.splitlines()
        total_files_line = next(l for l in lines if "Total files" in l)
        total_folders_line = next(l for l in lines if "Total folders" in l)
        assert "60" in total_files_line
        assert "23" in total_folders_line

    def test_visible_totals_excludes_remote_when_disabled(self):
        """visible_totals must match when remote is disabled."""
        from footprinter.cli.status import visible_totals

        counts = {
            "files": self._FILES,
            "folders": self._FOLDERS,
            "remote_source_accounts": self._DRIVE_ACCOUNTS,
        }
        health = {"connector_rows": [], "remote_enabled": False}
        totals = visible_totals(counts, health)
        assert totals["files"] == 10
        assert totals["folders"] == 3

    def test_data_counts_use_display_label(self):
        """Data counts should show display label from connector health rows, not raw name."""
        data = _build_data(
            files=self._FILES,
            folders=self._FOLDERS,
            remote_source_accounts=self._DRIVE_ACCOUNTS,
        )
        health = {
            "connector_rows": [
                {
                    "source": "Google Drive (Consulting)",
                    "status": "[green]authenticated[/green]",
                    "account": "work",
                    "label": "Consulting",
                },
            ],
            "remote_enabled": True,
        }
        output = _capture_status(data, health)
        assert "Remote folders (Consulting)" in output
        assert "Remote files (Consulting)" in output
        assert "Remote folders (work)" not in output

    def test_data_counts_fallback_to_raw_name(self):
        """When no connector rows, data counts fall back to raw name."""
        data = _build_data(
            files=self._FILES,
            folders=self._FOLDERS,
            remote_source_accounts=self._DRIVE_ACCOUNTS,
        )
        health = {
            "connector_rows": [],
            "remote_enabled": True,
        }
        output = _capture_status(data, health)
        assert "Remote folders (work)" in output
        assert "Remote files (work)" in output

    def test_data_counts_multi_account_distinct_labels(self):
        """Multiple Drive accounts each show their own display label."""
        data = _build_data(
            files={
                "local": {"count": 10, "size_mb": 1.0},
                "gdrive_work": {"count": 50, "size_mb": 5.0},
                "gdrive_personal": {"count": 30, "size_mb": 3.0},
            },
            folders={"local": 3, "gdrive_work": 20, "gdrive_personal": 15},
            remote_source_accounts={
                "gdrive_work": "work",
                "gdrive_personal": "personal",
            },
        )
        health = {
            "connector_rows": [
                {
                    "source": "Google Drive (Consulting)",
                    "status": "[green]authenticated[/green]",
                    "account": "work",
                    "label": "Consulting",
                },
                {
                    "source": "Google Drive (My Files)",
                    "status": "[green]authenticated[/green]",
                    "account": "personal",
                    "label": "My Files",
                },
            ],
            "remote_enabled": True,
        }
        output = _capture_status(data, health)
        assert "Google Drive (Consulting)" in output
        assert "Google Drive (My Files)" in output

    def test_visible_totals_includes_remote_when_enabled(self):
        """visible_totals must include all sources when remote enabled."""
        from footprinter.cli.status import visible_totals

        counts = {
            "files": self._FILES,
            "folders": self._FOLDERS,
            "remote_source_accounts": self._DRIVE_ACCOUNTS,
        }
        health = {"connector_rows": [], "remote_enabled": True}
        totals = visible_totals(counts, health)
        assert totals["files"] == 60
        assert totals["folders"] == 23
