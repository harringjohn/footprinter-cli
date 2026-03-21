"""fp vectorize — manage per-record vectorization control flags.

Commands:
    fp vectorize exclude <entity> <id> [<id>...]   Mark records to skip vectorization
    fp vectorize include <entity> <id> [<id>...]   Restore records for vectorization
    fp vectorize review [<entity>]                 Show excluded record counts
    fp vectorize import <path>                     Apply flags from a JSON file
"""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Optional, Union

from rich.console import Console

from footprinter.cli._common import FORMATTER, console, open_db

# Entity types that support the vectorize flag
ENTITY_TABLES = {"files": "files", "messages": "messages", "chats": "chats"}


# ---------------------------------------------------------------------------
# Core flag operations
# ---------------------------------------------------------------------------


def _set_vectorize_flag(
    conn: sqlite3.Connection,
    table: str,
    ids: list[int],
    value: int,
) -> int:
    """Set metadata.vectorize on records via json_set().

    Returns the number of rows updated.
    """
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cursor = conn.execute(
        f"UPDATE {table} SET metadata = json_set("
        f"COALESCE(metadata, '{{}}'), '$.vectorize', ?) "
        f"WHERE id IN ({placeholders}) AND status != 'removed'",
        [value, *ids],
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_exclude(
    args: argparse.Namespace,
    *,
    db_path: Optional[Union[str, Path]] = None,
    output: Optional[Console] = None,
) -> None:
    """Set metadata.vectorize=0 for given entity/IDs."""
    table = ENTITY_TABLES.get(args.entity)
    if not table:
        (output or console).print(f"[red]Unknown entity:[/red] {args.entity}. Use one of: {', '.join(ENTITY_TABLES)}")
        return

    with open_db(db_path) as conn:
        count = _set_vectorize_flag(conn, table, args.ids, 0)
        (output or console).print(f"Excluded {count} {args.entity} from vectorization.")


def _handle_include(
    args: argparse.Namespace,
    *,
    db_path: Optional[Union[str, Path]] = None,
    output: Optional[Console] = None,
) -> None:
    """Set metadata.vectorize=1 for given entity/IDs."""
    table = ENTITY_TABLES.get(args.entity)
    if not table:
        (output or console).print(f"[red]Unknown entity:[/red] {args.entity}. Use one of: {', '.join(ENTITY_TABLES)}")
        return

    with open_db(db_path) as conn:
        count = _set_vectorize_flag(conn, table, args.ids, 1)
        (output or console).print(f"Included {count} {args.entity} for vectorization.")


def _handle_review(
    args: argparse.Namespace,
    *,
    db_path: Optional[Union[str, Path]] = None,
    output: Optional[Console] = None,
) -> None:
    """Show counts of excluded records per entity."""
    out = output or console
    entities = (
        {args.entity: ENTITY_TABLES[args.entity]} if args.entity and args.entity in ENTITY_TABLES else ENTITY_TABLES
    )

    from rich.table import Table

    table = Table(title="Vectorization Exclusions")
    table.add_column("Entity", style="bold")
    table.add_column("Excluded", justify="right")
    table.add_column("Total", justify="right")

    with open_db(db_path) as conn:
        for entity_name, table_name in entities.items():
            excluded = conn.execute(
                f"SELECT COUNT(*) as n FROM {table_name} "
                f"WHERE json_extract(metadata, '$.vectorize') = 0 "
                f"AND status != 'removed'"
            ).fetchone()["n"]
            total = conn.execute(f"SELECT COUNT(*) as n FROM {table_name} WHERE status != 'removed'").fetchone()["n"]
            table.add_row(entity_name, str(excluded), str(total))

    out.print(table)


VALID_ACTIONS = frozenset({"exclude", "include"})


def _handle_import(
    args: argparse.Namespace,
    *,
    db_path: Optional[Union[str, Path]] = None,
    output: Optional[Console] = None,
) -> None:
    """Apply vectorize flags from a JSON file."""
    out = output or console
    path = Path(args.path)
    if not path.exists():
        out.print(f"[red]File not found:[/red] {path}")
        return

    data = json.loads(path.read_text())

    # Support structured format: {"entity": ..., "action": ..., "ids": [...]}
    # and flat list format: [1, 2, 3] (defaults to files + exclude)
    if isinstance(data, list):
        entity = "files"
        action = "exclude"
        ids = [int(i) for i in data]
    elif isinstance(data, dict):
        entity = data.get("entity", "files")
        action = data.get("action", "exclude")
        ids = [int(i) for i in data.get("ids", [])]
    else:
        out.print("[red]Invalid JSON format.[/red] Expected a list or object.")
        return

    if action not in VALID_ACTIONS:
        out.print(f"[red]Unknown action:[/red] {action}. Use one of: {', '.join(VALID_ACTIONS)}")
        return

    table = ENTITY_TABLES.get(entity)
    if not table:
        out.print(f"[red]Unknown entity:[/red] {entity}. Use one of: {', '.join(ENTITY_TABLES)}")
        return

    value = 0 if action == "exclude" else 1
    with open_db(db_path) as conn:
        count = _set_vectorize_flag(conn, table, ids, value)
        verb = "Excluded" if action == "exclude" else "Included"
        out.print(f"{verb} {count} {entity} via import.")


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``fp vectorize`` subcommand."""
    parser = subparsers.add_parser(
        "vectorize",
        help="Manage per-record vectorization control",
        description="Exclude or include individual records from vectorization.",
        formatter_class=FORMATTER,
    )
    sub = parser.add_subparsers(dest="vectorize_action", metavar="ACTION")

    # exclude
    exc = sub.add_parser("exclude", help="Exclude records from vectorization")
    exc.add_argument("entity", choices=list(ENTITY_TABLES), help="Entity type")
    exc.add_argument("ids", nargs="+", type=int, help="Record IDs to exclude")
    exc.set_defaults(func=lambda args: _handle_exclude(args))

    # include
    inc = sub.add_parser("include", help="Include records for vectorization")
    inc.add_argument("entity", choices=list(ENTITY_TABLES), help="Entity type")
    inc.add_argument("ids", nargs="+", type=int, help="Record IDs to include")
    inc.set_defaults(func=lambda args: _handle_include(args))

    # review
    rev = sub.add_parser("review", help="Show excluded record counts")
    rev.add_argument(
        "entity",
        nargs="?",
        default=None,
        choices=list(ENTITY_TABLES),
        help="Filter to a specific entity type",
    )
    rev.set_defaults(func=lambda args: _handle_review(args))

    # import
    imp = sub.add_parser("import", help="Apply flags from a JSON file")
    imp.add_argument("path", help="Path to JSON file")
    imp.set_defaults(func=lambda args: _handle_import(args))

    # Default: show help if no action given
    parser.set_defaults(func=lambda args: parser.print_help())
