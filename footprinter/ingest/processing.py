"""
Processing module — access resolution and pipeline framework.

Primary role: ``run_access_resolution`` stamps visibility and permissions
on ingested entities, with last-run-based incremental processing.
Also provides the ``ProcessingPipeline`` framework for phase registration
and dispatch.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

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
    from footprinter.access import ENTITY_META, recalculate_access, stamp_entities

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
                    where += " AND status != 'removed'"

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
