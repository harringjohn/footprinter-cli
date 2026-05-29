"""Shared CLI utilities used across all CLI subcommands.

Provides database connection, argument helpers, identifier resolution,
JSON output, and shared constants.
"""

import argparse
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union

from rich.console import Console

from footprinter.services import access_service as _access
from footprinter.services.access_service import (
    resolve_inherit_permission,
    resolve_inherit_visibility,
)

# ---------------------------------------------------------------------------
# Shared instances and constants
# ---------------------------------------------------------------------------

console = Console()

# Formatter for parsers that use description= or epilog= with pre-formatted text.
# Custom subclass replaces the dense argparse usage line with a clean header.


class FootprinterHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        # argparse calls this with prefix="" when computing subparser prog
        # prefixes; returning the Usage-wrapped string there would compound
        # "Usage: Usage: fp …" at every nesting level.
        if prefix == "":
            return self._prog
        return f"\nUsage: {self._prog}\n\n"


FORMATTER = FootprinterHelpFormatter

# Color vocabulary — consistent markup across CLI subcommands
C_SUCCESS = "green"
C_WARNING = "yellow"
C_ERROR = "red"
C_INFO = "cyan"
C_DIM = "dim"

VALID_STATUSES = frozenset({"listed", "unlisted", "removed"})

ALLOWED_TABLES = frozenset({"clients", "projects"})
ALLOWED_COLUMNS = frozenset({"name"})


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------


def connect_db(db_path: Union[str, Path]) -> Optional[sqlite3.Connection]:
    """Open a read/write connection to the Footprinter database.

    Returns None if the database file does not exist. Sets row_factory
    and busy_timeout so callers don't need to repeat boilerplate.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def open_db(db_path=None):
    """Open the Footprinter DB; yields conn, closes on exit.

    Calls :func:`~footprinter.services.access_service.load_globals` to
    populate the global visibility/permission cache, mirroring the MCP
    layer's ``get_db()``.

    Exits with code 1 if the database file does not exist.
    """
    if db_path is None:
        from footprinter.paths import get_db_path

        db_path = get_db_path()
    conn = connect_db(db_path)
    if conn is None:
        console.print(
            "[red]Database not found.[/red] Run [bold]fp setup[/bold] then [bold]fp ingest[/bold] to initialize."
        )
        sys.exit(1)
    _access.load_globals(conn)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def open_database(db_path=None):
    """Open the Footprinter DB; yields a Database instance, closes on exit.

    Like ``open_db`` but yields the full ``Database`` wrapper instead of a
    raw ``sqlite3.Connection``.  Use this when callers need methods only
    available on the wrapper.

    Exits with code 1 if the database file does not exist.
    """
    if db_path is None:
        from footprinter.paths import get_db_path

        db_path = get_db_path()
    db_path = Path(db_path)
    if not db_path.exists():
        console.print(
            "[red]Database not found.[/red] Run [bold]fp setup[/bold] then [bold]fp ingest[/bold] to initialize."
        )
        sys.exit(1)
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def add_json_flag(parser) -> None:
    """Add a ``--json`` flag to an argparse parser."""
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )


def add_csv_flag(parser) -> None:
    """Add a ``--csv`` flag to an argparse parser."""
    parser.add_argument(
        "--csv",
        action="store_true",
        default=False,
        help="Output as CSV",
    )


def add_template_flag(parser) -> None:
    """Add a ``--template`` flag to an argparse parser."""
    parser.add_argument(
        "--template",
        action="store_true",
        default=False,
        help="Output an import-compatible CSV template with example data",
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def output_json(data) -> None:
    """Pretty-print *data* as JSON to stdout.

    Uses ``default=str`` so datetime objects serialize without error.
    """
    print(json.dumps(data, indent=2, default=str))


def output_csv(rows: list[dict], columns: list[str] | None = None) -> None:
    """Write *rows* as CSV to stdout.

    If *columns* is given, output only those columns in that order.
    Otherwise, use all keys from the first row.
    """
    import csv

    if not rows:
        return
    if columns is None:
        columns = list(rows[0].keys())
    writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: str(v) if v is not None else "" for k, v in row.items()})


# ---------------------------------------------------------------------------
# Identifier resolution
# ---------------------------------------------------------------------------


def resolve_identifier(
    conn: sqlite3.Connection,
    table: str,
    name_col: str,
    identifier: str,
) -> int:
    """Resolve a user-supplied identifier to a row ID.

    Tries numeric ID first, then falls back to case-insensitive name match.

    Returns the integer row ID on success.

    Raises ``ValueError`` when:
    - No matching row is found (by ID or name)
    - Multiple rows match the name (includes the full match list)
    """
    if table not in ALLOWED_TABLES or name_col not in ALLOWED_COLUMNS:
        raise ValueError(f"Invalid table/column: {table}.{name_col}")

    # Try numeric ID first
    try:
        row_id = int(identifier)
        cursor = conn.execute(
            f"SELECT id FROM {table} WHERE id = ?",
            (row_id,),
        )
        if cursor.fetchone():
            return row_id
    except ValueError:
        pass

    # Fall back to case-insensitive name match
    cursor = conn.execute(
        f"SELECT id, {name_col} FROM {table} WHERE {name_col} COLLATE NOCASE = ?",
        (identifier,),
    )
    rows = cursor.fetchall()

    if len(rows) == 0:
        raise ValueError(f"No {table} found matching '{identifier}'")

    if len(rows) == 1:
        return rows[0]["id"]

    # Ambiguous — list all matches
    match_list = ", ".join(f"id={r['id']} name={r[name_col]!r}" for r in rows)
    raise ValueError(f"Ambiguous: {len(rows)} {table} match '{identifier}': {match_list}")


# ---------------------------------------------------------------------------
# Pure utilities
# ---------------------------------------------------------------------------


def add_verbose_flag(parser) -> None:
    """Add a ``--verbose`` flag to an argparse parser."""
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Show access and visibility columns",
    )


def enrich_verbose_access(
    rows: list[dict],
    entity_type: str,
    *,
    id_key: str = "id",
) -> None:
    """Annotate rows in-place with resolved access fields.

    Replaces raw ``mcp_*`` keys with a six-field access block appended
    in order: ``mcp_view``, ``mcp_read``, ``visibility``, ``access``,
    ``access_source``, ``visibility_source``.  Internal provenance columns
    (``mcp_view_source``, ``mcp_read_source``) are consumed then removed.

    No-op if *rows* is empty.
    """
    if not rows:
        return
    for r in rows:
        mcp_view = r.pop("mcp_view", None)
        mcp_read_present = "mcp_read" in r
        mcp_read = r.pop("mcp_read", None)
        read_source = r.pop("mcp_read_source", None)
        view_source = r.pop("mcp_view_source", None)

        if not mcp_read_present:
            access = "—"
            access_source = "—"
        elif mcp_read not in (None, "inherit"):
            access = "allow" if mcp_read == "allow" else "deny"
            access_source = read_source if read_source else "cached"
        else:
            access = resolve_inherit_permission(mcp_read)
            if mcp_read == "inherit":
                access_source = "global" if _access.is_global_policy_loaded() else "baseline"
            else:
                access_source = "default"

        if mcp_view not in (None, "inherit"):
            visibility_source = view_source if view_source else "cached"
        elif mcp_view == "inherit":
            visibility_source = "global" if _access.is_global_policy_loaded() else "baseline"
        else:
            visibility_source = "default"

        r["mcp_view"] = mcp_view
        r["mcp_read"] = mcp_read
        r["visibility"] = resolve_inherit_visibility(mcp_view)
        r["access"] = access
        r["access_source"] = access_source
        r["visibility_source"] = visibility_source


def verbose_access_cells(row: dict) -> list[str]:
    """Return [mcp_view, mcp_read, visibility, access, source, vis_source] with Rich color markup."""
    vis_colors = {"visible": "green", "opaque": "yellow", "hidden": "red"}

    mcp_view = row.get("mcp_view")
    if mcp_view is None:
        mcp_view_cell = "[dim]—[/dim]"
    elif mcp_view == "inherit":
        mcp_view_cell = "[dim]inherit[/dim]"
    else:
        vc = vis_colors.get(mcp_view, "white")
        mcp_view_cell = f"[{vc}]{mcp_view}[/{vc}]"

    mcp_read = row.get("mcp_read")
    if mcp_read is None:
        mcp_read_cell = "[dim]—[/dim]"
    elif mcp_read == "inherit":
        mcp_read_cell = "[dim]inherit[/dim]"
    elif mcp_read == "allow":
        mcp_read_cell = "[green]allow[/green]"
    else:
        mcp_read_cell = "[red]deny[/red]"

    visibility = row.get("visibility", "opaque")
    vis_color = vis_colors.get(visibility, "white")
    vis_cell = f"[{vis_color}]{visibility}[/{vis_color}]"

    access = row.get("access", "deny")
    if access == "—":
        access_cell = "[dim]—[/dim]"
    elif access == "allow":
        access_cell = "[green]allow[/green]"
    else:
        access_cell = "[red]deny[/red]"

    source = row.get("access_source")
    if source is None or source == "—":
        source_cell = "[dim]—[/dim]"
    else:
        source_cell = source

    vis_source = row.get("visibility_source")
    if vis_source is None or vis_source == "—":
        vis_source_cell = "[dim]—[/dim]"
    elif vis_source == source:
        vis_source_cell = "[dim]≡[/dim]"
    else:
        vis_source_cell = vis_source

    return [mcp_view_cell, mcp_read_cell, vis_cell, access_cell, source_cell, vis_source_cell]


def format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string (B / KB / MB / GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
