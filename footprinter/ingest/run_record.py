"""Pure persistence for pipeline run records.

Saves and loads a JSON record of each pipeline run. No heuristics or
config awareness — warning logic lives in the display layer.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from footprinter.paths import get_last_run_path

SESSION_WINDOW_MINUTES = 10


def save_run_record(
    results: List[Dict],
    mode: str,
    started_at: datetime,
    *,
    interrupted: bool = False,
    path: Optional[Path] = None,
) -> Path:
    """Write a run record to JSON, merging with recent records.

    If an existing record started within SESSION_WINDOW_MINUTES of
    ``started_at``, new stages are appended to it (preserving the
    original ``started_at``).  Otherwise the record is replaced.

    Args:
        results: List of per-stage result dicts from PipeRunner.
        mode: Run mode string (e.g. "incremental", "full").
        started_at: When the pipeline started.
        interrupted: Whether the run was interrupted (e.g. KeyboardInterrupt).
        path: Override output path (default: get_last_run_path()).

    Returns:
        The path the record was written to.
    """
    if path is None:
        path = get_last_run_path()

    completed_at = datetime.now(timezone.utc)
    total_elapsed = sum(r.get("elapsed_seconds", 0) for r in results)

    # Merge with existing record if within session window
    existing = load_run_record(path=path)
    if existing and _within_session_window(existing, started_at):
        existing["stages"].extend(results)
        existing["completed_at"] = completed_at.isoformat()
        existing["total_elapsed_seconds"] = sum(r.get("elapsed_seconds", 0) for r in existing["stages"])
        existing["interrupted"] = interrupted
        record = existing
    else:
        record = {
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "mode": mode,
            "interrupted": interrupted,
            "total_elapsed_seconds": total_elapsed,
            "stages": results,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, default=str))
    return path


def _within_session_window(existing: Dict, new_started_at: datetime) -> bool:
    """Check if an existing record is within the merge window."""
    try:
        existing_start = datetime.fromisoformat(existing["started_at"])
        return abs(new_started_at - existing_start) <= timedelta(minutes=SESSION_WINDOW_MINUTES)
    except (KeyError, ValueError):
        return False


def load_run_record(path: Optional[Path] = None) -> Optional[Dict]:
    """Read a run record from JSON.

    Returns:
        The parsed record dict, or None if the file doesn't exist.
    """
    if path is None:
        path = get_last_run_path()

    if not path.exists():
        return None

    return json.loads(path.read_text())
