"""Database schema initialization."""

from footprinter.ingest.db.ddl import (
    ACCESS_CONTROL_TABLES,
    DDLMixin,
    _INGESTS_DDL,
)
from footprinter.ingest.db.fts import FTSMixin, _FTS_DEFINITIONS


class SchemaMixin(DDLMixin, FTSMixin):
    """Mixin providing database schema initialization."""


__all__ = [
    "SchemaMixin",
    "ACCESS_CONTROL_TABLES",
    "_FTS_DEFINITIONS",
    "_INGESTS_DDL",
]
