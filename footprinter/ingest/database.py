"""SQLite database schema and operations for Footprinter."""

import logging
from pathlib import Path

from footprinter.db_base import get_connection
from footprinter.ingest.db import ddl, fts
from footprinter.ingest.db.connector_schema import init_connector_schemas
from footprinter.paths import get_db_path

logger = logging.getLogger(__name__)


class Database:
    """SQLite database connection and schema manager for Footprinter.

    Owns the connection and delegates schema/FTS work to the free functions
    in ``ddl`` and ``fts``.
    """

    _FTS_TRIGGER_NAMES = fts._FTS_TRIGGER_NAMES

    def __init__(self, db_path: str = None, connector_specs: list = None):
        if db_path is None:
            db_path = str(get_db_path())
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = get_connection(self.db_path)
        # WAL is file-level persistent, set once at creation — not in the per-connection factory.
        self.conn.execute("PRAGMA journal_mode=WAL")
        ddl.init_schema(self.conn)
        init_connector_schemas(self.conn, connector_specs or [])

    def init_db(self):
        """Re-run schema initialization on the owned connection (idempotent)."""
        ddl.init_schema(self.conn)

    def check_fts_triggers(self) -> list[str]:
        """Return names of expected FTS triggers missing from the database."""
        return fts.check_fts_triggers(self.conn)

    def create_fts_triggers(self) -> None:
        """Create all FTS sync triggers."""
        return fts.create_fts_triggers(self.conn)

    def drop_fts_triggers(self) -> None:
        """Drop all FTS sync triggers."""
        return fts.drop_fts_triggers(self.conn)

    def rebuild_fts_indexes(self) -> None:
        """Rebuild all FTS indexes from base tables and restore triggers."""
        return fts.rebuild_fts_indexes(self.conn)

    def check_fts_health(self) -> dict:
        """Check FTS table health: existence and queryability."""
        return fts.check_fts_health(self.conn)

    def repair_fts(self) -> dict:
        """Drop and rebuild all FTS tables from base table data."""
        return fts.repair_fts(self.conn)

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        """Enter context manager, returning self."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager, closing the database connection."""
        self.close()
