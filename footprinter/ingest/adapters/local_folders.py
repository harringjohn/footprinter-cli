"""Local folders adapter.

Wraps FolderIndexer to conform to PipeAdapter protocol.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from footprinter.ingest.adapters.protocol import ErrorType, PipeContext, PipeResult
from footprinter.ingest.folder_indexer import FolderIndexer

logger = logging.getLogger(__name__)


class LocalFoldersAdapter:
    """Adapter wrapping FolderIndexer for the local_folders stage."""

    name = "local_folders"
    pipe_name = "local_folders"
    required_extras: List[str] = []

    def run(self, db: Any, ctx: PipeContext) -> PipeResult:
        """Scan local folder structure into folders."""
        try:
            indexer = FolderIndexer(ctx.source_config, db)
            root_paths = ctx.source_config.get("directories", ["~/Work", "~/Personal"])

            folders = indexer.scan_folders(root_paths)
            inserted, updated = indexer.save_folders(folders)

            return PipeResult.completed(
                "local_folders",
                folders_found=len(folders),
                inserted=inserted,
                updated=updated,
            )
        except Exception as e:
            logger.error(f"local_folders stage failed: {e}")
            return PipeResult.make_error(
                "local_folders",
                error=str(e),
                error_type=ErrorType.RUNTIME,
            )

    def status(self, db: Any) -> Dict[str, Any]:
        """Return folders count."""
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM folders")
        count = cursor.fetchone()[0]
        return {"folders": count}
