"""Connector-scope schema extensions.

Connectors declare extra columns via ConnectorSpec.schema_extensions.
This module applies those declarations using idempotent ALTER TABLE,
mirroring the pattern in app_schema.py.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def init_connector_schemas(conn: sqlite3.Connection, connector_specs: list) -> None:
    """Apply schema extensions for the given connector specs.

    The caller is responsible for filtering to installed connectors.
    For each spec with schema_extensions, calls register_connector_schema()
    to add columns via ALTER TABLE.
    """
    for spec in connector_specs:
        if spec.schema_extensions:
            register_connector_schema(conn, spec.schema_extensions)


def register_connector_schema(
    conn: sqlite3.Connection,
    extensions: dict[str, list[tuple[str, str]]],
) -> None:
    """Add connector-declared columns to existing tables.

    Args:
        conn: An open sqlite3 connection with base schema already initialized.
        extensions: Mapping of table_name → [(col_name, col_definition), ...].
            Example: {"folders": [("web_link", "TEXT")]}
    """
    cursor = conn.cursor()
    for table, columns in extensions.items():
        for col_name, col_def in columns:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # column already exists
                else:
                    raise
    conn.commit()
