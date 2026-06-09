"""Local folders adapter.

Wraps FolderIndexer to conform to PipeAdapter protocol.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from footprinter.db.folders import link_local_folder_parents, mark_removed_folders
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
            # ctx.scan_roots scopes the scan to a specific list (e.g.
            # the folder just added via `fp setup folders add`). When unset,
            # fall back to all configured roots — the fp ingest default.
            if ctx.scan_roots is not None:
                root_paths = ctx.scan_roots
            else:
                root_paths = ctx.source_config.get("directories", ["~/Work", "~/Personal"])

            folders = indexer.scan_folders(root_paths)
            inserted, updated, unchanged = indexer.save_folders(folders)

            linked = link_local_folder_parents(db.conn)
            if linked:
                logger.info(f"Linked {linked} folder(s) to parents")

            # Mark phantom folder rows as removed. Skipped on scoped scans
            # (ctx.scan_roots is not None) — those only walk a subset of
            # configured roots, so every other folder would falsely appear
            # "missing" and get mass-marked. Mirrors the
            # `if not self.incremental:` gate around mark_removed_files in
            # FileIndexer.
            removed_ids: List[int] = []
            if ctx.scan_roots is None:
                scanned_paths = {f["path"] for f in folders}
                if scanned_paths:
                    removed_ids = mark_removed_folders(db.conn, scanned_paths)
                    db.conn.commit()
                    if removed_ids:
                        logger.info(f"Marked {len(removed_ids)} folder(s) as removed")

            return PipeResult.completed(
                "local_folders",
                folders_found=len(folders),
                inserted=inserted,
                updated=updated,
                unchanged=unchanged,
                linked=linked,
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
