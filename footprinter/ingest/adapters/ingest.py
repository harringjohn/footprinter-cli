"""Shared ingest loop helper for pipeline adapters.

Extracts the common iterate-try-count-log pattern used by Browser, Email,
DriveFiles, and DriveFolders adapters into a single function.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from footprinter.ingest.adapters.protocol import PipeResult

logger = logging.getLogger(__name__)


def ingest_entries(
    stage: str,
    entries: Iterable,
    insert_fn: Callable[[Any], Any],
    *,
    count_label: str = "items_indexed",
    max_logged_errors: int = 5,
    progress_interval: int | None = None,
    conn: Any | None = None,
    batch_size: int = 1000,
    on_progress: Callable[[int], None] | None = None,
) -> PipeResult:
    """Iterate *entries*, calling *insert_fn* per entry with error resilience.

    Returns a PipeResult with:
    - ``count_label``: number of successful inserts
    - ``skipped``: number of entries the insert_fn chose not to process
    - ``errors``: number of failed inserts
    - Status ``completed`` or ``completed_with_errors``

    **Skip contract:** if *insert_fn* returns ``False`` (identity check, not
    truthiness), the entry is counted as *skipped* rather than a success.
    Any other return value (``None``, ``True``, etc.) counts as a success.
    This lets adapters signal "I intentionally didn't process this" without
    post-correcting counts.

    **Batch commits:** when *conn* is provided, ``conn.commit()`` is called
    every *batch_size* successful inserts and once after the loop for any
    remainder.  On insert error, pending successes are committed before
    continuing.  When *conn* is ``None``, no commits are issued.

    **Commit failures:** if ``conn.commit()`` itself raises, the error is
    caught and logged (warning for mid-loop commits, error for the final
    commit).  Processing continues — uncommitted rows stay in the open
    transaction and are flushed by the next successful commit or by a
    retry commit after the loop.  The ``count_label`` value counts entries
    where *insert_fn* succeeded, not entries durably committed; when
    ``commit_errors`` is present in the result data, some inserts may not
    have been persisted.

    Note: 100% failure still returns ``completed_with_errors`` (not ``error``).
    ``error`` is reserved for stage-level failures (database, config, etc.).
    ``completed_with_errors`` means the loop completed — individual entries failed.

    Errors are logged up to *max_logged_errors* to avoid flooding.
    If *progress_interval* is set, logs a progress message every N successes.
    """
    success_count = 0
    skip_count = 0
    error_count = 0
    commit_error_count = 0
    batch_count = 0
    processed_count = 0

    for entry in entries:
        try:
            result = insert_fn(entry)
            if result is False:
                skip_count += 1
            else:
                success_count += 1
                batch_count += 1
                if conn is not None and batch_count >= batch_size:
                    try:
                        conn.commit()
                    except Exception as exc:
                        commit_error_count += 1
                        logger.warning(
                            "%s: batch commit failed (%d pending): %s",
                            stage,
                            batch_count,
                            exc,
                        )
                    batch_count = 0
                if progress_interval and success_count % progress_interval == 0:
                    logger.info(f"Indexed {success_count} {count_label}...")
        except Exception as e:
            error_count += 1
            if conn is not None and batch_count > 0:
                try:
                    conn.commit()
                except Exception as exc:
                    commit_error_count += 1
                    logger.warning(
                        "%s: error-recovery commit failed (%d pending): %s",
                        stage,
                        batch_count,
                        exc,
                    )
                batch_count = 0
            if error_count <= max_logged_errors:
                logger.error(f"Error in {stage} ingest: {e}")
        finally:
            processed_count += 1
            if on_progress is not None:
                on_progress(processed_count)

    if conn is not None and (batch_count > 0 or commit_error_count > 0):
        try:
            conn.commit()
        except Exception as exc:
            commit_error_count += 1
            logger.error(
                "%s: final commit failed (%d pending): %s",
                stage,
                batch_count,
                exc,
            )

    suppressed = error_count - max_logged_errors
    if suppressed > 0:
        logger.warning(f"{stage}: {suppressed} more errors not shown")

    data = {count_label: success_count, "skipped": skip_count, "errors": error_count}
    if commit_error_count > 0:
        data["commit_errors"] = commit_error_count

    if error_count > 0 or commit_error_count > 0:
        error_parts = []
        if error_count > 0:
            error_parts.append(f"{error_count} entries failed")
        if commit_error_count > 0:
            error_parts.append(f"{commit_error_count} commit errors")
        return PipeResult.completed_with_errors(
            stage,
            error=", ".join(error_parts),
            **data,
        )

    return PipeResult.completed(stage, **data)
