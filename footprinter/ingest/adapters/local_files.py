"""Local files adapter.

Wraps FileIndexer to conform to PipeAdapter protocol.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from footprinter.db import files as files_db
from footprinter.ingest.adapters.protocol import ErrorType, PipeContext, PipeResult
from footprinter.ingest.file_indexer import FileIndexer
from footprinter.source_registry import SourceRegistry

logger = logging.getLogger(__name__)


class LocalFilesAdapter:
    """Adapter wrapping FileIndexer for the local_files stage."""

    name = "local_files"
    pipe_name = "local_files"
    required_extras: List[str] = []

    def run(self, db: Any, ctx: PipeContext) -> PipeResult:
        """Index local files into files table."""
        try:
            last_run = None if ctx.full_mode else ctx.last_run
            indexer = FileIndexer(
                config_path=ctx.config_path,
                last_run=last_run,
                db=db,
                scan_roots=ctx.scan_roots,
            )

            # Build in-memory maps once before ingest
            registry = SourceRegistry(db.conn)
            folder_path_map, folder_project_map = files_db.build_folder_maps(db.conn)
            relationship_maps = {
                "project_prefix_map": files_db.build_project_prefix_map(db.conn),
                "folder_path_map": folder_path_map,
                "folder_project_map": folder_project_map,
                "remote_source_names": frozenset(registry.remote_source_names()),
            }

            counts = indexer.index_files(
                relationship_maps=relationship_maps,
                on_progress=ctx.on_progress,
            )

            return PipeResult.completed(
                "local_files",
                inserted=counts["inserted"],
                updated=counts["updated"],
                unchanged=counts["unchanged"],
                skipped=counts["skipped"],
                errors=counts["errors"],
                mode="full" if ctx.full_mode else "incremental",
            )
        except Exception as e:
            logger.error(f"local_files stage failed: {e}")
            return PipeResult.make_error(
                "local_files",
                error=str(e),
                error_type=ErrorType.RUNTIME,
            )

    def status(self, db: Any) -> Dict[str, Any]:
        """Return local file count."""
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files WHERE source = 'local' AND status != 'removed'")
        count = cursor.fetchone()[0]
        return {"local_files": count}
