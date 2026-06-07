"""Database schema initialization."""

from footprinter.ingest.db.ddl import (
    _INGESTS_DDL,
    ACCESS_CONTROL_TABLES,
    DDLMixin,
)
from footprinter.ingest.db.fts import _FTS_DEFINITIONS, FTSMixin


class SchemaMixin(DDLMixin, FTSMixin):
    """Mixin providing database schema initialization."""


__all__ = [
    "SchemaMixin",
    "ACCESS_CONTROL_TABLES",
    "_FTS_DEFINITIONS",
    "_INGESTS_DDL",
]
