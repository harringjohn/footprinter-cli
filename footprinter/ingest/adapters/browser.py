"""Browser adapter.

Wraps BrowserManager to conform to PipeAdapter protocol.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any, Dict, List

from footprinter.db import browser as browser_db
from footprinter.ingest.adapters.ingest import ingest_entries
from footprinter.ingest.adapters.protocol import ErrorType, PipeContext, PipeResult
from footprinter.ingest.browser_indexer import BrowserManager

logger = logging.getLogger(__name__)


class BrowserAdapter:
    """Adapter wrapping BrowserManager for the browser stage."""

    name = "browser"
    pipe_name = "browser"
    required_extras: List[str] = []

    def run(self, db: Any, ctx: PipeContext) -> PipeResult:
        """Index browser history into visits table."""
        try:
            last_run = None if ctx.full_mode else ctx.last_run
            manager = BrowserManager(ctx.source_config, since=last_run)
            result = ingest_entries(
                "browser",
                manager.parse_all(),
                partial(browser_db.insert_visit, db.conn),
                count_label="urls_indexed",
                conn=db.conn,
                on_progress=ctx.on_progress,
            )
            return result
        except Exception as e:
            logger.error(f"browser stage failed: {e}")
            return PipeResult.make_error(
                "browser",
                error=str(e),
                error_type=ErrorType.RUNTIME,
            )

    def status(self, db: Any) -> Dict[str, Any]:
        """Return browser visit entry count."""
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM visits")
        count = cursor.fetchone()[0]
        return {"visits": count}
