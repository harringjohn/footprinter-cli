"""
Processing module — access resolution and pipeline framework.

Primary role: ``run_access_resolution`` stamps visibility and permissions
on ingested entities, with last-run-based incremental processing.
Also provides the ``ProcessingPipeline`` framework for phase registration
and dispatch.
"""

from __future__ import annotations

import logging
import signal
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from footprinter.ingest.adapters.protocol import ErrorType, PipeResult

if TYPE_CHECKING:
    from footprinter.ingest.database import Database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Last-run helpers (backed by ingests table)
# ---------------------------------------------------------------------------


def _read_last_run(conn: sqlite3.Connection, pipe: str) -> Optional[str]:
    """Read the last-completed timestamp for a pipe."""
    row = conn.execute(
        "SELECT completed_at FROM ingests WHERE pipe = ? AND status = 'completed' ORDER BY completed_at DESC LIMIT 1",
        (pipe,),
    ).fetchone()
    if row is None:
        return None
    return row[0] if isinstance(row, tuple) else row["completed_at"]


# ---------------------------------------------------------------------------
# Access resolution runner
# ---------------------------------------------------------------------------


def run_access_resolution(db: "Database", full_mode: bool = False) -> PipeResult:
    """Stamp visibility and permissions on entities.

    Args:
        db: Database instance (needs db.conn).
        full_mode: If True, recalculate everything. If False, only
            entities added/modified since the last run.

    Returns:
        PipeResult with per-entity-type counts in data.
    """
    from footprinter.access_stamper import ENTITY_META, recalculate_access, stamp_entities

    conn = db.conn
    last_run = _read_last_run(conn, "access_resolution")

    try:
        if full_mode or last_run is None:
            # Full recalculation
            stats = recalculate_access(conn, "global")
        else:
            # Incremental — only entities with indexed_at > last run
            ids_by_type: Dict[str, list] = {}
            for entity_type, meta in ENTITY_META.items():
                table = meta["table"]

                # Not all tables have indexed_at (folders, projects, clients don't)
                try:
                    conn.execute(f"SELECT indexed_at FROM {table} LIMIT 0")
                except sqlite3.OperationalError:
                    continue

                where = "indexed_at > ?"
                if meta["has_status"]:
                    where += " AND status = 'listed'"

                rows = conn.execute(f"SELECT id FROM {table} WHERE {where}", (last_run,)).fetchall()
                ids = [r["id"] if isinstance(r, sqlite3.Row) else r[0] for r in rows]

                if ids:
                    ids_by_type[entity_type] = ids

            stats = stamp_entities(conn, ids_by_type)
    except Exception as e:  # Intentional broad catch: last-resort for access resolution; pipeline must continue
        logger.error("Access resolution error: %s", e, exc_info=True)
        return PipeResult.make_error("access_resolution", str(e), ErrorType.RUNTIME)
    else:
        return PipeResult.completed("access_resolution", **stats)


# ---------------------------------------------------------------------------
# Vectorization runner
# ---------------------------------------------------------------------------

_COMMIT_INTERVAL = 100
_shutdown = False


def _handle_shutdown(signum: int, frame: Any) -> None:
    global _shutdown
    _shutdown = True
    logger.warning("Received %s — finishing current file...", signal.Signals(signum).name)


def run_vectorization(
    db: "Database",
    full_mode: bool = False,
    on_progress: Optional[Callable[[int], None]] = None,
    file_ids: Optional[List[int]] = None,
) -> PipeResult:
    """Embed files that haven't been vectorized yet.

    Split off from inline file ingest so the index is usable before
    embedding completes. Queries the files manifest, extracts chunks,
    upserts to the vector store, and stamps ``vectorized_at`` on each
    successfully embedded row.

    Args:
        db: Database instance.
        full_mode: When True, re-embed every listed file
            by dropping the ``vectorized_at IS NULL`` clause.
        on_progress: Optional callback fired with cumulative file count
            after each file is processed.
        file_ids: When provided, scope vectorization to only these file IDs.
            Empty list means no-op. None means broad (existing behavior).

    Returns:
        PipeResult — ``skipped`` when ``file_vectorization`` is disabled,
        otherwise ``completed`` (or ``completed_with_errors``) with
        per-row counts in data.
    """
    from footprinter.semantic.vector_store import VectorStore, _file_vectorization_enabled

    if not _file_vectorization_enabled():
        return PipeResult.skipped("vectorization", "file_vectorization disabled")

    counts = {
        "vectorized_new": 0,
        "vectorized_failed": 0,
        "vectorized_skipped_missing": 0,
        "vectorized_skipped_large": 0,
    }
    skipped_large_files: List[Dict[str, Any]] = []

    if file_ids is not None and len(file_ids) == 0:
        return PipeResult.completed("vectorization", skipped_large_files=skipped_large_files, **counts)

    if file_ids is not None:
        where = "status = 'listed' AND vectorize = 1"
        if not full_mode:
            where += " AND vectorized_at IS NULL"
        if len(file_ids) <= 500:
            placeholders = ",".join("?" * len(file_ids))
            where += f" AND id IN ({placeholders})"
            rows = db.conn.execute(f"SELECT id, path FROM files WHERE {where}", file_ids).fetchall()
        else:
            db.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _vec_scope (file_id INTEGER PRIMARY KEY)")
            db.conn.execute("DELETE FROM _vec_scope")
            db.conn.executemany("INSERT INTO _vec_scope (file_id) VALUES (?)", [(fid,) for fid in file_ids])
            where += " AND id IN (SELECT file_id FROM _vec_scope)"
            rows = db.conn.execute(f"SELECT id, path FROM files WHERE {where}").fetchall()
    else:
        where = "status = 'listed' AND vectorize = 1"
        if not full_mode:
            where += " AND vectorized_at IS NULL"
        rows = db.conn.execute(f"SELECT id, path FROM files WHERE {where}").fetchall()

    if not rows:
        return PipeResult.completed("vectorization", skipped_large_files=skipped_large_files, **counts)

    try:
        store = VectorStore.get_instance()
    except Exception as e:  # Intentional broad catch: vector store init failure must not crash the stage
        logger.warning("Vectorization stage: vector store unavailable: %s", e)
        return PipeResult.make_error("vectorization", f"vector store unavailable: {e}", ErrorType.RUNTIME)

    from pathlib import Path

    from footprinter.ingest.full_content_extractor import FullContentExtractor
    from footprinter.source_registry import get_config

    extractor = FullContentExtractor.from_config(get_config())
    vectorize_cap = getattr(extractor, "max_vectorize_size_bytes", 0)

    global _shutdown
    _shutdown = False
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    processed = 0
    failures: List[str] = []
    interrupted = False
    try:
        for row in rows:
            if _shutdown:
                db.conn.commit()
                interrupted = True
                break

            file_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            file_path = row["path"] if isinstance(row, sqlite3.Row) else row[1]
            processed += 1
            try:
                path = Path(file_path) if file_path else None
                if path is None or not path.exists():
                    counts["vectorized_skipped_missing"] += 1
                    continue
                # Size-cap check before extraction so we can record the skip with size.
                if vectorize_cap > 0:
                    try:
                        file_size = path.stat().st_size
                    except OSError as stat_err:
                        logger.warning(f"stat() failed for {path}; skipping size-cap check: {stat_err}")
                        file_size = None
                    if file_size is not None and file_size > vectorize_cap:
                        logger.info(
                            f"Skipping vectorization of {path.name}: {file_size} bytes "
                            f"exceeds cap of {vectorize_cap} bytes"
                        )
                        counts["vectorized_skipped_large"] += 1
                        skipped_large_files.append({"path": str(path), "size_bytes": file_size})
                        # Drop any prior vectors for this file_id so stale embeddings
                        # don't linger when a previously-small file grows past the cap.
                        try:
                            store.delete_file(file_id)
                        except Exception as e:  # Intentional broad catch: cleanup is best-effort
                            logger.debug(f"delete_file failed for {file_id}: {e}")
                        # Stamp vectorized_at with chunks=0 so the row is not re-evaluated
                        # every incremental run. Upstream ingest clears vectorized_at on
                        # file modification, so a shrunk file will be re-considered.
                        db.conn.execute(
                            "UPDATE files SET vectorized_at = CURRENT_TIMESTAMP, vectorized_chunks = 0 WHERE id = ?",
                            (file_id,),
                        )
                        continue
                chunks = extractor.extract_with_chunking(path)
                if not chunks:
                    counts["vectorized_skipped_missing"] += 1
                    continue
                metadata = {"file_type": path.suffix.lower(), "file_name": path.name}
                store.upsert_file(file_id, str(path), chunks, metadata)
                db.conn.execute(
                    "UPDATE files SET vectorized_at = CURRENT_TIMESTAMP, vectorized_chunks = ? WHERE id = ?",
                    (len(chunks), file_id),
                )
                counts["vectorized_new"] += 1
            except Exception as e:  # Intentional broad catch: per-row failure must not abort the stage
                counts["vectorized_failed"] += 1
                failures.append(f"id={file_id}: {e}")
                logger.debug("Vectorization failed for file_id=%s path=%s: %s", file_id, file_path, e)
            finally:
                if on_progress is not None:
                    on_progress(processed)
                if processed % _COMMIT_INTERVAL == 0:
                    db.conn.commit()

        if not interrupted:
            db.conn.commit()
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)

    if interrupted:
        return PipeResult.completed(
            "vectorization",
            skipped_large_files=skipped_large_files,
            interrupted=True,
            **counts,
        )
    if failures:
        return PipeResult.completed_with_errors(
            "vectorization",
            f"{len(failures)} file(s) failed to vectorize",
            skipped_large_files=skipped_large_files,
            **counts,
        )
    return PipeResult.completed(
        "vectorization",
        skipped_large_files=skipped_large_files,
        **counts,
    )


# ---------------------------------------------------------------------------
# Folder stats runner
# ---------------------------------------------------------------------------


def run_folder_stats(db: "Database") -> PipeResult:
    """Refresh pre-computed folder counts (direct_file_count, total_*).

    Wraps ``refresh_folder_counts`` so that folder stats are kept current
    after every ingest run. Always a full rebuild from the current ``files``
    table — that is the correct semantic for a derived-stats refresh, so
    this runner ignores ``full_mode``.
    """
    from footprinter.db.folders import refresh_folder_counts

    try:
        stats = refresh_folder_counts(db.conn)
    except Exception as e:  # Intentional broad catch: last-resort for folder stats; pipeline must continue
        logger.error("Folder stats refresh error: %s", e, exc_info=True)
        return PipeResult.make_error("folder_stats", str(e), ErrorType.RUNTIME)
    else:
        return PipeResult.completed("folder_stats", **stats)


# Identity mapping — pipe names map directly to phase names.
PIPE_TO_PHASE: Dict[str, str] = {}


@dataclass
class PhaseSpec:
    """Specification for a single processing phase."""

    name: str
    skip_guard: Optional[Callable[["Database"], bool]] = None
    runner: Optional[Callable[["Database"], PipeResult]] = None


class ProcessingPipeline:
    """Pipeline for processing stages.

    Phases are registered with a runner callable and optional skip guard.
    Execution order follows registration order.
    """

    def __init__(self) -> None:
        self._phases: Dict[str, PhaseSpec] = {}

    def register(
        self,
        name: str,
        runner: Optional[Callable[["Database"], PipeResult]] = None,
        skip_guard: Optional[Callable[["Database"], bool]] = None,
    ) -> None:
        """Register a processing phase."""
        self._phases[name] = PhaseSpec(
            name=name,
            skip_guard=skip_guard,
            runner=runner,
        )

    def is_processing_pipe(self, pipe_name: str) -> bool:
        """Check if a pipe name maps to a registered processing phase."""
        phase_name = PIPE_TO_PHASE.get(pipe_name, pipe_name)
        return phase_name in self._phases

    @property
    def phase_names(self) -> List[str]:
        """Return phase names in registration order."""
        return list(self._phases.keys())

    def run_phase(self, pipe_name: str, db: "Database") -> PipeResult:
        """Execute a processing phase by pipe name.

        Applies skip guard, then calls runner directly.
        """
        phase_name = PIPE_TO_PHASE.get(pipe_name, pipe_name)
        spec = self._phases.get(phase_name)

        if spec is None:
            return PipeResult.make_error(pipe_name, f"Unknown processing phase: {phase_name}")

        if spec.runner is None:
            return PipeResult.make_error(pipe_name, f"No runner registered for phase: {phase_name}")

        # Check skip guard
        if spec.skip_guard is not None:
            try:
                should_skip = spec.skip_guard(db)
                if should_skip:
                    return PipeResult.skipped(pipe_name, f"Skip guard triggered for {phase_name}")
            except Exception as e:
                logger.warning(f"Skip guard for {phase_name} raised {type(e).__name__}: {e}; proceeding")

        return spec.runner(db)
