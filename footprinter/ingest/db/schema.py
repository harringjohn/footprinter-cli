"""Database schema initialization."""

from footprinter.ingest.db.ddl import (
    _INGESTS_DDL,
    ACCESS_CONTROL_TABLES,
)
from footprinter.ingest.db.fts import _FTS_DEFINITIONS

__all__ = [
    "ACCESS_CONTROL_TABLES",
    "_FTS_DEFINITIONS",
    "_INGESTS_DDL",
]
