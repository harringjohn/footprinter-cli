"""
File indexer that coordinates file scanning and content extraction.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from footprinter.utils.paths import abbreviate_home
from footprinter.db import files as files_db
from footprinter.source_registry import get_config

from .content_extractors import ContentExtractor
from .database import Database
from .file_scanner import FileScanner

logger = logging.getLogger(__name__)


class FileIndexer:
    """File indexer coordinating all indexing operations."""

    def __init__(
        self,
        config_path: str = None,
        last_run: Optional[datetime] = None,
        db: Optional["Database"] = None,
        scan_roots: Optional[List[str]] = None,
    ):
        """
        Initialize the indexer.

        Args:
            config_path: Path to config YAML file (default: resolved via get_config_path())
            last_run: Timestamp of last successful run. If set, only index files
                modified after this time. None means full scan.
            db: Optional shared Database handle. If None, creates its own.
            scan_roots: When set, scan only these paths instead of
                config["directories"] (FPR-1624 — used by `fp setup folders add`).
        """
        self.config = get_config(config_path)
        self.db = db if db is not None else Database()
        self._owns_db = db is None
        self.incremental = last_run is not None

        if last_run:
            logger.info(f"Incremental mode: indexing files modified since {last_run}")
        else:
            logger.info("Full scan mode (no last_run provided)")

        self._vector_store = None  # lazy

        known_paths = None
        if last_run is not None:
            known_paths = files_db.get_known_local_paths(self.db.conn)
            logger.info("Loaded %d known paths for move detection", len(known_paths))

        self.file_scanner = FileScanner(
            self.config, since_datetime=last_run, scan_roots=scan_roots,
            known_paths=known_paths,
        )
        self.content_extractor = ContentExtractor()

    def index_files(
        self,
        relationship_maps: Optional[Dict[str, Any]] = None,
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> dict:
        """Index all files from configured directories to files table.

        Args:
            relationship_maps: Optional pre-built maps for in-memory
                project/folder resolution. When provided, avoids per-row SQL.
            on_progress: Optional callback fired with cumulative file count
                after each file is processed (inserted + updated + skipped +
                unchanged + errors).

        Returns:
            Dict with keys: inserted, updated, skipped, unchanged, errors
        """
        logger.info("Starting file indexing to files...")

        inserted_count = 0
        updated_count = 0
        skipped_count = 0
        unchanged_count = 0
        error_count = 0
        total_processed = 0
        batch = []
        batch_size = 1000  # Commit every 1000 files for performance
        self._indexed_paths = set()  # Track all indexed paths for stale detection

        for file_metadata in self.file_scanner.scan_all_directories():
            try:
                # Extract content preview (only when opt-in enabled)
                if self.config.get("indexing", {}).get("content_snippets", False):
                    file_path = Path(file_metadata["file_path"])
                    content_preview = self.content_extractor.extract(file_path)
                    file_metadata["content_preview"] = content_preview
                else:
                    file_metadata["content_preview"] = None

                # Add to batch
                batch.append(file_metadata)
                self._indexed_paths.add(file_metadata["file_path"])

                # Batch insert for performance
                if len(batch) >= batch_size:
                    bi, bu, bs, bun = self._insert_batch(batch, relationship_maps)
                    inserted_count += bi
                    updated_count += bu
                    skipped_count += bs
                    unchanged_count += bun
                    batch = []
                    logger.info(
                        f"Progress: {inserted_count:,} inserted,"
                        f" {updated_count:,} updated,"
                        f" {unchanged_count:,} unchanged,"
                        f" {skipped_count:,} skipped..."
                    )

            except Exception as e:  # Intentional broad catch: batch loop must not abort on single-item errors
                logger.error(f"Error indexing file {file_metadata.get('file_path')}: {e}")
                error_count += 1
            finally:
                total_processed += 1
                if on_progress is not None:
                    on_progress(total_processed)

        # Insert remaining files
        if batch:
            bi, bu, bs, bun = self._insert_batch(batch, relationship_maps)
            inserted_count += bi
            updated_count += bu
            skipped_count += bs
            unchanged_count += bun

        # Mark stale files (no longer on disk)
        # Only do this in full mode - incremental mode only scans modified files
        if not self.incremental:
            removed_ids = files_db.mark_removed_files(self.db.conn, self._indexed_paths)
            if removed_ids:
                store = self._get_vector_store()
                if store:
                    for file_id in removed_ids:
                        try:
                            store.delete_file(file_id)
                        except Exception:  # Intentional broad catch: vector cleanup is best-effort
                            logger.warning("Failed to delete vectors for removed file_id=%s", file_id, exc_info=True)
                logger.info(f"Marked {len(removed_ids):,} files as stale (no longer on disk)")
        else:
            logger.info("Skipping stale detection in incremental mode")

        logger.info(
            f"File indexing complete: {inserted_count:,} inserted,"
            f" {updated_count:,} updated,"
            f" {unchanged_count:,} unchanged,"
            f" {skipped_count:,} skipped,"
            f" {error_count:,} errors"
        )
        return {
            "inserted": inserted_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "unchanged": unchanged_count,
            "errors": error_count,
        }

    def _get_vector_store(self):
        if self._vector_store is None:
            try:
                from footprinter.semantic.vector_store import VectorStore

                self._vector_store = VectorStore.get_instance()
            except Exception as e:  # Intentional broad catch: vector store is optional; any init failure disables it
                logger.warning("Vector store unavailable: %s", e)
                self._vector_store = False  # sentinel: don't retry
        return self._vector_store if self._vector_store is not False else None

    def _insert_batch(
        self,
        batch,
        relationship_maps: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Insert a batch of files into files table.

        Returns:
            Tuple of (inserted, updated, skipped, unchanged) counts
        """
        inserted = 0
        updated = 0
        skipped = 0
        unchanged = 0
        # Incremental mode logs per-file at INFO (counts are typically small);
        # full mode emits at DEBUG so a fresh scan doesn't flood the log.
        log_per_file = logger.info if self.incremental else logger.debug
        for file_metadata in batch:
            result = files_db.insert_file(self.db.conn, file_metadata, relationship_maps=relationship_maps)
            if result is None:
                skipped += 1
                continue
            # FPR-1721: vectorization moved to follow-up stage
            # (footprinter.ingest.processing.run_vectorization) — fast ingest only.
            result_type, _file_id = result
            if result_type == "inserted":
                inserted += 1
            elif result_type == "unchanged":
                unchanged += 1
            else:
                result_type = "updated"
                updated += 1
            if result_type != "unchanged":
                log_per_file("%s %s", result_type, abbreviate_home(file_metadata["file_path"]))
        self.db.conn.commit()
        return inserted, updated, skipped, unchanged
