"""Tests for footprinter.cli.status — CLI status command."""

import io
import json
import sqlite3

import pytest
from rich.console import Console

from footprinter.cli.status import print_status
from footprinter.services.status_service import (
    _query_all_counts,
    get_data_counts,
    get_source_health,
)


@pytest.fixture
def status_db(tmp_path):
    """Create a DB with tool-scope schema, return (conn, db_path) for status tests.

    Uses real Database.init_db() for production-matching schema.
    The conn is for inserting test data; db_path is for get_data_counts().
    """
    db_path = tmp_path / "test.db"
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    db.conn.close()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn, db_path
    conn.close()


class TestGetDataCountsEmpty:
    """Verify all counts return safely on an empty database."""

    def test_no_crashes(self, status_db):
        conn, _ = status_db
        counts = get_data_counts(conn)
        assert isinstance(counts, dict)

    def test_new_keys_present(self, status_db):
        conn, _ = status_db
        counts = get_data_counts(conn)
        assert counts["top_chats"] == []
        assert counts["chat_date_range"] == {"earliest": None, "latest": None}
        assert counts["recent_uploads"] == []
        assert counts["recent_files"] == []
        # classifications_v2 removed — retention is app-scope

    def test_existing_keys_still_present(self, status_db):
        conn, _ = status_db
        counts = get_data_counts(conn)
        assert "files" in counts
        assert "visits" in counts
        assert "emails" in counts
        assert "chats" in counts
        assert "messages" in counts
        # "classifications" removed — retention is app-scope


class TestFolderStatusFilter:
    """Removed folders must be excluded from status counts."""

    def test_removed_folders_excluded_from_counts(self, status_db):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO folders (path, relative_path, name, source, status) "
            "VALUES ('/tmp/a', 'a', 'a', 'local', 'listed')"
        )
        conn.execute(
            "INSERT INTO folders (path, relative_path, name, source, status) "
            "VALUES ('/tmp/b', 'b', 'b', 'local', 'removed')"
        )
        conn.commit()
        counts = get_data_counts(conn)
        assert counts["folders"]["local"] == 1

    def test_removed_drive_folders_excluded(self, status_db):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO folders (path, relative_path, name, source, status) "
            "VALUES ('/drive/x', 'x', 'x', 'drive_personal', 'removed')"
        )
        conn.commit()
        counts = get_data_counts(conn)
        assert "drive_personal" not in counts["folders"]


class TestChatStats:
    """Verify top_chats and chat_date_range from chat data."""

    def _populate(self, conn):
        conn.execute(
            "INSERT INTO chats "
            "(external_id, account, title, message_count, created_at) "
            "VALUES ('c1', 'claude', 'Project planning', 42, '2025-06-01')"
        )
        conn.execute(
            "INSERT INTO chats "
            "(external_id, account, title, message_count, created_at) "
            "VALUES ('c2', 'claude', 'Code review', 15, '2025-07-10')"
        )
        conn.execute(
            "INSERT INTO chats "
            "(external_id, account, title, message_count, created_at) "
            "VALUES ('c3', 'claude', 'Bug triage', 100, '2025-05-20')"
        )
        # Add some messages too
        for i in range(5):
            conn.execute(
                "INSERT INTO messages "
                "(chat_id, message_id, role, content, created_at) "
                "VALUES (1, ?, 'user', 'msg', '2025-06-01')",
                (f"m{i}",),
            )
        conn.commit()

    def test_top_chats_ordered_by_message_count(self, status_db):
        conn, db_path = status_db
        self._populate(conn)
        counts = get_data_counts(conn)
        top = counts["top_chats"]
        assert len(top) == 3
        # Highest message_count first
        assert top[0]["title"] == "Bug triage"
        assert top[0]["message_count"] == 100
        assert top[1]["title"] == "Project planning"
        assert top[1]["message_count"] == 42

    def test_top_chats_limited_to_5(self, status_db):
        conn, db_path = status_db
        for i in range(8):
            conn.execute(
                "INSERT INTO chats "
                "(external_id, account, title, message_count, created_at) "
                "VALUES (?, 'claude', ?, ?, '2025-06-01')",
                (f"c{i}", f"Conv {i}", i * 10),
            )
        conn.commit()
        counts = get_data_counts(conn)
        assert len(counts["top_chats"]) == 5

    def test_chat_date_range(self, status_db):
        conn, db_path = status_db
        self._populate(conn)
        counts = get_data_counts(conn)
        dr = counts["chat_date_range"]
        assert dr["earliest"] == "2025-05-20"
        assert dr["latest"] == "2025-07-10"


class TestRecentUploads:
    """Verify recent_uploads from uploads table."""

    def _populate(self, conn):
        conn.execute(
            "INSERT INTO uploads "
            "(filename, file_hash, type, status, items_added, uploaded_at) "
            "VALUES ('export1.json', 'h1', 'chat', 'completed', 50, '2025-07-01')"
        )
        conn.execute(
            "INSERT INTO uploads "
            "(filename, file_hash, type, status, items_added, uploaded_at) "
            "VALUES ('export2.json', 'h2', 'chat', 'completed', 30, '2025-07-05')"
        )
        conn.execute(
            "INSERT INTO uploads "
            "(filename, file_hash, type, status, items_added, uploaded_at) "
            "VALUES ('export3.json', 'h3', 'chat', 'failed', 0, '2025-07-10')"
        )
        conn.commit()

    def test_recent_uploads_populated(self, status_db):
        conn, db_path = status_db
        self._populate(conn)
        counts = get_data_counts(conn)
        uploads = counts["recent_uploads"]
        assert len(uploads) == 3

    def test_recent_uploads_ordered_by_date(self, status_db):
        conn, db_path = status_db
        self._populate(conn)
        counts = get_data_counts(conn)
        uploads = counts["recent_uploads"]
        # Most recent first
        assert uploads[0]["filename"] == "export3.json"
        assert uploads[0]["status"] == "failed"

    def test_recent_uploads_limited_to_5(self, status_db):
        conn, db_path = status_db
        for i in range(8):
            conn.execute(
                "INSERT INTO uploads "
                "(filename, file_hash, type, status, items_added, uploaded_at) "
                "VALUES (?, ?, 'chat', 'completed', ?, '2025-07-01')",
                (f"export{i}.json", f"h{i}", i * 10),
            )
        conn.commit()
        counts = get_data_counts(conn)
        assert len(counts["recent_uploads"]) == 5

    def test_recent_uploads_has_expected_keys(self, status_db):
        conn, db_path = status_db
        self._populate(conn)
        counts = get_data_counts(conn)
        upload = counts["recent_uploads"][0]
        assert "filename" in upload
        assert "type" in upload
        assert "status" in upload
        assert "items_added" in upload
        assert "uploaded_at" in upload


# TestClassificationBreakdown removed — retention is app-scope.
# Tests for classifications_v2 and classifications keys no longer apply.


class TestLastRunFromIngests:
    """Verify last_run reads from ingests table (not dead runs table)."""

    def test_last_run_populated_from_ingests(self, status_db):
        """Seeding ingests populates last_run with mode key (not run_type)."""
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO ingests "
            "(pipe, started_at, completed_at, status, mode, items_processed, errors, elapsed_seconds) "
            "VALUES ('local_files', '2026-04-01T10:00:00', '2026-04-01T10:05:00', "
            "'completed', 'incremental', 42, 1, 300.0)"
        )
        conn.commit()
        counts = get_data_counts(conn)
        last_run = counts["last_run"]
        assert last_run is not None
        assert last_run["mode"] == "incremental"
        assert last_run["items_processed"] == 42
        assert last_run["errors"] == 1

    def test_last_run_none_when_no_ingests(self, status_db):
        """Empty ingests table → last_run is None (no MAX(indexed_at) fallback)."""
        conn, _ = status_db
        counts = get_data_counts(conn)
        assert counts["last_run"] is None


class TestLastRunPrefersAggregate:
    """When pipe='all' rows exist they win over per-pipe rows."""

    def _seed_per_pipe(self, conn):
        conn.execute(
            "INSERT INTO ingests "
            "(pipe, started_at, completed_at, status, mode, items_processed, errors, elapsed_seconds) "
            "VALUES ('local_files', '2026-04-01T10:00:00', '2026-04-01T10:05:00', "
            "'completed', 'incremental', 100, 0, 300.0)"
        )
        # folder_stats finishes last — under the legacy query this would win
        conn.execute(
            "INSERT INTO ingests "
            "(pipe, started_at, completed_at, status, mode, items_processed, errors, elapsed_seconds) "
            "VALUES ('folder_stats', '2026-04-01T10:06:00', '2026-04-01T10:06:01', "
            "'completed', 'incremental', 0, 0, 1.0)"
        )

    def test_aggregate_row_wins_over_per_pipe(self, status_db):
        conn, db_path = status_db
        self._seed_per_pipe(conn)
        conn.execute(
            "INSERT INTO ingests "
            "(pipe, started_at, completed_at, status, mode, items_processed, errors, elapsed_seconds) "
            "VALUES ('all', '2026-04-01T10:00:00', '2026-04-01T10:06:01', "
            "'completed', 'incremental', 100, 0, 361.0)"
        )
        conn.commit()

        counts = get_data_counts(conn)
        last_run = counts["last_run"]
        assert last_run is not None
        assert last_run["pipe"] == "all"
        assert last_run["items_processed"] == 100
        assert last_run["elapsed_seconds"] == 361.0

    def test_falls_back_to_per_pipe_when_no_aggregate(self, status_db):
        """Pre-fix DBs (only per-pipe rows) still render Last ingest via fallback."""
        conn, db_path = status_db
        self._seed_per_pipe(conn)
        conn.commit()

        counts = get_data_counts(conn)
        last_run = counts["last_run"]
        assert last_run is not None
        # No aggregate row exists, so legacy query selects most recent per-pipe row
        assert last_run["pipe"] == "folder_stats"


class TestMissingTables:
    """Verify OperationalError handled gracefully with bare :memory: DB."""

    def test_all_new_keys_default_safely(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        counts = _query_all_counts(cursor, {})
        # New keys should all have safe defaults
        assert counts["top_chats"] == []
        assert counts["chat_date_range"] == {"earliest": None, "latest": None}
        assert counts["recent_uploads"] == []
        assert counts["recent_files"] == []
        # classifications_v2 removed — retention is app-scope
        conn.close()

    def test_existing_keys_default_safely(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        counts = _query_all_counts(cursor, {})
        assert counts["files"] == {}
        assert counts["files_total"] == 0
        assert counts["visits"] == 0
        conn.close()


class TestJsonOutput:
    """Verify JSON output includes new keys."""

    def test_json_has_new_keys(self, status_db, monkeypatch, capsys):
        conn, db_path = status_db
        conn.commit()

        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["fp", "--json"])

        from footprinter.cli.status import main

        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        counts = data["counts"]

        assert "top_chats" in counts
        assert "chat_date_range" in counts
        assert "recent_uploads" in counts
        # classifications_v2 removed — retention is app-scope


class TestConfigLoading:
    """Verify status.main() uses source_registry.get_config (not a local loader)."""

    def test_main_calls_get_config(self, status_db, monkeypatch, capsys):
        """main() should call source_registry.get_config, not a local loader."""
        from unittest.mock import patch

        conn, db_path = status_db
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["fp", "--json"])

        with patch("footprinter.cli.status.get_config") as mock_gc:
            mock_gc.return_value = {}
            from footprinter.cli.status import main

            main()
            mock_gc.assert_called_once()

    def test_main_handles_missing_config(self, status_db, monkeypatch, capsys):
        """main() should degrade gracefully when get_config() raises."""
        from unittest.mock import patch

        conn, db_path = status_db
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["fp", "--json"])

        with patch("footprinter.cli.status.get_config") as mock_gc:
            mock_gc.side_effect = FileNotFoundError("no config")
            from footprinter.cli.status import main

            main()  # should not crash

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "counts" in data


class TestExistingBehaviorUnchanged:
    """Ensure fp status (no subcommand) still works."""

    def test_default_status_still_works(self, status_db, monkeypatch, capsys):
        conn, db_path = status_db
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["fp", "--json"])

        from footprinter.cli.status import main

        main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Should still have the full status structure
        assert "counts" in data
        assert "database" in data


def _capture_print_status(data: dict, health: dict) -> str:
    """Render print_status() to a string via a StringIO-backed Console."""
    import footprinter.cli.status as mod

    buf = io.StringIO()
    old_console = mod.console
    mod.console = Console(file=buf, force_terminal=False, width=120)
    try:
        print_status(data, health)
    finally:
        mod.console = old_console
    return buf.getvalue()


def _minimal_data(**overrides) -> dict:
    """Return minimal data dict for print_status()."""
    base = {
        "database": {"path": "/tmp/test.db", "size_mb": 1.0},
        "config": {"path": "/tmp/config.yaml", "exists": True},
        "counts": {
            "files": {},
            "files_total": 0,
            "folders": {},
            "visits": 0,
            "emails": 0,
            "chats": {},
            "messages": 0,
            "remote_source_accounts": {},
            "top_chats": [],
            "chat_date_range": {"earliest": None, "latest": None},
            "recent_uploads": [],
            "recent_files": [],
            "access_resolution": {},
        },
        "last_run": None,
    }
    base["counts"].update(overrides)
    return base


def _minimal_health(**overrides) -> dict:
    """Return minimal health dict for print_status()."""
    base = {
        "connector_rows": [],
        "remote_enabled": False,
        "semantic": {"enabled": False, "installed": False, "available": False},
    }
    base.update(overrides)
    return base


class TestChatsAndFoldersInTable:
    """Chats and folders should be rows in the main counts table, not loose text."""

    def test_chats_row_in_table(self):
        data = _minimal_data(chats={"claude": 127})
        output = _capture_print_status(data, _minimal_health())
        assert "Chats" in output
        assert "127" in output

    def test_local_and_total_folders_in_table(self):
        data = _minimal_data(folders={"local": 100, "gdrive_personal": 50})
        output = _capture_print_status(data, _minimal_health())
        assert "Local folders" in output
        assert "Total folders" in output

    def test_no_loose_chats_text(self):
        data = _minimal_data(chats={"claude": 127})
        output = _capture_print_status(data, _minimal_health())
        # Old pattern: "Chats:  claude: 127" as loose dim text — should be a table row now
        assert "Chats:" not in output

    def test_no_loose_folders_text(self):
        data = _minimal_data(folders={"local": 100})
        output = _capture_print_status(data, _minimal_health())
        # Old pattern: "Indexed folders:    local: 100" as loose dim text
        assert "Indexed folders" not in output


class TestRecentlyModifiedFiles:
    """Recently modified files table should appear before Top Chats."""

    def test_recent_files_table_rendered(self):
        data = _minimal_data(
            recent_files=[
                {"name": "test.py", "source": "local", "modified_at": "2025-07-01"},
            ]
        )
        output = _capture_print_status(data, _minimal_health())
        assert "Recently Modified" in output
        assert "test.py" in output

    def test_recent_files_before_top_chats(self):
        data = _minimal_data(
            recent_files=[
                {"name": "test.py", "source": "local", "modified_at": "2025-07-01"},
            ],
            messages=5,
            top_chats=[
                {"title": "Chat 1", "message_count": 10, "created_at": "2025-07-01"},
            ],
        )
        output = _capture_print_status(data, _minimal_health())
        recent_pos = output.find("Recently Modified")
        chats_pos = output.find("Top Chats")
        assert recent_pos < chats_pos, "Recently Modified should appear before Top Chats"

    def test_recent_files_query_exists(self, status_db):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO files (name, path, source, status, size_bytes, modified_at) "
            "VALUES ('test.py', '/tmp/test.py', 'local', 'listed', 100, '2025-07-01')"
        )
        conn.commit()
        counts = get_data_counts(conn)
        assert "recent_files" in counts
        assert len(counts["recent_files"]) == 1
        assert counts["recent_files"][0]["name"] == "test.py"


class TestPerAccountDriveRows:
    """Source Health should have one row per connector health entry."""

    def test_separate_connector_rows(self):
        health = _minimal_health(
            connector_rows=[
                {"source": "Google Drive (personal)", "status": "[green]authenticated[/green]"},
                {"source": "Google Drive (work)", "status": "[green]authenticated[/green]"},
            ],
        )
        output = _capture_print_status(_minimal_data(), health)
        assert "Google Drive (personal)" in output
        assert "Google Drive (work)" in output

    def test_no_single_google_drive_row(self):
        health = _minimal_health(
            connector_rows=[
                {"source": "Google Drive (personal)", "status": "[green]authenticated[/green]"},
                {"source": "Google Drive (work)", "status": "[red]no token[/red]"},
            ],
        )
        output = _capture_print_status(_minimal_data(), health)
        # Should not have a bare "Google Drive" row without account name
        lines = output.split("\n")
        bare_drive_lines = [ln for ln in lines if "Google Drive" in ln and "personal" not in ln and "work" not in ln]
        assert len(bare_drive_lines) == 0


class TestDisabledConnectorsOmitted:
    """Disabled connectors should not appear in Source Health."""

    def test_no_connector_rows_omits_connectors(self):
        health = _minimal_health(connector_rows=[])
        output = _capture_print_status(_minimal_data(), health)
        assert "Google Drive" not in output
        assert "Gmail" not in output


class TestVectorsSplitRows:
    """Semantic search rows should use 'Semantic Search' terminology."""

    def test_semantic_search_files_row(self):
        health = _minimal_health(
            semantic={
                "enabled": True,
                "installed": True,
                "available": True,
                "file_chunks": 500,
                "chat_docs": 100,
            }
        )
        output = _capture_print_status(_minimal_data(), health)
        assert "Semantic Search (files)" in output
        assert "500" in output

    def test_semantic_search_chats_row(self):
        health = _minimal_health(
            semantic={
                "enabled": True,
                "installed": True,
                "available": True,
                "file_chunks": 500,
                "chat_docs": 100,
            }
        )
        output = _capture_print_status(_minimal_data(), health)
        assert "Semantic Search (chats)" in output
        assert "100" in output

    def test_no_vectors_label(self):
        health = _minimal_health(
            semantic={
                "enabled": True,
                "installed": True,
                "available": True,
                "file_chunks": 500,
                "chat_docs": 100,
            }
        )
        output = _capture_print_status(_minimal_data(), health)
        assert "Vectors" not in output

    def test_semantic_not_enabled_omitted(self):
        health = _minimal_health(semantic={"enabled": False, "installed": False, "available": False})
        output = _capture_print_status(_minimal_data(), health)
        assert "Vectors" not in output
        assert "Semantic" not in output


class TestSourceHealthSemantic:
    """Semantic health should reflect config enabled/disabled state."""

    def test_semantic_disabled_not_shown(self):
        """When config has semantic disabled, health should reflect 'not enabled'."""
        from unittest.mock import patch

        config = {"semantic": {"file_vectorization": False}}
        with patch("footprinter.semantic.vector_store._semantic_available", return_value=True):
            health = get_source_health(config)
        assert health["semantic"]["enabled"] is False

    def test_semantic_enabled_packages_missing(self):
        """When config enabled but packages not importable, health shows install hint."""
        from unittest.mock import patch

        config = {"semantic": {"file_vectorization": True}}
        with patch("footprinter.semantic.vector_store._semantic_available", return_value=False):
            health = get_source_health(config)
        assert health["semantic"]["enabled"] is True
        assert health["semantic"]["installed"] is False

    def test_semantic_enabled_no_chroma_dir(self):
        """When enabled + packages present but no chroma dir, health says enabled."""
        from unittest.mock import MagicMock, patch

        config = {"semantic": {"file_vectorization": True}}
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        with (
            patch("footprinter.semantic.vector_store._semantic_available", return_value=True),
            patch("footprinter.services.status_service.get_chroma_path", return_value=mock_path),
        ):
            health = get_source_health(config)
        assert health["semantic"]["enabled"] is True
        assert health["semantic"]["installed"] is True
        assert health["semantic"]["available"] is False

    def test_semantic_enabled_active(self):
        """When enabled + packages + chroma dir, health shows active with counts."""
        from unittest.mock import MagicMock, patch

        config = {"semantic": {"file_vectorization": True}}
        mock_vs = MagicMock()
        mock_vs.get_file_stats.return_value = {"total_chunks": 500}
        mock_vs.get_chat_stats.return_value = {"total_documents": 100}

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        with (
            patch("footprinter.semantic.vector_store._semantic_available", return_value=True),
            patch("footprinter.services.status_service.get_chroma_path", return_value=mock_path),
            patch(
                "footprinter.semantic.vector_store.VectorStore.get_instance",
                return_value=mock_vs,
            ),
        ):
            health = get_source_health(config)
        assert health["semantic"]["enabled"] is True
        assert health["semantic"]["available"] is True
        assert health["semantic"]["file_chunks"] == 500
        assert health["semantic"]["chat_docs"] == 100

    def test_semantic_label_in_output(self):
        """print_status output contains 'Semantic Search' not 'Vectors'."""
        health = _minimal_health(
            semantic={
                "enabled": True,
                "installed": True,
                "available": True,
                "file_chunks": 10,
                "chat_docs": 5,
            }
        )
        output = _capture_print_status(_minimal_data(), health)
        assert "Semantic Search" in output
        assert "Vectors" not in output

    def test_semantic_disabled_missing_deps_display(self):
        """When enabled but missing deps, show install hint."""
        health = _minimal_health(semantic={"enabled": True, "installed": False, "available": False})
        output = _capture_print_status(_minimal_data(), health)
        assert "Semantic Search" in output
        assert "missing deps" in output

    def test_semantic_enabled_no_index_display(self):
        """When enabled + installed but no chroma dir, show build hint."""
        health = _minimal_health(
            semantic={
                "enabled": True,
                "installed": True,
                "available": False,
            }
        )
        output = _capture_print_status(_minimal_data(), health)
        assert "Semantic Search" in output
        assert "run fp ingest" in output


class TestDataTableStructure:
    """Data counts table should show local/drive/total breakdown."""

    def test_local_folders_row(self):
        data = _minimal_data(folders={"local": 25})
        output = _capture_print_status(data, _minimal_health())
        assert "Local folders" in output

    def test_local_files_row(self):
        data = _minimal_data(files={"local": {"count": 100, "size_mb": 50.0}})
        output = _capture_print_status(data, _minimal_health())
        assert "Local files" in output
        assert "100" in output
        assert "50.0 MB" in output

    def test_drive_folders_per_account(self):
        data = _minimal_data(
            folders={"local": 10, "gdrive_personal": 20},
            remote_source_accounts={"gdrive_personal": "personal"},
        )
        health = _minimal_health(remote_enabled=True)
        output = _capture_print_status(data, health)
        assert "Remote folders (personal)" in output

    def test_drive_files_per_account(self):
        data = _minimal_data(
            files={
                "local": {"count": 10, "size_mb": 1.0},
                "gdrive_personal": {"count": 50, "size_mb": 25.0},
            },
            remote_source_accounts={"gdrive_personal": "personal"},
        )
        health = _minimal_health(remote_enabled=True)
        output = _capture_print_status(data, health)
        assert "Remote files (personal)" in output

    def test_total_folders_row(self):
        data = _minimal_data(folders={"local": 10, "gdrive_personal": 20})
        output = _capture_print_status(data, _minimal_health())
        assert "Total folders" in output
        assert "30" in output

    def test_total_files_row(self):
        data = _minimal_data(
            files={
                "local": {"count": 10, "size_mb": 1.0},
                "gdrive_personal": {"count": 20, "size_mb": 5.0},
            }
        )
        output = _capture_print_status(data, _minimal_health())
        assert "Total files" in output
        assert "30" in output
        assert "6.0 MB" in output

    def test_projects_row_removed(self):
        data = _minimal_data(projects=5)
        output = _capture_print_status(data, _minimal_health())
        assert "Projects" not in output

    def test_drive_rows_hidden_when_disabled(self):
        data = _minimal_data(
            files={
                "local": {"count": 10, "size_mb": 1.0},
                "gdrive_personal": {"count": 50, "size_mb": 25.0},
            },
            folders={"local": 10, "gdrive_personal": 20},
            remote_source_accounts={"gdrive_personal": "personal"},
        )
        health = _minimal_health(remote_enabled=False, connector_rows=[])
        output = _capture_print_status(data, health)
        assert "Remote folders" not in output
        assert "Remote files" not in output

    def test_local_before_drive_before_totals(self):
        data = _minimal_data(
            files={
                "local": {"count": 10, "size_mb": 1.0},
                "gdrive_personal": {"count": 50, "size_mb": 25.0},
            },
            folders={"local": 10, "gdrive_personal": 20},
            remote_source_accounts={"gdrive_personal": "personal"},
        )
        health = _minimal_health(
            remote_enabled=True,
            connector_rows=[
                {"source": "Google Drive (personal)", "status": "[green]authenticated[/green]"},
            ],
        )
        output = _capture_print_status(data, health)
        local_pos = output.find("Local files")
        drive_pos = output.find("Remote files")
        total_pos = output.find("Total files")
        assert local_pos < drive_pos < total_pos

    def test_drive_rows_shown_with_zero_counts(self):
        """Drive rows should appear with 0 counts when remote is enabled."""
        data = _minimal_data(
            files={"local": {"count": 10, "size_mb": 1.0}},
            folders={"local": 5},
            remote_source_accounts={"gdrive_work": "work"},
        )
        health = _minimal_health(
            remote_enabled=True,
            connector_rows=[
                {"source": "Google Drive (work)", "status": "[green]authenticated[/green]"},
            ],
        )
        output = _capture_print_status(data, health)
        assert "Remote folders (work)" in output, "Remote folders row should appear even with 0 count"
        assert "Remote files (work)" in output, "Remote files row should appear even with 0 count"

    def test_other_sources_after_totals(self):
        data = _minimal_data(
            files={"local": {"count": 10, "size_mb": 1.0}},
            visits=100,
            emails=50,
            messages=25,
        )
        output = _capture_print_status(data, _minimal_health())
        total_pos = output.find("Total files")
        assert total_pos < output.find("Browser history")
        assert total_pos < output.find("Emails")
        assert total_pos < output.find("Chat messages")


class TestJsonIncludesRecentFiles:
    """JSON output should include recent_files key."""

    def test_json_has_recent_files(self, status_db, monkeypatch, capsys):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO files (name, path, source, status, size_bytes, modified_at) "
            "VALUES ('test.py', '/tmp/test.py', 'local', 'listed', 100, '2025-07-01')"
        )
        conn.commit()

        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(db_path))
        monkeypatch.setattr("sys.argv", ["fp", "--json"])

        from footprinter.cli.status import main

        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "recent_files" in data["counts"]
        assert len(data["counts"]["recent_files"]) == 1


# ---------------------------------------------------------------------------
# Formatting consistency tests
# ---------------------------------------------------------------------------


class TestSectionOrder:
    """Verify correct rendering order of status sections."""

    def test_source_health_before_data_counts(self):
        """Source Health should appear before the data counts table."""
        health = _minimal_health(
            connector_rows=[
                {"source": "Google Drive (work)", "status": "[green]authenticated[/green]"},
            ],
        )
        output = _capture_print_status(_minimal_data(), health)
        health_pos = output.find("Source Health")
        # Data counts table has "Source" as its first column header
        # Find the first "Source" that is a column header (in the data table)
        # Source Health title appears first, then data table column
        assert health_pos != -1, "Source Health table should be rendered"
        # The data counts table contains "Local" rows — check order vs those
        # Since both exist, Source Health must come before the data rows
        local_pos = output.find("Browser history")
        assert health_pos < local_pos, "Source Health should appear before data counts table rows"

    def test_source_health_omitted_when_no_connectors(self):
        """Source Health should not appear when no connectors are configured."""
        health = _minimal_health()
        output = _capture_print_status(_minimal_data(), health)
        assert "Source Health" not in output

    def test_uploads_before_top_chats(self):
        """Recent Uploads should render before Top Chats."""
        data = _minimal_data(
            messages=5,
            top_chats=[
                {"title": "Chat 1", "message_count": 10, "created_at": "2025-07-01"},
            ],
            recent_uploads=[
                {
                    "filename": "export.json",
                    "type": "chat",
                    "status": "completed",
                    "items_added": 50,
                    "uploaded_at": "2025-07-01",
                },
            ],
        )
        output = _capture_print_status(data, _minimal_health())
        uploads_pos = output.find("Recent Uploads")
        chats_pos = output.find("Top Chats")
        assert uploads_pos < chats_pos, "Recent Uploads should appear before Top Chats"

    def test_no_chat_history_line(self):
        """Chat history date range line should not appear."""
        data = _minimal_data(
            messages=5,
            chat_date_range={"earliest": "2025-05-01", "latest": "2025-07-01"},
            top_chats=[
                {"title": "Chat 1", "message_count": 10, "created_at": "2025-07-01"},
            ],
        )
        output = _capture_print_status(data, _minimal_health())
        assert "Chat history:" not in output


class TestColumnNames:
    """All date columns should be named 'Date'."""

    def test_recently_modified_uses_date_column(self):
        """Recently Modified table should use 'Date', not 'Modified'."""
        data = _minimal_data(
            recent_files=[
                {"name": "test.py", "source": "local", "modified_at": "2025-07-01"},
            ]
        )
        output = _capture_print_status(data, _minimal_health())
        # Find lines around "Recently Modified" — column headers follow the title
        recent_section = output[output.find("Recently Modified") :]
        # "Modified" as a standalone column name should not appear;
        # "Date" should be the column name instead
        # Check that "Date" appears in the header area (before first data row)
        first_data_line = recent_section.find("test.py")
        header_area = recent_section[:first_data_line]
        assert "Date" in header_area, "Column should be named 'Date'"
        # "Modified" only appears in the table title "Recently Modified Files",
        # not as a standalone column header
        # Count occurrences — should be 0 standalone "Modified" columns
        # The word "Modified" in "Recently Modified Files" is fine
        lines = recent_section[:first_data_line].split("\n")
        for line in lines:
            # Skip the title line
            if "Recently Modified Files" in line:
                continue
            # No other line should have standalone "Modified" as a column header
            # In Rich tables, column headers appear as separated text
            if "Modified" in line and "Recently" not in line:
                raise AssertionError(f"Found 'Modified' as column header: {line!r}")

    def test_uploads_uses_date_column(self):
        """Recent Uploads table should use 'Date', not 'When'."""
        data = _minimal_data(
            recent_uploads=[
                {
                    "filename": "export.json",
                    "type": "chat",
                    "status": "completed",
                    "items_added": 50,
                    "uploaded_at": "2025-07-01",
                },
            ],
        )
        output = _capture_print_status(data, _minimal_health())
        uploads_section = output[output.find("Recent Uploads") :]
        first_data = uploads_section.find("export.json")
        header_area = uploads_section[:first_data]
        assert "Date" in header_area, "Column should be named 'Date'"
        assert "When" not in header_area, "Column should not be named 'When'"


class TestDateFormatting:
    """All dates should use format_relative_time(), not raw ISO."""

    def test_top_chats_uses_relative_time(self):
        """Top Chats date column should show relative time, not ISO."""
        data = _minimal_data(
            messages=5,
            top_chats=[
                {
                    "title": "Chat 1",
                    "message_count": 10,
                    "created_at": "2025-07-01T12:00:00",
                },
            ],
        )
        output = _capture_print_status(data, _minimal_health())
        chats_section = output[output.find("Top Chats") :]
        # Raw ISO should not appear in the rendered output
        assert "2025-07-01T12:00:00" not in chats_section, "Top Chats should use relative time, not raw ISO"
        # Should show relative time (e.g., "Xd ago" or a formatted date)
        chat_line = next(ln for ln in chats_section.split("\n") if "Chat 1" in ln)
        assert "ago" in chat_line or "2025-07-01" in chat_line, "Should show relative time or formatted date"

    def test_uploads_uses_relative_time(self):
        """Recent Uploads date column should show relative time, not ISO."""
        data = _minimal_data(
            recent_uploads=[
                {
                    "filename": "export.json",
                    "type": "chat",
                    "status": "completed",
                    "items_added": 50,
                    "uploaded_at": "2025-07-01T12:00:00",
                },
            ],
        )
        output = _capture_print_status(data, _minimal_health())
        uploads_section = output[output.find("Recent Uploads") :]
        assert "2025-07-01T12:00:00" not in uploads_section, "Uploads should use relative time, not raw ISO"
        upload_line = next(ln for ln in uploads_section.split("\n") if "export.json" in ln)
        assert "ago" in upload_line or "2025-07-01" in upload_line, "Should show relative time or formatted date"


# ---------------------------------------------------------------------------
# RED — Dynamic connector health rows
# ---------------------------------------------------------------------------


class TestDynamicConnectorHealth:
    """get_source_health() must include connector_rows from specs with health_check."""

    def test_connector_health_rows_from_spec(self):
        """A spec with health_check produces rows in health['connector_rows']."""
        from unittest.mock import patch

        from footprinter.connectors import ConnectorSpec

        fake_rows = [
            {"source": "FakeService (work)", "status": "[green]ok[/green]"},
        ]
        spec = ConnectorSpec(
            name="fake",
            extra="fake",
            description="Fake",
            pipes=(),
            probe_module="os",
            config_sections=(),
            setup_hook="os.getcwd",
            remove_packages=(),
            health_check="fake_mod.get_health_rows",
        )

        with (
            patch("footprinter.services.status_service.discover_connectors", return_value={"fake": spec}),
            patch("footprinter.services.status_service.is_installed", return_value=True),
            patch("footprinter.services.status_service.resolve_hook") as mock_resolve,
        ):
            mock_resolve.return_value = lambda config: fake_rows
            health = get_source_health({"semantic": {}})

        assert "connector_rows" in health
        assert len(health["connector_rows"]) == 1
        assert health["connector_rows"][0]["source"] == "FakeService (work)"

    def test_no_connectors_gives_empty_rows(self):
        """With no connectors, connector_rows is empty."""
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "footprinter.services.status_service.discover_connectors", return_value={}
        ):
            health = get_source_health({})

        assert health.get("connector_rows", []) == []


class TestAccessResolution:
    """Access resolution counts (stamped vs total) in fp status."""

    def test_access_resolution_returns_stamped_and_total(self, status_db):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO files (source, name, path, status, visibility) "
            "VALUES ('local', 'a.txt', '/a', 'listed', 'full')"
        )
        conn.execute(
            "INSERT INTO files (source, name, path, status, visibility) "
            "VALUES ('local', 'b.txt', '/b', 'listed', NULL)"
        )
        conn.execute(
            "INSERT INTO emails (account, message_id, thread_id, received_at, visibility) "
            "VALUES ('test@x.com', 'msg1', 't1', '2026-01-01', 'full')"
        )
        conn.execute(
            "INSERT INTO emails (account, message_id, thread_id, received_at, visibility) "
            "VALUES ('test@x.com', 'msg2', 't2', '2026-01-01', NULL)"
        )
        conn.execute(
            "INSERT INTO chats (external_id, account, visibility) "
            "VALUES ('c1', 'slack', 'full')"
        )
        conn.commit()

        counts = get_data_counts(conn)
        ar = counts["access_resolution"]
        assert ar["files"] == {"stamped": 1, "total": 2}
        assert ar["emails"] == {"stamped": 1, "total": 2}
        assert ar["chats"] == {"stamped": 1, "total": 1}

    def test_access_resolution_files_only_listed(self, status_db):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO files (source, name, path, status, visibility) "
            "VALUES ('local', 'a.txt', '/a', 'listed', 'full')"
        )
        conn.execute(
            "INSERT INTO files (source, name, path, status, visibility) "
            "VALUES ('local', 'b.txt', '/b', 'removed', 'full')"
        )
        conn.commit()

        counts = get_data_counts(conn)
        ar = counts["access_resolution"]
        assert ar["files"]["stamped"] == 1
        assert ar["files"]["total"] == 1

    def test_access_resolution_empty_db(self, status_db):
        conn, _ = status_db
        counts = get_data_counts(conn)
        ar = counts["access_resolution"]
        assert ar["files"] == {"stamped": 0, "total": 0}
        assert ar["emails"] == {"stamped": 0, "total": 0}
        assert ar["chats"] == {"stamped": 0, "total": 0}

    def test_json_output_includes_access_resolution(self, status_db):
        conn, db_path = status_db
        conn.execute(
            "INSERT INTO files (source, name, path, status, visibility) "
            "VALUES ('local', 'a.txt', '/a', 'listed', 'full')"
        )
        conn.commit()

        counts = get_data_counts(conn)
        assert "access_resolution" in counts
        assert "files" in counts["access_resolution"]
        assert "stamped" in counts["access_resolution"]["files"]
        assert "total" in counts["access_resolution"]["files"]

    def test_rich_output_shows_access_resolution(self):
        data = _minimal_data(
            access_resolution={
                "files": {"stamped": 8, "total": 10},
                "emails": {"stamped": 3, "total": 5},
                "chats": {"stamped": 1, "total": 2},
            }
        )
        health = _minimal_health()
        output = _capture_print_status(data, health)
        assert "Access Resolution" in output
        assert "files" in output.lower()
        assert "80%" in output

    def test_rich_output_hides_when_empty(self):
        data = _minimal_data(access_resolution={})
        health = _minimal_health()
        output = _capture_print_status(data, health)
        assert "Access Resolution" not in output

    def test_rich_output_hides_when_all_zero(self):
        data = _minimal_data(
            access_resolution={
                "files": {"stamped": 0, "total": 0},
                "emails": {"stamped": 0, "total": 0},
                "chats": {"stamped": 0, "total": 0},
            }
        )
        health = _minimal_health()
        output = _capture_print_status(data, health)
        assert "Access Resolution" not in output
