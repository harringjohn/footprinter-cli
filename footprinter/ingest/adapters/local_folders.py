"""Local folders adapter.

Wraps FolderIndexer to conform to PipeAdapter protocol.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from footprinter.db.folders import mark_removed_folders
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
            inserted, updated, unchanged = indexer.save_folders(folders)

            # Mark phantom folder rows as removed (FPR-1654). Skip when no
            # paths were scanned — guards against accidental mass-remove if
            # every configured root is missing or fully excluded.
            scanned_paths = {f["path"] for f in folders}
            if scanned_paths:
                removed_ids = mark_removed_folders(db.conn, scanned_paths)
                if removed_ids:
                    logger.info(f"Marked {len(removed_ids)} folder(s) as removed")
            else:
                removed_ids = []

            return PipeResult.completed(
                "local_folders",
                folders_found=len(folders),
                inserted=inserted,
                updated=updated,
                unchanged=unchanged,
                removed=len(removed_ids),
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
