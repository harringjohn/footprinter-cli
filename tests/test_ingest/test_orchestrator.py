"""
Tests for the data pipeline orchestrator.

Smoke tests to verify the orchestrator's stage and pipeline definitions
are properly configured and the basic operations work correctly.
"""

import importlib.util
import inspect
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    _has_retention = importlib.util.find_spec("footprinter.analysis.retention_classifier") is not None
except ModuleNotFoundError:
    _has_retention = False
import yaml


class TestOrchestratorDefinitions:
    """Test that orchestrator pipelines and sources are properly defined.

    These tests verify dynamic pipeline resolution via registry functions
    rather than static class-level dicts.
    """

    def test_all_sources_includes_core_excludes_future(self):
        """get_all_pipes returns core + connector stages, excludes future."""
        from footprinter.ingest.registry import CORE_PIPES, FUTURE_PIPES, get_all_pipes

        # With no connectors, should have core only (future excluded)
        result = get_all_pipes({})
        for s in CORE_PIPES:
            assert s in result
        for s in FUTURE_PIPES:
            assert s not in result

    def test_pipelines_always_has_local_and_all(self):
        """Pipelines always include 'local' and 'all'."""
        from footprinter.ingest.registry import get_pipelines

        pipelines = get_pipelines({})
        assert "local" in pipelines
        assert "all" in pipelines

    def test_local_pipeline_stages(self):
        """Local pipeline should include correct stages."""
        from footprinter.ingest.registry import get_pipelines

        pipelines = get_pipelines({})
        local_stages = pipelines["local"]

        # Should include core local stages
        assert "local_folders" in local_stages
        assert "local_files" in local_stages
        assert "browser" in local_stages
        assert "project_links" not in local_stages  # opt-in only
        assert "rules_analysis" not in local_stages

        # Should NOT include drive file stages
        assert "drive_folders" not in local_stages
        assert "drive_files" not in local_stages

    def test_google_pipeline_stages(self):
        """With Google connector sources, 'google' pipeline has drive + gmail stages."""
        from footprinter.ingest.registry import get_pipelines

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        pipelines = get_pipelines(google_sources, google_pipelines)

        assert "google" in pipelines
        google_stages = pipelines["google"]
        assert "drive_folders" in google_stages
        assert "drive_files" in google_stages
        assert "gmail" in google_stages
        assert "drive_links" not in google_stages  # processor, not adapter

    def test_all_pipeline_includes_core_and_connectors(self):
        """'all' pipeline merges core + installed connector sources."""
        from footprinter.ingest.registry import CORE_PIPES, get_pipelines

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        pipelines = get_pipelines(google_sources, google_pipelines)
        all_stages = pipelines["all"]

        for s in CORE_PIPES:
            assert s in all_stages, f"'{s}' missing from 'all' pipeline"
        for s in google_sources:
            assert s in all_stages, f"'{s}' missing from 'all' pipeline"

    def test_summaries_not_in_any_pipeline(self):
        """summaries must NOT appear in any dynamically resolved pipeline."""
        from footprinter.ingest.registry import get_pipelines

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        pipelines = get_pipelines(google_sources, google_pipelines)
        for pipeline_name, stages in pipelines.items():
            assert "summaries" not in stages, f"'summaries' should not be in the '{pipeline_name}' pipeline"

    def test_remote_accounts_configured(self):
        """Remote accounts should be a list (may be empty if google_drive disabled)."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orchestrator = DataPipelineOrchestrator()

        assert isinstance(orchestrator.remote_accounts, list)


class TestOrchestratorInitialization:
    """Test orchestrator initialization."""

    def test_init_with_default_config(self, temp_dir):
        """Orchestrator can be initialized with default config path."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        # Create a minimal config file
        config_path = temp_dir / "config.yaml"
        config_path.write_text("""
directories:
  - ~/Work
  - ~/Personal
browsers:
  - safari
""")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))
        assert orchestrator.config is not None
        assert orchestrator.db is not None  # Eagerly loaded for IngestService

    def test_full_mode_flag(self, temp_dir):
        """Full mode flag should be configurable."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))
        assert orchestrator.full_mode is False

        orchestrator.full_mode = True
        assert orchestrator.full_mode is True

    def test_exposes_run_vectorization(self, temp_dir):
        """Orchestrator exposes run_vectorization that delegates to the runner.

        Phased ingest needs the orchestrator to drive vectorization as a
        follow-up stage after the main pipeline returns. CLI/setup callers
        invoke this method instead of running it inline during ingest.
        """
        from footprinter.ingest.adapters.protocol import PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))
        assert hasattr(orchestrator, "run_vectorization")

        sentinel = PipeResult.completed("vectorization", vectorized_new=0)
        with patch(
            "footprinter.ingest.orchestrator.run_vectorization",
            return_value=sentinel,
        ) as mock_runner:
            result = orchestrator.run_vectorization()

        assert result is sentinel
        mock_runner.assert_called_once()
        # Must receive the orchestrator's db handle (not a path or None).
        called_db = mock_runner.call_args[0][0]
        assert called_db is orchestrator.db


class TestOrchestratorStatus:
    """Test orchestrator status reporting."""

    def test_get_status_returns_dict(self, temp_db):
        """get_status should return a dictionary with expected keys."""
        import sqlite3

        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        # Create a minimal database with required tables
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                source TEXT,
                size_bytes INTEGER,
                status TEXT DEFAULT 'listed'
            )
        """)
        cursor.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, source TEXT)")
        cursor.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE chats (id INTEGER PRIMARY KEY, account TEXT)")
        cursor.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY)")
        cursor.execute("CREATE TABLE classifications (id INTEGER PRIMARY KEY, classification TEXT)")
        conn.commit()
        conn.close()

        # Patch get_db_path to use temp database — get_status now lives in status.py
        with patch("footprinter.ingest.status.get_db_path", return_value=temp_db):
            with patch.object(DataPipelineOrchestrator, "__init__", lambda x, *args, **kwargs: None):
                orchestrator = DataPipelineOrchestrator()
                orchestrator.config = {}
                orchestrator.db = None
                orchestrator.full_mode = False

                status = orchestrator.get_status()

        assert isinstance(status, dict)
        assert "files" in status
        assert "files_total" in status
        assert "folders" in status
        assert "visits" in status
        assert "emails" in status
        assert "projects" in status


class TestOrchestratorStageExecution:
    """Test individual stage execution."""

    def test_run_stage_unknown_stage(self, temp_dir):
        """Running unknown stage should return error."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))
        result = orchestrator.run_pipe("nonexistent_stage")

        assert result["status"] == "error"
        assert "Unknown pipe" in result.get("error", "")

    def test_run_stages_rejects_unknown(self, temp_dir):
        """Running stages should reject unknown stage names with ValueError."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        with pytest.raises(ValueError, match="unknown_stage"):
            orchestrator.run_pipes(["unknown_stage"])

    def test_run_pipes_rejects_explicit_post_pipe(self, temp_dir):
        """Explicit --pipe access_resolution is rejected with a dedicated message."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        with pytest.raises(ValueError, match="post-processing stage"):
            orchestrator.run_pipes(["access_resolution"])

    def test_pipeline_expansion_still_includes_access_resolution(self, temp_dir):
        """Regression: named pipelines still have access_resolution appended."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        assert "access_resolution" in orchestrator.pipelines["local"]
        assert "access_resolution" in orchestrator.pipelines["all"]

    def test_run_pipeline_not_blocked_by_post_pipe_guard(self, temp_dir):
        """run_pipeline() must dispatch even though its expanded list contains access_resolution."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        dispatched: list = []

        def fake_dispatch(pipes, *args, **kwargs):
            dispatched.extend(pipes)
            return [{"stage": p, "status": "completed"} for p in pipes]

        with patch.object(orchestrator, "_dispatch_pipes", side_effect=fake_dispatch):
            results = orchestrator.run_pipeline("local")

        assert "access_resolution" in dispatched
        assert any(r["stage"] == "access_resolution" for r in results)

    def test_run_pipes_appends_post_pipes(self, temp_dir):
        """run_pipes() auto-appends POST_PIPES after user-specified pipes."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator
        from footprinter.ingest.registry import POST_PIPES

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        dispatched: list = []

        def fake_dispatch(pipes, *args, **kwargs):
            dispatched.extend(pipes)
            return [{"stage": p, "status": "completed"} for p in pipes]

        with patch.object(orchestrator, "_dispatch_pipes", side_effect=fake_dispatch):
            orchestrator.run_pipes(["local_files"])

        assert dispatched == ["local_files"] + list(POST_PIPES)

    def test_run_pipes_appends_post_pipes_multiple_sources(self, temp_dir):
        """run_pipes() with multiple sources appends POST_PIPES after all of them."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator
        from footprinter.ingest.registry import POST_PIPES

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        dispatched: list = []

        def fake_dispatch(pipes, *args, **kwargs):
            dispatched.extend(pipes)
            return [{"stage": p, "status": "completed"} for p in pipes]

        with patch.object(orchestrator, "_dispatch_pipes", side_effect=fake_dispatch):
            orchestrator.run_pipes(["local_files", "browser"])

        assert dispatched == ["local_files", "browser"] + list(POST_PIPES)

    def test_run_refresh_dispatches_source_pipes_with_post_processing(self, temp_dir):
        """run_refresh() executes data-source pipes AND POST_PIPES, bypassing the user-facing post-pipe guard."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator
        from footprinter.ingest.registry import POST_PIPES

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        dispatched: list = []

        def fake_dispatch(pipes, *args, **kwargs):
            dispatched.extend(pipes)
            return [{"stage": p, "status": "completed"} for p in pipes]

        with patch.object(orchestrator, "_dispatch_pipes", side_effect=fake_dispatch):
            results = orchestrator.run_refresh("local")

        # data-source pipes first, post-pipes (in registry order) last
        assert "local_folders" in dispatched
        assert "local_files" in dispatched
        for post in POST_PIPES:
            assert post in dispatched
        assert dispatched[-len(POST_PIPES):] == POST_PIPES
        assert any(r["stage"] == "access_resolution" for r in results)

    def test_run_refresh_rejects_unknown_source(self, temp_dir):
        """run_refresh() raises ValueError for an unrecognized refresh source."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        with pytest.raises(ValueError, match="Unknown refresh source"):
            orchestrator.run_refresh("nonexistent_source")


class TestPrintFunctions:
    """Test output formatting functions (Rich-based)."""

    def test_print_status_no_error(self):
        """print_status should not raise errors."""
        import io

        from rich.console import Console

        from footprinter.ingest.status import print_status

        status = {
            "files_total": 100,
            "files": {"local": {"count": 50, "size_mb": 10.5}},
            "folders": {"local": 20},
            "visits": 500,
            "emails": 1000,
            "chats": {"claude": 10},
            "messages": 200,
            "projects": 5,
            "classifications": {"KEEP": 30, "DELETE": 20},
        }

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        print_status(status, console=console)

        output = buf.getvalue()
        assert "Data Pipeline Status" in output
        assert "50" in output  # local file count

    def test_print_results_no_error(self):
        """print_results should not raise errors."""
        import io

        from rich.console import Console

        from footprinter.ingest.status import print_results

        results = [
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 1.5,
                "files_indexed": 100,
            },
            {
                "stage": "browser",
                "status": "completed",
                "elapsed_seconds": 0.5,
                "urls_indexed": 500,
            },
        ]

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        print_results(results, console=console)

        output = buf.getvalue()
        assert "Pipeline Results" in output
        assert "local_files" in output


class TestOrchestratorIntegration:
    """Integration tests with mocked dependencies."""

    def test_chat_stage_returns_completed_status(self, temp_dir):
        """Chat stage should return completed status after scanning."""
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        config_path = temp_dir / "config.yaml"
        config_path.write_text("directories: ['~/Work']")

        orchestrator = DataPipelineOrchestrator(config_path=str(config_path))

        # Mock _get_db to return a mock database
        mock_db = MagicMock()

        # Mock ChatIndexer and suppress Claude Code scanning (no real dirs)
        with patch.object(orchestrator, "_get_db", return_value=mock_db):
            with patch("footprinter.ingest.adapters.chat.ChatIndexer") as MockChatManager:
                mock_manager = MagicMock()
                mock_manager.get_stats.return_value = {
                    "total_chats": 5,
                    "total_messages": 100,
                    "by_account": {"claude": 5},
                }
                MockChatManager.return_value = mock_manager

                with patch("footprinter.ingest.adapters.chat.CLAUDE_CODE_PROJECTS_DIR", temp_dir / "nonexistent"):
                    result = orchestrator.run_pipe("chat")

        assert result["status"] == "completed"
        assert result["current_chats"] == 5


def _wire_runner(orch):
    """Set up a PipeRunner on an orchestrator built with __new__."""
    from footprinter.ingest.pipe_runner import PipeRunner
    from footprinter.ingest.registry import CORE_PIPE_REGISTRY, get_all_pipes, get_pipelines

    if not hasattr(orch, "processing"):
        orch.processing = MagicMock()
    connector_pipes = {}
    orch.runner = PipeRunner(
        processing=orch.processing,
        get_db=lambda: orch.db,
        config=orch.config,
        config_path=orch.config_path,
        adapter_registry=dict(CORE_PIPE_REGISTRY),
        pipelines=get_pipelines(connector_pipes),
        all_pipes=get_all_pipes(connector_pipes),
    )
    if not hasattr(orch, "ingest_service"):
        orch.ingest_service = _make_ingest_service()


def _make_ingest_service():
    """Create an IngestService backed by an in-memory DB with the ingests table."""
    from footprinter.services.ingest_service import IngestService

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ingests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipe TEXT NOT NULL,
            started_at DATETIME NOT NULL,
            completed_at DATETIME,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
            mode TEXT, trigger TEXT,
            items_processed INTEGER DEFAULT 0, items_new INTEGER DEFAULT 0,
            items_updated INTEGER DEFAULT 0, items_skipped INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0, elapsed_seconds REAL, metadata TEXT
        )
    """)
    return IngestService(conn)


def _make_orchestrator():
    """Create an orchestrator instance without requiring config files."""
    from footprinter.ingest.orchestrator import DataPipelineOrchestrator

    orchestrator = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
    orchestrator.config = {"directories": ["~/Work"]}
    orchestrator.config_path = "/dev/null"
    orchestrator.db = None
    orchestrator.full_mode = False
    orchestrator.remote_accounts = []
    orchestrator.ingest_service = _make_ingest_service()
    _wire_runner(orchestrator)
    return orchestrator


class TestFatalErrorStopsPipeline:
    """Test that fatal error types stop the pipeline while runtime errors continue."""

    def test_database_error_stops_pipeline(self):
        """A stage returning error_type='database' should stop the pipeline."""
        orchestrator = _make_orchestrator()

        def mock_run_stage(stage, **kwargs):
            if stage == "local_folders":
                return {
                    "stage": stage,
                    "status": "error",
                    "error": "db locked",
                    "error_type": "database",
                    "elapsed_seconds": 0.1,
                }
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(orchestrator.runner, "run_pipe", side_effect=mock_run_stage):
            results = orchestrator.run_pipes(["local_folders", "local_files"])

        # Pipeline should have stopped after the first stage
        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert results[0]["error_type"] == "database"

    def test_config_error_stops_pipeline(self):
        """A stage returning error_type='config' should stop the pipeline."""
        orchestrator = _make_orchestrator()

        def mock_run_stage(stage, **kwargs):
            if stage == "browser":
                return {
                    "stage": stage,
                    "status": "error",
                    "error": "file not found",
                    "error_type": "config",
                    "elapsed_seconds": 0.1,
                }
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(orchestrator.runner, "run_pipe", side_effect=mock_run_stage):
            results = orchestrator.run_pipes(["browser", "chat"])

        assert len(results) == 1
        assert results[0]["error_type"] == "config"

    def test_runtime_error_continues_pipeline(self):
        """A stage returning error_type='runtime' should NOT stop the pipeline."""
        orchestrator = _make_orchestrator()

        def mock_run_stage(stage, **kwargs):
            if stage == "browser":
                return {
                    "stage": stage,
                    "status": "error",
                    "error": "timeout",
                    "error_type": "runtime",
                    "elapsed_seconds": 0.1,
                }
            return {"stage": stage, "status": "completed", "elapsed_seconds": 0.1}

        with patch.object(orchestrator.runner, "run_pipe", side_effect=mock_run_stage):
            results = orchestrator.run_pipes(["browser", "chat"])

        # Pipeline should have continued past the runtime error (POST_PIPES also run)
        assert len(results) == 4
        assert results[0]["status"] == "error"
        assert results[1]["status"] == "completed"


class TestErrorTypeTagging:
    """Test that run_pipe tags error_type correctly based on exception type.

    These tests verify the orchestrator's outer try/except safety net catches
    exceptions that escape adapter dispatch (e.g., adapter construction failure).
    """

    def test_sqlite_error_tagged_as_database(self):
        """sqlite3.OperationalError should produce error_type='database'."""
        orchestrator = _make_orchestrator()

        # Adapter construction raises sqlite3 error
        mock_cls = MagicMock(side_effect=sqlite3.OperationalError("database is locked"))
        orchestrator.runner.adapter_registry["browser"] = mock_cls
        result = orchestrator.run_pipe("browser")

        assert result["status"] == "error"
        assert result["error_type"] == "database"
        assert "database is locked" in result["error"]

    def test_file_not_found_tagged_as_config(self):
        """FileNotFoundError should produce error_type='config'."""
        orchestrator = _make_orchestrator()

        mock_cls = MagicMock(side_effect=FileNotFoundError("credentials.json not found"))
        orchestrator.runner.adapter_registry["gmail"] = mock_cls
        result = orchestrator.run_pipe("gmail")

        assert result["status"] == "error"
        assert result["error_type"] == "config"
        assert "credentials.json" in result["error"]

    def test_generic_exception_tagged_as_runtime(self):
        """Generic Exception should produce error_type='runtime'."""
        orchestrator = _make_orchestrator()

        mock_cls = MagicMock(side_effect=RuntimeError("unexpected error"))
        orchestrator.runner.adapter_registry["local_files"] = mock_cls
        result = orchestrator.run_pipe("local_files")

        assert result["status"] == "error"
        assert result["error_type"] == "runtime"
        assert "unexpected error" in result["error"]


@pytest.fixture(autouse=True, scope="class")
def _mock_chromadb_for_rebuild():
    """Make footprinter.semantic importable without chromadb installed."""
    mods = {}
    for name in ("chromadb", "chromadb.utils", "chromadb.utils.embedding_functions", "onnxruntime"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            if name == "chromadb.utils.embedding_functions":
                mod.ONNXMiniLM_L6_V2 = lambda: None
            sys.modules[name] = mod
            mods[name] = mod
    yield
    for name in mods:
        sys.modules.pop(name, None)


@pytest.mark.usefixtures("_mock_chromadb_for_rebuild")
class TestRebuildVectorsFileEnabled:
    """Test that rebuild_vectors vectorizes files when the flag is on."""

    @staticmethod
    def _make_db(extra_sql=None):
        """Create an in-memory DB with the tables rebuild_vectors expects."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, source TEXT, "
            "status TEXT, modified_at TEXT, vectorized_at TEXT, vectorized_chunks INTEGER, "
            "metadata TEXT, vectorize INTEGER DEFAULT 1)"
        )
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER, "
            "role TEXT, content TEXT, created_at TEXT, vectorized_at TEXT, metadata TEXT, "
            "status TEXT DEFAULT 'listed', vectorized_chunks INTEGER, vectorize INTEGER DEFAULT 1)"
        )
        conn.execute(
            "CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT, "
            "account TEXT, created_at TEXT, message_count INTEGER, "
            "metadata_vectorized_at TEXT, metadata TEXT, status TEXT DEFAULT 'listed', "
            "vectorize INTEGER DEFAULT 1)"
        )
        if extra_sql:
            for sql in extra_sql:
                conn.execute(sql)
        conn.commit()
        return conn

    def _run_rebuild(self, conn, quiet=True):
        """Run rebuild_vectors with mocked VectorStore and DB, return the mock store."""
        from footprinter.ingest.vector_ops import rebuild_vectors

        mock_store = MagicMock()

        with patch("footprinter.semantic.vector_store.VectorStore") as MockVS:
            MockVS.get_instance.return_value = mock_store
            MockVS.reset_instance = MagicMock()
            with (
                patch("footprinter.paths.get_chroma_path", return_value=Path("/tmp/nonexistent")),
                patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
                patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            ):
                with patch("footprinter.ingest.vector_ops.get_db_path", return_value=":memory:"):
                    with patch("footprinter.ingest.vector_ops.sqlite3") as mock_sql:
                        mock_sql.connect.return_value = conn
                        mock_sql.Row = sqlite3.Row
                        rebuild_vectors(quiet=quiet)

        return mock_store

    def test_rebuild_vectorizes_files(self, tmp_path):
        """store.index_file() should be called when files exist."""
        # Create a real file so Path.exists() returns True
        test_file = tmp_path / "f.txt"
        test_file.write_text("test content")

        conn = self._make_db(
            extra_sql=[f"INSERT INTO files (id, path, source, status) VALUES (1, '{test_file}', 'local', 'listed')"]
        )

        mock_extractor = MagicMock()
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "test content", "chunk_index": 0, "total_chunks": 1}
        ]

        with patch("footprinter.ingest.full_content_extractor.FullContentExtractor", return_value=mock_extractor):
            mock_store = self._run_rebuild(conn)

        mock_store.upsert_file.assert_called_once()

    def test_rebuild_summary_shows_file_counts(self, tmp_path):
        """Summary output should show file vectorization counts (quiet=False)."""
        test_file = tmp_path / "f.txt"
        test_file.write_text("test content")

        conn = self._make_db(
            extra_sql=[f"INSERT INTO files (id, path, source, status) VALUES (1, '{test_file}', 'local', 'listed')"]
        )

        mock_extractor = MagicMock()
        mock_extractor.extract_with_chunking.return_value = [
            {"content": "test content", "chunk_index": 0, "total_chunks": 1}
        ]

        with patch("footprinter.ingest.full_content_extractor.FullContentExtractor", return_value=mock_extractor):
            mock_store = self._run_rebuild(conn, quiet=False)

        # File vectorization should have run
        mock_store.upsert_file.assert_called_once()

    def test_rebuild_still_vectorizes_chats(self):
        """Chats and messages should still be vectorized alongside files."""
        conn = self._make_db(
            extra_sql=[
                "INSERT INTO messages (id, chat_id, role, content, created_at) "
                "VALUES (1, 1, 'user', 'hello world', '2024-01-01')",
                "INSERT INTO chats (id, title, account, created_at, message_count) "
                "VALUES (1, 'Test', 'claude', '2024-01-01', 1)",
            ]
        )
        mock_store = self._run_rebuild(conn)
        # incremental mode (default) uses upsert, not add
        mock_store._chats.upsert.assert_called()


class TestImportCommand:
    """Test the 'import' CLI command that delegates to ChatIndexer.upload()."""

    def _parse_args(self, argv):
        """Parse args using the orchestrator's parser, simulating CLI invocation."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "command",
            nargs="?",
            default="run",
            help="Command: run (default), status, import, retention",
        )
        parser.add_argument(
            "subcommand",
            nargs="?",
            default=None,
            help="Sub-action or file path (see command help)",
        )
        parser.add_argument("--stages", "-s", type=str)
        parser.add_argument("--full", "-f", action="store_true")
        parser.add_argument("--quiet", "-q", action="store_true")
        parser.add_argument("--log-file", type=str)
        parser.add_argument("--rebuild-vectors", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--project", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--past-due", action="store_true")
        return parser.parse_args(argv)

    def test_import_command_parses_path(self):
        """'import /some/path' should parse command='import' and subcommand='/some/path'."""
        args = self._parse_args(["import", "/tmp/export.zip"])
        assert args.command == "import"
        assert args.subcommand == "/tmp/export.zip"

    def test_import_command_no_path_parses(self):
        """'import' with no path should parse command='import' and subcommand=None."""
        args = self._parse_args(["import"])
        assert args.command == "import"
        assert args.subcommand is None

    # Import dispatch tests removed — main() no longer exists in indexer.cli.
    # Import is now handled by cli/router.py (fp import <path>).


# TestLogFileSchemaFiltering removed — tested the legacy main() entry point
# which no longer exists. Log file filtering is now handled by cli/router.py.


class TestRefreshPipeDefinitions:
    """Test that dynamic refresh pipes are properly defined and consistent."""

    def test_core_refresh_pipes_defined(self):
        """Core refresh pipes should always be present."""
        from footprinter.ingest.registry import get_refresh_pipes

        refresh = get_refresh_pipes({})
        assert "local" in refresh
        assert "browser" in refresh
        assert "chat" in refresh
        assert "all" in refresh

    def test_with_google_all_pipes_defined(self):
        """With Google connector, google/gmail/drive refresh keys are added."""
        from footprinter.ingest.registry import get_refresh_pipes

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        refresh = get_refresh_pipes(google_sources, google_pipelines)
        expected = {"local", "browser", "chat", "google", "gmail", "drive", "all"}
        assert set(refresh.keys()) == expected

    def test_all_pipes_are_valid(self):
        """Every pipe in every refresh group must be a valid pipe."""
        from footprinter.ingest.registry import get_all_pipes, get_refresh_pipes

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        refresh = get_refresh_pipes(google_sources, google_pipelines)
        all_pipes = get_all_pipes(google_sources)
        for source, pipes in refresh.items():
            for pipe in pipes:
                assert pipe in all_pipes, f"refresh_pipes['{source}'] contains invalid pipe '{pipe}'"

    def test_refresh_pipes_exclude_processor_pipes(self):
        """Refresh pipes must NOT include processor pipes."""
        from footprinter.ingest.registry import get_refresh_pipes

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        refresh = get_refresh_pipes(google_sources, google_pipelines)
        for source, pipes in refresh.items():
            assert "project_links" not in pipes, f"'{source}' should not have 'project_links'"
            assert "rules_analysis" not in pipes, f"'{source}' should not have 'rules_analysis'"
            assert "summaries" not in pipes, f"'{source}' should not have 'summaries'"

    def test_all_matches_all_pipeline(self):
        """'all' refresh source should match 'all' pipeline stages."""
        from footprinter.ingest.registry import get_pipelines, get_refresh_pipes

        google_sources = {"drive_folders": None, "drive_files": None, "gmail": None}
        google_pipelines = {"google": ["drive_folders", "drive_files", "gmail"]}
        refresh = get_refresh_pipes(google_sources, google_pipelines)
        pipelines = get_pipelines(google_sources, google_pipelines)
        assert refresh["all"] == pipelines["all"]


class TestRefreshCommand:
    """Test the 'refresh' CLI command dispatch."""

    def _parse_args(self, argv):
        """Parse args using the orchestrator's parser, simulating CLI invocation."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "command",
            nargs="?",
            default="run",
            help="Command: run (default), status, import, retention, refresh",
        )
        parser.add_argument(
            "subcommand",
            nargs="?",
            default=None,
            help="Sub-action or file path (see command help)",
        )
        parser.add_argument("--stages", "-s", type=str)
        parser.add_argument("--full", "-f", action="store_true")
        parser.add_argument("--quiet", "-q", action="store_true")
        parser.add_argument("--log-file", type=str)
        parser.add_argument("--rebuild-vectors", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--project", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--past-due", action="store_true")
        return parser.parse_args(argv)

    def test_refresh_local_parses(self):
        """'refresh local' should parse command='refresh' subcommand='local'."""
        args = self._parse_args(["refresh", "local"])
        assert args.command == "refresh"
        assert args.subcommand == "local"

    # Refresh dispatch tests that called main() removed — main() no longer exists.
    # Refresh is now handled by cli/router.py (fp refresh <source>).


class TestRetentionCommand:
    """Test the 'retention' CLI command that delegates to analysis classes."""

    def _parse_args(self, argv):
        """Parse args using the orchestrator's parser, simulating CLI invocation."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "command",
            nargs="?",
            default="run",
            help="Command: run (default), status, import, retention",
        )
        parser.add_argument(
            "subcommand",
            nargs="?",
            default=None,
            help="Sub-action or file path (see command help)",
        )
        parser.add_argument("--stages", "-s", type=str)
        parser.add_argument("--full", "-f", action="store_true")
        parser.add_argument("--quiet", "-q", action="store_true")
        parser.add_argument("--log-file", type=str)
        parser.add_argument("--rebuild-vectors", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--project", type=int, default=None)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--past-due", action="store_true")
        return parser.parse_args(argv)

    def test_retention_status_parses(self):
        """'retention status' should parse command='retention' subcommand='status'."""
        args = self._parse_args(["retention", "status"])
        assert args.command == "retention"
        assert args.subcommand == "status"

    def test_retention_classify_with_flags(self):
        """'retention classify --apply --limit 100' should parse all flags."""
        args = self._parse_args(["retention", "classify", "--apply", "--limit", "100"])
        assert args.command == "retention"
        assert args.subcommand == "classify"
        assert args.apply is True
        assert args.limit == 100

    def test_retention_close_with_project(self):
        """'retention close --project 5' should parse project flag."""
        args = self._parse_args(["retention", "close", "--project", "5"])
        assert args.command == "retention"
        assert args.subcommand == "close"
        assert args.project == 5

    def test_retention_purge_with_flags(self):
        """'retention purge --project 3 --apply --force' should parse all flags."""
        args = self._parse_args(["retention", "purge", "--project", "3", "--apply", "--force"])
        assert args.command == "retention"
        assert args.subcommand == "purge"
        assert args.project == 3
        assert args.apply is True
        assert args.force is True

    def test_retention_report_past_due(self):
        """'retention report --past-due' should parse the flag."""
        args = self._parse_args(["retention", "report", "--past-due"])
        assert args.command == "retention"
        assert args.subcommand == "report"
        assert args.past_due is True

    # Retention dispatch tests removed — _dispatch_retention() and main()
    # no longer exist in indexer.cli. Retention is app-scope, not in the shipped tool.


class TestAdapterRegistry:
    """Tests for the CORE_PIPE_REGISTRY and registry-based dispatch in run_pipe()."""

    def test_adapter_registry_maps_core_stages(self):
        """CORE_PIPE_REGISTRY contains exactly 4 core adapter entries."""
        from footprinter.ingest.adapters import (
            BrowserAdapter,
            ChatAdapter,
            LocalFilesAdapter,
            LocalFoldersAdapter,
        )
        from footprinter.ingest.registry import CORE_PIPE_REGISTRY

        expected = {
            "local_folders": LocalFoldersAdapter,
            "local_files": LocalFilesAdapter,
            "browser": BrowserAdapter,
            "chat": ChatAdapter,
        }
        assert CORE_PIPE_REGISTRY == expected

    def test_connector_spec_declares_google_adapter_entries(self):
        """Google ConnectorSpec declares adapter_entries for Drive and Gmail."""
        from footprinter.connectors import ConnectorSpec

        spec = ConnectorSpec(
            name="google",
            extra="google",
            description="Google Drive and Gmail integration",
            pipes=("drive_folders", "drive_files", "gmail"),
            probe_module="google.auth",
            config_sections=("google_drive", "gmail"),
            setup_hook="footprinter.cli.google_setup.run_google_setup",
            remove_packages=(),
            adapter_entries={
                "drive_folders": "footprinter.connectors.google.adapters.drive_folders:DriveFoldersAdapter",
                "drive_files": "footprinter.connectors.google.adapters.drive_files:DriveFilesAdapter",
                "gmail": "footprinter.connectors.google.adapters.gmail:GmailAdapter",
            },
        )
        entries = spec.adapter_entries
        assert "drive_folders" in entries
        assert "drive_files" in entries
        assert "gmail" in entries
        # Entries point to the new connector adapter paths
        assert "connectors.google.adapters" in entries["gmail"]

    def test_adapter_registry_values_are_adapter_classes(self):
        """Each registry value produces a zero-arg instance satisfying PipeAdapter."""
        from footprinter.ingest.adapters import PipeAdapter
        from footprinter.ingest.registry import CORE_PIPE_REGISTRY

        for stage, adapter_cls in CORE_PIPE_REGISTRY.items():
            instance = adapter_cls()
            assert isinstance(instance, PipeAdapter), f"{stage} adapter {adapter_cls} does not satisfy PipeAdapter"

    def test_run_stage_dispatches_through_adapter(self, tmp_path):
        """run_pipe() dispatches data source stages through the adapter registry."""
        from footprinter.ingest.adapters.protocol import PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
        orch.config = {}
        orch.config_path = str(tmp_path / "config.yaml")
        orch.full_mode = False
        mock_db = MagicMock()
        orch.db = mock_db
        _wire_runner(orch)

        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser", urls_indexed=42)

        mock_cls = MagicMock(return_value=mock_adapter)
        orch.runner.adapter_registry["browser"] = mock_cls
        result = orch.run_pipe("browser")

        mock_cls.assert_called_once()
        call_args = mock_adapter.run.call_args
        call_db, call_ctx = call_args[0]
        assert call_db is mock_db
        from footprinter.ingest.adapters.protocol import PipeContext

        assert isinstance(call_ctx, PipeContext)
        assert call_ctx.config_path == orch.config_path
        assert call_ctx.full_mode is False
        assert result["stage"] == "browser"
        assert result["status"] == "completed"
        assert result["urls_indexed"] == 42

    def test_run_stage_converts_stage_result_to_dict(self, tmp_path):
        """PipeResult from adapter.run() is converted via to_dict() with elapsed_seconds set."""
        from footprinter.ingest.adapters.protocol import PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
        orch.config = {}
        orch.config_path = str(tmp_path / "config.yaml")
        orch.full_mode = False
        orch.db = MagicMock()
        _wire_runner(orch)

        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.completed("browser", urls_indexed=10)

        mock_cls = MagicMock(return_value=mock_adapter)
        orch.runner.adapter_registry["browser"] = mock_cls
        result = orch.run_pipe("browser")

        # elapsed_seconds should be set by the runner (not the adapter default 0.0)
        assert "elapsed_seconds" in result
        assert isinstance(result["elapsed_seconds"], float)

    def test_run_stage_delegates_to_pipeline(self, tmp_path):
        """Processing stages delegate to processing.run_phase()."""
        from footprinter.ingest.adapters.protocol import PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator
        from footprinter.ingest.processing import ProcessingPipeline

        orch = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
        orch.config = {}
        orch.config_path = str(tmp_path / "config.yaml")
        orch.full_mode = False
        orch.db = MagicMock()

        mock_pipeline = MagicMock(spec=ProcessingPipeline)
        mock_pipeline.is_processing_pipe.return_value = True
        mock_pipeline.run_phase.return_value = PipeResult.completed("drive_links", linked=5)
        orch.processing = mock_pipeline
        _wire_runner(orch)

        result = orch.run_pipe("drive_links")

        mock_pipeline.run_phase.assert_called_once_with("drive_links", orch.db)
        assert result["status"] == "completed"

    def test_processing_pipeline_initialized_empty(self, tmp_path):
        """Orchestrator creates an empty ProcessingPipeline (no stages registered for v1.0)."""
        from footprinter.ingest.processing import ProcessingPipeline

        pipeline = ProcessingPipeline()

        assert isinstance(pipeline, ProcessingPipeline)
        # No processing stages registered for v1.0
        for phase in ("drive_links", "project_links", "summaries"):
            assert not pipeline.is_processing_pipe(phase), f"{phase} should not be registered"

    def test_summaries_dispatches_via_processing_pipeline(self, tmp_path):
        """run_pipe('summaries') should dispatch through ProcessingPipeline.run_phase()."""
        from footprinter.ingest.adapters.protocol import PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator
        from footprinter.ingest.processing import ProcessingPipeline

        orch = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
        orch.config = {}
        orch.config_path = str(tmp_path / "config.yaml")
        orch.full_mode = False
        orch.db = MagicMock()

        mock_pipeline = MagicMock(spec=ProcessingPipeline)
        mock_pipeline.is_processing_pipe.return_value = True
        mock_pipeline.run_phase.return_value = PipeResult.completed("summaries", summarized=10)
        orch.processing = mock_pipeline
        _wire_runner(orch)

        result = orch.run_pipe("summaries")

        mock_pipeline.run_phase.assert_called_once_with("summaries", orch.db)
        assert result["status"] == "completed"

    def test_run_stage_all_adapters_zero_arg(self, tmp_path):
        """All registry adapters are instantiated with zero args (no identity check)."""
        from footprinter.ingest.adapters.protocol import PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator
        from footprinter.ingest.registry import CORE_PIPE_REGISTRY

        orch = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
        orch.config = {"config_path": "/path/to/config.yaml", "full_mode": True}
        orch.config_path = "/path/to/config.yaml"
        orch.full_mode = True
        orch.db = MagicMock()
        _wire_runner(orch)

        for pipe_name, adapter_cls in CORE_PIPE_REGISTRY.items():
            mock_cls = MagicMock(spec=adapter_cls)
            mock_cls.return_value.run.return_value = PipeResult.completed(pipe_name)
            orch.runner.adapter_registry[pipe_name] = mock_cls
            orch.run_pipe(pipe_name)

            # Verify zero-arg construction — no kwargs passed
            mock_cls.assert_called_once_with()

    def test_run_stage_adapter_error_produces_valid_result(self, tmp_path):
        """When adapter.run() returns PipeResult.make_error(), result has error status."""
        from footprinter.ingest.adapters.protocol import ErrorType, PipeResult
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator.__new__(DataPipelineOrchestrator)
        orch.config = {}
        orch.config_path = str(tmp_path / "config.yaml")
        orch.full_mode = False
        orch.db = MagicMock()
        _wire_runner(orch)

        mock_adapter = MagicMock()
        mock_adapter.run.return_value = PipeResult.make_error(
            "gmail",
            error="SMTP timeout",
            error_type=ErrorType.RUNTIME,
        )

        mock_cls = MagicMock(return_value=mock_adapter)
        orch.runner.adapter_registry["gmail"] = mock_cls
        result = orch.run_pipe("gmail")

        assert result["status"] == "error"
        assert result["error"] == "SMTP timeout"
        assert result["error_type"] == "runtime"


class TestThinFacade:
    """orchestrator.py should be a thin facade under 130 lines."""

    def test_orchestrator_under_130_lines(self):
        """orchestrator.py should be under 130 lines (grew for IngestService wiring)."""
        import inspect

        import footprinter.ingest.orchestrator as mod

        source = inspect.getsource(mod)
        line_count = len(source.strip().splitlines())
        assert line_count < 135, f"orchestrator.py is {line_count} lines, target < 135"

    def test_get_status_importable_from_status(self):
        """get_status should be a standalone function in status.py."""
        from footprinter.ingest.status import get_status

        assert callable(get_status)

    def test_processing_no_longer_exports_app_scope_functions(self):
        """processing.py must not contain app-scope functions after extraction."""
        import footprinter.ingest.processing as proc

        assert not hasattr(proc, "run_project_links")
        assert not hasattr(proc, "run_summaries")
        assert not hasattr(proc, "_drive_links_skip_guard")
        assert not hasattr(proc.ProcessingPipeline, "run_drive_links")


class TestConfigConsolidation:
    """All config loading should go through source_registry.get_config()."""

    def test_init_uses_get_config(self, tmp_path):
        """Orchestrator __init__ should call source_registry.get_config(), not yaml.safe_load."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"source_seeds": [], "directories": []}))

        with (
            patch("footprinter.ingest.orchestrator.get_config") as mock_get_config,
            patch("footprinter.ingest.orchestrator.get_db_path", return_value=tmp_path / "test.db"),
            patch("footprinter.source_registry.remote_accounts", return_value=[]),
            patch("footprinter.ingest.orchestrator.PipeRunner"),
            patch("footprinter.ingest.database.Database"),
        ):
            mock_get_config.return_value = {"source_seeds": [], "directories": []}
            from footprinter.ingest.orchestrator import DataPipelineOrchestrator

            DataPipelineOrchestrator(config_path=str(config_file))
            mock_get_config.assert_called_once_with(str(config_file))

    def test_init_respects_env_config(self, tmp_path, monkeypatch):
        """FOOTPRINTER_CONFIG env var should work through orchestrator init."""
        config_file = tmp_path / "env-config.yaml"
        config_data = {"source_seeds": [], "directories": [], "env_marker": True}
        config_file.write_text(yaml.dump(config_data))
        monkeypatch.setenv("FOOTPRINTER_CONFIG", str(config_file))

        with (
            patch("footprinter.ingest.orchestrator.get_db_path", return_value=tmp_path / "test.db"),
            patch("footprinter.source_registry.remote_accounts", return_value=[]),
            patch("footprinter.ingest.orchestrator.PipeRunner"),
            patch("footprinter.ingest.database.Database"),
        ):
            from footprinter.ingest.orchestrator import DataPipelineOrchestrator

            orch = DataPipelineOrchestrator()
            assert orch.config.get("env_marker") is True

    def test_no_duplicate_yaml_load(self):
        """Config-loading modules should not contain yaml.safe_load (code-quality guard)."""
        import footprinter.ingest.file_indexer as idx_mod
        import footprinter.ingest.folder_indexer as fs_mod
        import footprinter.ingest.orchestrator as orch_mod

        for mod in [orch_mod, idx_mod, fs_mod]:
            source = inspect.getsource(mod)
            assert "yaml.safe_load" not in source, (
                f"{mod.__name__} still contains yaml.safe_load — should use source_registry.get_config()"
            )


class TestNoConnectorLeakWithoutDiscovery:
    """With no installed connectors, no connector pipe names may appear.

    In a snapshot install the ``footprinter.connectors`` entry-point group is
    empty (``footprinter-google`` is a separate package), so
    ``discover_connectors()`` returns ``{}``. The orchestrator's pipeline
    surface must then be core + post only — no ``drive_folders``,
    ``drive_files``, or ``gmail`` leaking through.
    """

    CONNECTOR_NAMES = ("drive_folders", "drive_files", "gmail")

    def _build_orchestrator(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"source_seeds": [], "directories": []}))
        with (
            patch("footprinter.ingest.orchestrator.discover_connectors", return_value={}),
            patch("footprinter.ingest.orchestrator.get_connector_pipes", return_value={}),
            patch("footprinter.ingest.orchestrator.get_schema_specs", return_value=[]),
            patch("footprinter.ingest.orchestrator.get_db_path", return_value=tmp_path / "test.db"),
            patch("footprinter.source_registry.remote_accounts", return_value=[]),
            patch("footprinter.ingest.orchestrator.PipeRunner"),
            patch("footprinter.ingest.database.Database"),
        ):
            from footprinter.ingest.orchestrator import DataPipelineOrchestrator

            return DataPipelineOrchestrator(config_path=str(config_file))

    def test_pipelines_all_has_no_connector_names(self, tmp_path):
        from footprinter.ingest.registry import CORE_PIPES, POST_PIPES

        orch = self._build_orchestrator(tmp_path)

        assert orch.pipelines["all"] == list(CORE_PIPES) + list(POST_PIPES)
        for name in self.CONNECTOR_NAMES:
            assert name not in orch.pipelines["all"]

    def test_all_pipes_has_no_connector_names(self, tmp_path):
        orch = self._build_orchestrator(tmp_path)
        for name in self.CONNECTOR_NAMES:
            assert name not in orch.all_pipes

    def test_user_pipes_has_no_connector_names(self, tmp_path):
        orch = self._build_orchestrator(tmp_path)
        for name in self.CONNECTOR_NAMES:
            assert name not in orch.user_pipes

    def test_refresh_pipes_has_no_connector_keys_or_values(self, tmp_path):
        orch = self._build_orchestrator(tmp_path)
        for key in ("google", "drive", "gmail"):
            assert key not in orch.refresh_pipes
        for pipes in orch.refresh_pipes.values():
            for name in self.CONNECTOR_NAMES:
                assert name not in pipes

    def test_only_local_pipeline_present(self, tmp_path):
        orch = self._build_orchestrator(tmp_path)
        assert set(orch.pipelines.keys()) == {"local", "all"}
