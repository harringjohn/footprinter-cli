"""SQLite database schema and operations for Footprinter."""

import logging
from pathlib import Path

from footprinter.ingest.db.connector_schema import init_connector_schemas
from footprinter.ingest.db.schema import SchemaMixin
from footprinter.paths import get_db_path

logger = logging.getLogger(__name__)


class Database(SchemaMixin):
    """SQLite database connection and schema manager for Footprinter."""

    def __init__(self, db_path: str = None, connector_specs: list = None):
        if db_path is None:
            db_path = str(get_db_path())
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = None
        self.init_db()
        init_connector_schemas(self.conn, connector_specs or [])

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
