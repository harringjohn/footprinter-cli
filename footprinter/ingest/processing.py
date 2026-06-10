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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from footprinter.ingest.adapters.protocol import ErrorType, PipeResult

if TYPE_CHECKING:
    from footprinter.ingest.database import Database

logger = logging.getLogger(__name__)

_DEFAULT_VECTORIZE_STATUSES = ["listed"]


def _get_vectorize_statuses() -> List[str]:
    try:
        from footprinter.source_registry import get_config

        val = get_config().get("semantic", {}).get(
            "vectorize_statuses", _DEFAULT_VECTORIZE_STATUSES
        )
        if isinstance(val, list) and len(val) > 0:
            return val
        logger.warning(
            "semantic.vectorize_statuses: expected non-empty list, using default %s",
            _DEFAULT_VECTORIZE_STATUSES,
        )
        return list(_DEFAULT_VECTORIZE_STATUSES)
    except Exception as e:
        logger.debug("Config unavailable for vectorize_statuses: %s", e)
        return list(_DEFAULT_VECTORIZE_STATUSES)


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


def _embed_one_file(
    store: Any,
    extractor: Any,
    db_conn: Any,
    file_id: int,
    file_path: Optional[str],
    *,
    vectorize_cap: int = 0,
    use_upsert: bool = True,
) -> tuple[str, int]:
    """Embed a single file and stamp its vectorization state.

    Shared by both file-vectorization entry points (``run_vectorization`` and
    ``_vectorize_files``) so the per-file extract -> size-cap -> store-write ->
    stamp body cannot drift between them.

    Performs: path resolution + existence check, size-cap enforcement (drops any
    prior vectors and stamps ``vectorized_chunks = 0`` on oversize), content
    extraction + chunking, empty-chunk skip, the chroma write (``upsert_file``
    when ``use_upsert`` else ``index_file``), and the ``vectorized_at`` /
    ``vectorized_chunks`` SQLite stamp on success.

    Signal handling, progress reporting, commit cadence, and row selection stay
    with the caller. Per-file failures (extraction, chroma write) propagate so
    the caller can count and log them.

    Returns:
        ``(outcome, chunks)`` where outcome is one of ``"new"``,
        ``"skipped_missing"``, or ``"skipped_large"`` and ``chunks`` is the
        number of chunks written (0 for skips).
    """
    path = Path(file_path) if file_path else None
    if path is None or not path.exists():
        return ("skipped_missing", 0)

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
            try:
                store.delete_file(file_id)
            except Exception as e:  # Intentional broad catch: cleanup is best-effort
                logger.debug(f"delete_file failed for {file_id}: {e}")
            db_conn.execute(
                "UPDATE files SET vectorized_at = CURRENT_TIMESTAMP,"
                " vectorized_chunks = 0 WHERE id = ?",
                (file_id,),
            )
            return ("skipped_large", file_size)

    chunks = extractor.extract_with_chunking(path)
    if not chunks:
        return ("skipped_missing", 0)

    metadata = {"file_type": path.suffix.lower(), "file_name": path.name}
    if use_upsert:
        store.upsert_file(file_id, str(path), chunks, metadata)
    else:
        store.index_file(file_id, str(path), chunks, metadata)
    db_conn.execute(
        "UPDATE files SET vectorized_at = CURRENT_TIMESTAMP,"
        " vectorized_chunks = ? WHERE id = ?",
        (len(chunks), file_id),
    )
    return ("new", len(chunks))


def _handle_shutdown(signum: int, frame: Any) -> None:
    global _shutdown
    _shutdown = True
    import footprinter.ingest.vector_ops as _vo
    _vo._shutdown = True
    logger.warning("Received %s — finishing current item...", signal.Signals(signum).name)


def run_vectorization(
    db: "Database",
    full_mode: bool = False,
    on_progress: Optional[Callable[[int], None]] = None,
    file_ids: Optional[List[int]] = None,
) -> PipeResult:
    """Embed files, messages, and chat info that haven't been vectorized yet.

    Split off from inline ingest so the index is usable before embedding
    completes. Handles all vectorization types: files (via local extraction),
    messages and chat_info (via shared helpers in vector_ops).

    Args:
        db: Database instance.
        full_mode: When True, re-embed every listed file
            by dropping the ``vectorized_at IS NULL`` clause.
        on_progress: Optional callback fired with cumulative count
            after each item is processed.
        file_ids: When provided, scope file vectorization to only these IDs.
            Empty list means no-op for files. None means broad (existing behavior).
            Message/chat_info phases always select their own unvectorized rows.

    Returns:
        PipeResult — ``skipped`` when all vectorization is disabled,
        otherwise ``completed`` (or ``completed_with_errors``) with
        per-type counts in data.
    """
    from footprinter.semantic.vector_store import (
        VectorStore,
        _chat_vectorization_enabled,
        _file_vectorization_enabled,
    )

    files_enabled = _file_vectorization_enabled()
    chats_enabled = _chat_vectorization_enabled()

    if not files_enabled and not chats_enabled:
        return PipeResult.skipped("vectorization", "vectorization disabled")

    counts: Dict[str, Any] = {
        "vectorized_new": 0,
        "vectorized_failed": 0,
        "vectorized_skipped_missing": 0,
        "vectorized_skipped_large": 0,
        "vectorized_messages_new": 0,
        "vectorized_chat_info_new": 0,
    }
    skipped_large_files: List[Dict[str, Any]] = []

    if file_ids is not None and len(file_ids) == 0 and not chats_enabled:
        return PipeResult.completed("vectorization", skipped_large_files=skipped_large_files, **counts)

    try:
        store = VectorStore.get_instance()
    except Exception as e:  # Intentional broad catch: vector store init failure must not crash the stage
        logger.warning("Vectorization stage: vector store unavailable: %s", e)
        return PipeResult.make_error("vectorization", f"vector store unavailable: {e}", ErrorType.RUNTIME)

    global _shutdown
    _shutdown = False
    import footprinter.ingest.vector_ops as _vo
    _vo._shutdown = False
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    processed = 0
    failures: List[str] = []
    interrupted = False
    try:
        # --- File vectorization phase ---
        if files_enabled:
            if file_ids is not None and len(file_ids) == 0:
                pass  # no-op for empty file_ids
            else:
                statuses = _get_vectorize_statuses()
                status_ph = ",".join("?" * len(statuses))

                if file_ids is not None:
                    where = f"status IN ({status_ph}) AND vectorize = 1"
                    if not full_mode:
                        where += " AND vectorized_at IS NULL"
                    if len(file_ids) <= 500:
                        placeholders = ",".join("?" * len(file_ids))
                        where += f" AND id IN ({placeholders})"
                        rows = db.conn.execute(
                            f"SELECT id, path FROM files WHERE {where}", statuses + file_ids
                        ).fetchall()
                    else:
                        db.conn.execute(
                            "CREATE TEMP TABLE IF NOT EXISTS _vec_scope (file_id INTEGER PRIMARY KEY)"
                        )
                        db.conn.execute("DELETE FROM _vec_scope")
                        db.conn.executemany(
                            "INSERT INTO _vec_scope (file_id) VALUES (?)",
                            [(fid,) for fid in file_ids],
                        )
                        where += " AND id IN (SELECT file_id FROM _vec_scope)"
                        rows = db.conn.execute(
                            f"SELECT id, path FROM files WHERE {where}", statuses
                        ).fetchall()
                else:
                    where = f"status IN ({status_ph}) AND vectorize = 1"
                    if not full_mode:
                        where += " AND vectorized_at IS NULL"
                    rows = db.conn.execute(
                        f"SELECT id, path FROM files WHERE {where}", statuses
                    ).fetchall()

                from footprinter.ingest.full_content_extractor import FullContentExtractor
                from footprinter.source_registry import get_config

                extractor = FullContentExtractor.from_config(get_config())
                vectorize_cap = getattr(extractor, "max_vectorize_size_bytes", 0)

                for row in rows:
                    if _shutdown:
                        db.conn.commit()
                        interrupted = True
                        break

                    file_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
                    file_path = row["path"] if isinstance(row, sqlite3.Row) else row[1]
                    processed += 1
                    try:
                        outcome, value = _embed_one_file(
                            store,
                            extractor,
                            db.conn,
                            file_id,
                            file_path,
                            vectorize_cap=vectorize_cap,
                            use_upsert=True,
                        )
                        if outcome == "new":
                            counts["vectorized_new"] += 1
                        elif outcome == "skipped_missing":
                            counts["vectorized_skipped_missing"] += 1
                        elif outcome == "skipped_large":
                            counts["vectorized_skipped_large"] += 1
                            skipped_large_files.append({"path": file_path, "size_bytes": value})
                    except Exception as e:  # Intentional broad catch: per-row failure must not abort
                        counts["vectorized_failed"] += 1
                        failures.append(f"id={file_id}: {e}")
                        logger.debug(
                            "Vectorization failed for file_id=%s path=%s: %s", file_id, file_path, e
                        )
                    finally:
                        if on_progress is not None:
                            on_progress(processed)
                        if processed % _COMMIT_INTERVAL == 0:
                            db.conn.commit()

                if not interrupted:
                    db.conn.commit()
                if _shutdown and not interrupted:
                    interrupted = True

        # --- Message + chat_info vectorization phases ---
        if not _shutdown and chats_enabled:
            msg_result = _vo._vectorize_messages(
                db.conn, db.conn.cursor(), store, console=None, mode="incremental"
            )
            counts["vectorized_messages_new"] = msg_result.get("done", 0)
            if msg_result.get("interrupted"):
                interrupted = True

        if not _shutdown and chats_enabled:
            chat_result = _vo._vectorize_chat_info(
                db.conn, db.conn.cursor(), store, console=None, mode="incremental"
            )
            counts["vectorized_chat_info_new"] = chat_result.get("done", 0)
            if chat_result.get("interrupted"):
                interrupted = True
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        _shutdown = False
        _vo._shutdown = False

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
