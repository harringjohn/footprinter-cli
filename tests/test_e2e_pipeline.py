"""End-to-end pipeline test: file on disk → ingest → DB record → search → result.

Validates the full data flow that individual unit tests cover in isolation.
"""

import os
import sqlite3
from pathlib import Path

import pytest
import yaml

from footprinter.services import search_service
from footprinter.services.roles import Role

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestIngestToSearchQuery:
    """Ingest real files, then verify they're searchable via the service layer."""

    @pytest.fixture
    def pipeline_env(self, tmp_path):
        """Set up an isolated FOOTPRINTER_HOME with config and sample files."""
        fp_home = tmp_path / "fp_home"
        fp_home.mkdir()
        db_path = fp_home / "footprinter.db"

        # Create sample files to ingest
        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "hello.txt").write_text("Hello world from the pipeline test")
        (content_dir / "notes.md").write_text("# Meeting Notes\n\nDiscussed the project timeline.")
        subdir = content_dir / "src"
        subdir.mkdir()
        (subdir / "app.py").write_text("def main():\n    print('footprinter')\n")

        # Write a minimal config
        config = {
            "directories": [str(content_dir)],
            "browsers": [],
            "exclusions": {
                "always": [r".*/\.git/.*", r".*/__pycache__/.*"],
            },
            "indexing": {
                "supported_extensions": [],
                "max_file_size_mb": 0,
                "lookback_days": 14,
            },
            "semantic": {
                "file_vectorization": False,
                "chat_vectorization": False,
            },
            "source_seeds": [
                {
                    "name": "local",
                    "source_type": "file",
                    "adapter": "local_fs",
                    "label": "Local Files",
                    "icon": "folder",
                    "enabled": True,
                },
                {
                    "name": "browser",
                    "source_type": "browser",
                    "adapter": "browser",
                    "label": "Browser",
                    "icon": "globe",
                    "enabled": True,
                },
                {
                    "name": "email",
                    "source_type": "email",
                    "adapter": "gmail",
                    "label": "Email",
                    "icon": "mail",
                    "enabled": True,
                },
                {
                    "name": "chat",
                    "source_type": "chat",
                    "adapter": "chat_export",
                    "label": "Chat",
                    "icon": "message-circle",
                    "enabled": True,
                },
            ],
        }
        config_path = fp_home / "config.yaml"
        config_path.write_text(yaml.dump(config))

        # Set env vars for isolation
        old_env = {}
        env_vars = {
            "FOOTPRINTER_HOME": str(fp_home),
            "FOOTPRINTER_DB_PATH": str(db_path),
            "FOOTPRINTER_CONFIG": str(config_path),
        }
        for k, v in env_vars.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        yield {
            "fp_home": fp_home,
            "db_path": db_path,
            "config_path": config_path,
            "content_dir": content_dir,
        }

        # Restore env
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_ingest_creates_file_records(self, pipeline_env):
        """Ingesting local files creates records in the DB."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator(config_path=str(pipeline_env["config_path"]))
        orch.full_mode = True
        orch.run_pipes(["local_folders", "local_files"])
        orch.close()

        # Verify DB has our files
        conn = sqlite3.connect(str(pipeline_env["db_path"]))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT name, path FROM files WHERE status != 'removed' ORDER BY name")
        rows = cursor.fetchall()
        conn.close()

        names = {r["name"] for r in rows}
        assert "hello.txt" in names, f"hello.txt not found in DB. Got: {names}"
        assert "notes.md" in names, f"notes.md not found in DB. Got: {names}"
        assert "app.py" in names, f"app.py not found in DB. Got: {names}"

    def test_ingested_files_are_searchable(self, pipeline_env):
        """Files ingested via the pipeline are findable through search_service."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator(config_path=str(pipeline_env["config_path"]))
        orch.full_mode = True
        orch.run_pipes(["local_folders", "local_files"])
        orch.close()

        # Search via the service layer (same path MCP tools use)
        conn = sqlite3.connect(str(pipeline_env["db_path"]))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        results = search_service.search(
            conn,
            role=Role.ADMIN,
            query="hello",
            sources=["files"],
        )
        conn.close()

        files = results.get("files", [])
        assert len(files) >= 1, f"Expected at least 1 file matching 'hello', got {len(files)}"
        assert any("hello.txt" in (f.get("name", "") or f.get("path", "")) for f in files), (
            f"hello.txt not in search results: {files}"
        )

    def test_full_chain_ingest_to_search(self, pipeline_env):
        """Full chain: create files → ingest → DB verify → search → result matches."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        # Run ingest
        orch = DataPipelineOrchestrator(config_path=str(pipeline_env["config_path"]))
        orch.full_mode = True
        orch.run_pipes(["local_folders", "local_files"])
        orch.close()

        # Verify DB records
        conn = sqlite3.connect(str(pipeline_env["db_path"]))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE status != 'removed'")
        count = cursor.fetchone()["cnt"]
        assert count >= 3, f"Expected at least 3 files ingested, got {count}"

        # Verify folders were created
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM folders")
        folder_count = cursor.fetchone()["cnt"]
        assert folder_count >= 1, f"Expected at least 1 folder, got {folder_count}"

        # Search for the markdown file by keyword
        results = search_service.search(
            conn,
            role=Role.ADMIN,
            query="notes",
            sources=["files"],
        )

        files = results.get("files", [])
        assert len(files) >= 1, f"Expected at least 1 file matching 'notes', got {len(files)}"
        found = files[0]
        assert "notes.md" in (found.get("name", "") or ""), f"Expected notes.md in result, got: {found}"

        conn.close()
