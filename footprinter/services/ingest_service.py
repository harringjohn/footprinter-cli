"""IngestService — single authority on ingest tracking and FTS optimization.

All ingest operations (pipeline and non-pipeline) flow through this service.
Wraps PipeRunner for pipeline ingests; called directly for non-pipeline ingests.
Manages FTS trigger lifecycle around batch runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable, List, Optional

from footprinter.utils.time import utc_now_iso

if TYPE_CHECKING:
    from footprinter.ingest.database import Database

log = logging.getLogger(__name__)


class IngestService:
    """Track ingest lifecycle: begin, complete, fail, query history.

    Optionally manages FTS trigger optimization around batch runs
    when constructed with a ``get_db`` callable.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        get_db: Optional[Callable[[], Database]] = None,
    ) -> None:
        self.conn = conn
        self._get_db = get_db

    def begin(
        self,
        pipe: str,
        mode: str | None = None,
        trigger: str | None = None,
    ) -> int:
        """Insert a running ingest record and return its id."""
        cursor = self.conn.execute(
            "INSERT INTO ingests (pipe, started_at, status, mode, trigger) VALUES (?, ?, 'running', ?, ?)",
            (pipe, utc_now_iso(), mode, trigger),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def complete(
        self,
        ingest_id: int,
        result: dict | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Mark an ingest as completed with optional result counts and metadata."""
        result = result or {}
        meta_json = json.dumps(metadata) if metadata is not None else None
        self.conn.execute(
            "UPDATE ingests SET"
            " status = 'completed',"
            " completed_at = ?,"
            " items_processed = ?,"
            " items_new = ?,"
            " items_updated = ?,"
            " items_skipped = ?,"
            " errors = ?,"
            " elapsed_seconds = ?,"
            " metadata = ?"
            " WHERE id = ?",
            (
                utc_now_iso(),
                result.get("items_processed", 0),
                result.get("items_new", 0),
                result.get("items_updated", 0),
                result.get("items_skipped", 0),
                result.get("errors", 0),
                result.get("elapsed_seconds"),
                meta_json,
                ingest_id,
            ),
        )
        self.conn.commit()

    def fail(self, ingest_id: int, error: str) -> None:
        """Mark an ingest as failed with an error message."""
        self.conn.execute(
            "UPDATE ingests SET status = 'failed', completed_at = ?, metadata = ? WHERE id = ?",
            (
                utc_now_iso(),
                json.dumps({"error": error}),
                ingest_id,
            ),
        )
        self.conn.commit()

    def last_run(self, pipe: str) -> datetime | None:
        """Return the completed_at timestamp of the most recent successful ingest."""
        row = self.conn.execute(
            "SELECT completed_at FROM ingests"
            " WHERE pipe = ? AND status = 'completed'"
            " ORDER BY completed_at DESC LIMIT 1",
            (pipe,),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["completed_at"])

    def run_pipe(
        self,
        pipe: str,
        *,
        mode: str | None = None,
        trigger: str | None = None,
        runner,
        on_progress=None,
    ) -> dict:
        """Wrap a PipeRunner.run_pipe call with ingest tracking.

        Creates an ingests record, passes last_run to runner.run_pipe(),
        then records completion or failure.
        """
        ingest_id = self.begin(pipe, mode=mode, trigger=trigger)
        try:
            result = runner.run_pipe(pipe, on_progress=on_progress, last_run=self.last_run(pipe))
            if result.get("status") == "error":
                self.fail(ingest_id, error=result.get("error", "unknown"))
            else:
                self.complete(ingest_id, result=result)
            return result
        except Exception as e:
            self.fail(ingest_id, error=str(e))
            raise

    def history(self, pipe: str, limit: int = 20) -> list[dict]:
        """Return recent ingest records for a pipe, most recent first."""
        rows = self.conn.execute(
            "SELECT * FROM ingests WHERE pipe = ? ORDER BY started_at DESC LIMIT ?",
            (pipe, limit),
        ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            if record.get("metadata") is not None:
                record["metadata"] = json.loads(record["metadata"])
            records.append(record)
        return records

    # ── FTS optimization ────────────────────────────────────────────

    def ensure_fts_health(self, full_mode: bool) -> None:
        """Check FTS health and restore missing triggers.

        Always probes FTS health (both modes). In incremental mode, also
        restores missing triggers from a prior crash (SIGKILL/OOM during
        a full-mode run) before the health probe. Trigger restore is
        skipped in full mode because triggers are dropped anyway.

        No-op if constructed without ``get_db``.
        """
        if self._get_db is None:
            return
        try:
            db = self._get_db()
            if db is None:
                return
            if not full_mode:
                missing_triggers = db.check_fts_triggers()
                if missing_triggers:
                    log.info(
                        "Restoring %d missing FTS triggers from prior crash",
                        len(missing_triggers),
                    )
                    db.create_fts_triggers()
            fts_health = db.check_fts_health()
            for table, info in fts_health.items():
                if info["status"] == "error":
                    log.warning(
                        "FTS index corrupted (%s) — run 'fp ingest --repair-fts' to fix",
                        table,
                    )
        except sqlite3.OperationalError as e:
            log.debug("FTS health probe skipped: %s", e)

    def run_pipes(
        self,
        pipes: List[str],
        *,
        runner,
        full_mode: bool = False,
        mode: str | None = None,
        trigger: str | None = None,
        on_pipe_start: Optional[Callable] = None,
        on_pipe_end: Optional[Callable] = None,
        on_progress: Optional[Callable] = None,
        pipe_hook: Optional[Callable] = None,
    ) -> List[dict]:
        """Run multiple pipes with FTS optimization around the batch.

        In full mode, drops FTS triggers before the first pipe and rebuilds
        FTS indexes after the last pipe (or on error) to avoid per-row
        trigger overhead during bulk ingest. FTS optimization requires
        ``get_db`` — silently skipped if constructed without it.

        When ``runner.run_pipes`` returns without raising, writes a single
        aggregate ingests row with ``pipe='all'`` summing items_processed,
        items_new, items_updated, items_skipped, and errors across the
        per-pipe results. ``status`` is ``'failed'`` if any per-pipe result
        has ``status == 'error'`` (including the fatal-error short-circuit
        case in ``PipeRunner``), else ``'completed'``. ``elapsed_seconds``
        is wall-clock around ``runner.run_pipes``. The aggregate write is
        best-effort: a failure logs a warning and is swallowed so
        successful pipe results still reach the caller.
        """
        self.ensure_fts_health(full_mode)

        fts_dropped = False
        if full_mode and self._get_db is not None:
            try:
                db = self._get_db()
                db.drop_fts_triggers()
                fts_dropped = True
            except sqlite3.OperationalError as e:
                log.warning("Failed to drop FTS triggers: %s", e)

        started_iso = utc_now_iso()
        t0 = time.monotonic()
        try:
            results = runner.run_pipes(
                pipes,
                on_pipe_start=on_pipe_start,
                on_pipe_end=on_pipe_end,
                on_progress=on_progress,
                pipe_hook=pipe_hook,
            )
            if pipes:
                try:
                    self._write_aggregate_row(
                        results, mode, trigger, started_iso, time.monotonic() - t0
                    )
                except sqlite3.Error as e:
                    log.warning("Failed to write aggregate ingest row: %s", e)
            return results
        finally:
            if fts_dropped:
                try:
                    db = self._get_db()
                    db.rebuild_fts_indexes()
                except sqlite3.OperationalError as e:
                    log.error("Failed to rebuild FTS indexes: %s", e)

    def _write_aggregate_row(
        self,
        results: list[dict],
        mode: str | None,
        trigger: str | None,
        started_iso: str,
        elapsed_wall: float,
    ) -> None:
        """Insert a single ingests row (pipe='all') summing per-pipe results."""
        any_errored = any(r.get("status") == "error" for r in results)
        status = "failed" if any_errored else "completed"
        metadata = json.dumps({"pipes": [r.get("stage") for r in results]})
        self.conn.execute(
            "INSERT INTO ingests ("
            "pipe, started_at, completed_at, status, mode, trigger,"
            " items_processed, items_new, items_updated, items_skipped,"
            " errors, elapsed_seconds, metadata"
            ") VALUES ('all', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                started_iso,
                utc_now_iso(),
                status,
                mode,
                trigger,
                sum(r.get("items_processed", 0) or 0 for r in results),
                sum(r.get("items_new", 0) or 0 for r in results),
                sum(r.get("items_updated", 0) or 0 for r in results),
                sum(r.get("items_skipped", 0) or 0 for r in results),
                sum(r.get("errors", 0) or 0 for r in results),
                round(elapsed_wall, 1),
                metadata,
            ),
        )
        self.conn.commit()
