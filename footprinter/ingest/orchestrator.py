"""Thin facade — coordinates pipeline pipes via delegation to extracted modules."""

import logging
from typing import Dict, List

from footprinter.connectors import discover_connectors, get_connector_pipes, get_schema_specs
from footprinter.ingest.pipe_runner import PipeRunner
from footprinter.ingest.registry import (
    CORE_PIPE_REGISTRY,
    POST_PIPES,
    get_all_pipes,
    get_pipelines,
    get_refresh_pipes,
    get_user_pipes,
)
from footprinter.paths import get_config_path, get_db_path
from footprinter.services.ingest_service import IngestService
from footprinter.source_registry import get_config

logger = logging.getLogger(__name__)


class DataPipelineOrchestrator:
    """Composition root — merges core + connector sources, delegates to PipeRunner."""

    def __init__(self, config_path: str = None):
        self.config = get_config(config_path)
        self.config_path = config_path or str(get_config_path())
        self.db = None
        self.full_mode = False
        from footprinter.source_registry import remote_accounts
        self.remote_accounts = remote_accounts()
        self._connectors = discover_connectors()
        connector_pipes = get_connector_pipes(self._connectors)
        self.adapter_registry = {**CORE_PIPE_REGISTRY, **connector_pipes}

        # Build connector metadata for pipeline resolution and skip hints
        connector_pipelines: dict[str, list[str]] = {}
        connector_pipe_map: dict[str, str] = {}
        for name, spec in self._connectors.items():
            connector_pipelines[name] = list(spec.adapter_entries.keys())
            for pipe in spec.pipes:
                connector_pipe_map[pipe] = name
            for pipe in spec.adapter_entries:
                connector_pipe_map[pipe] = name

        self.pipelines = get_pipelines(connector_pipes, connector_pipelines)
        self.refresh_pipes = get_refresh_pipes(connector_pipes, connector_pipelines)
        self.all_pipes = get_all_pipes(connector_pipes)
        self.user_pipes = get_user_pipes(connector_pipes)

        # Ensure DB schema exists (fresh installs need tables before pipes run)
        from .database import Database
        Database(str(get_db_path()), connector_specs=get_schema_specs(self._connectors)).close()
        from .processing import ProcessingPipeline, run_access_resolution
        self.processing = ProcessingPipeline()
        self.processing.register(
            "access_resolution",
            runner=lambda db: run_access_resolution(db, full_mode=self.full_mode),
        )
        self.runner = PipeRunner(
            processing=self.processing, get_db=self._get_db,
            config=self.config, config_path=self.config_path,
            adapter_registry=self.adapter_registry, pipelines=self.pipelines,
            all_pipes=self.all_pipes, user_pipes=self.user_pipes,
            connector_pipe_map=connector_pipe_map,
        )
        self.ingest_service = IngestService(self._get_db().conn, get_db=self._get_db)

    def _get_db(self):
        if self.db is None:
            from .database import Database
            self.db = Database(str(get_db_path()), connector_specs=get_schema_specs(self._connectors))
        return self.db

    def run_pipe(self, pipe: str) -> Dict:
        """Execute a single pipe by name."""
        self.runner.full_mode = self.full_mode
        mode = "full" if self.full_mode else "incremental"
        return self.ingest_service.run_pipe(pipe, mode=mode, trigger="cli", runner=self.runner)

    def run_pipeline(self, pipeline_name: str, on_pipe_start=None, on_pipe_end=None, on_progress=None) -> List[Dict]:
        """Execute all pipes in a named pipeline. Bypasses the user-facing post-pipe guard."""
        if pipeline_name not in self.runner.pipelines:
            raise ValueError(f"Unknown pipeline: {pipeline_name}. Available: {', '.join(self.runner.pipelines.keys())}")
        return self._dispatch_pipes(self.runner.pipelines[pipeline_name], on_pipe_start, on_pipe_end, on_progress)

    def run_pipes(self, pipes: List[str], on_pipe_start=None, on_pipe_end=None, on_progress=None) -> List[Dict]:
        """Execute a user-supplied pipe list. Rejects POST_PIPES (post-processing stages)."""
        post = [p for p in pipes if p in POST_PIPES]
        if post:
            raise ValueError(
                f"{post[0]} is a post-processing stage, not a user-selectable pipe. "
                f"Use 'fp ingest' or 'fp ingest --pipe <source>' to trigger it implicitly."
            )
        return self._dispatch_pipes(pipes, on_pipe_start, on_pipe_end, on_progress)

    def run_refresh(self, source: str, on_pipe_start=None, on_pipe_end=None, on_progress=None) -> List[Dict]:
        """Execute a refresh group. Shares _dispatch_pipes with run_pipeline so POST_PIPES run inline."""
        if source not in self.refresh_pipes:
            raise ValueError(f"Unknown refresh source: {source}. Available: {', '.join(self.refresh_pipes.keys())}")
        return self._dispatch_pipes(self.refresh_pipes[source], on_pipe_start, on_pipe_end, on_progress)

    def _dispatch_pipes(self, pipes, on_pipe_start, on_pipe_end, on_progress) -> List[Dict]:
        self.runner.full_mode = self.full_mode
        mode = "full" if self.full_mode else "incremental"
        hook = lambda pipe, on_progress=None: self.ingest_service.run_pipe(  # noqa: E731
            pipe, mode=mode, trigger="cli", runner=self.runner, on_progress=on_progress,
        )
        return self.ingest_service.run_pipes(
            pipes, runner=self.runner, full_mode=self.full_mode,
            on_pipe_start=on_pipe_start, on_pipe_end=on_pipe_end,
            on_progress=on_progress, pipe_hook=hook,
        )

    def get_status(self) -> Dict:
        """Return current data counts and pipeline health."""
        from footprinter.ingest.status import get_status
        return get_status(str(get_db_path()))

    def close(self):
        """Close the database connection and release resources."""
        if self.db:
            self.db.close()
            self.db = None
