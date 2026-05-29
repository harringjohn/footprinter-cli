"""fp update — modify per-record fields.

Commands:
    fp update file <id> --vectorize true|false     Toggle vectorization for a file
    fp update message <id> --vectorize true|false   Toggle vectorization for a message
    fp update chat <id> --vectorize true|false      Toggle vectorization for a chat
    fp update review [<entity>]                     Show excluded record counts
    fp update import <path>                         Apply vectorize flags from a JSON file
"""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Optional, Union

from rich.console import Console

from footprinter.cli._common import FORMATTER, console, open_db

ENTITY_TABLES = {"file": "files", "message": "messages", "chat": "chats"}
REVIEW_ENTITIES = {"files": "files", "messages": "messages", "chats": "chats"}
VALID_ACTIONS = frozenset({"exclude", "include"})


def _handle_update(
    args: argparse.Namespace,
    *,
    db_path: Optional[Union[str, Path]] = None,
    output: Optional[Console] = None,
) -> None:
    """Set vectorize column on a single record."""
    out = output or console
    table = args.entity_table
    value = 1 if args.vectorize == "true" else 0

    with open_db(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE {table} SET vectorize = ? WHERE id = ? AND status = 'listed'",
            (value, args.id),
        )
        conn.commit()
        if cursor.rowcount:
            label = "included in" if value else "excluded from"
            out.print(f"Record {args.id} {label} vectorization.")
        else:
            out.print(f"No listed record with id {args.id} in {table}.")


def _handle_review(
    args: argparse.Namespace,
    *,
    db_path: Optional[Union[str, Path]] = None,
    output: Optional[Console] = None,
) -> None:
    """Show counts of excluded records per entity."""
    out = output or console
    entities = (
        {args.entity: REVIEW_ENTITIES[args.entity]}
        if args.entity and args.entity in REVIEW_ENTITIES
        else REVIEW_ENTITIES
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
                f"WHERE vectorize = 0 AND status = 'listed'"
            ).fetchone()["n"]
            total = conn.execute(
                f"SELECT COUNT(*) as n FROM {table_name} WHERE status = 'listed'"
            ).fetchone()["n"]
            table.add_row(entity_name, str(excluded), str(total))

    out.print(table)


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

    table = REVIEW_ENTITIES.get(entity)
    if not table:
        out.print(f"[red]Unknown entity:[/red] {entity}. Use one of: {', '.join(REVIEW_ENTITIES)}")
        return

    value = 0 if action == "exclude" else 1
    if not ids:
        out.print("No IDs to process.")
        return

    placeholders = ",".join("?" for _ in ids)
    with open_db(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE {table} SET vectorize = ? "
            f"WHERE id IN ({placeholders}) AND status = 'listed'",
            [value, *ids],
        )
        conn.commit()
        verb = "Excluded" if action == "exclude" else "Included"
        out.print(f"{verb} {cursor.rowcount} {entity} via import.")


def register(subparsers) -> None:
    """Register the ``fp update`` subcommand."""
    parser = subparsers.add_parser(
        "update",
        help="Update per-record fields",
        description="Modify fields on individual records (files, messages, chats).",
        formatter_class=FORMATTER,
    )
    sub = parser.add_subparsers(dest="update_action", metavar="ACTION")

    for noun, table in ENTITY_TABLES.items():
        p = sub.add_parser(noun, help=f"Update a {noun} record")
        p.add_argument("id", type=int, help="Record ID")
        p.add_argument(
            "--vectorize",
            choices=["true", "false"],
            required=True,
            help="Include (true) or exclude (false) from vectorization",
        )
        p.set_defaults(func=lambda args: _handle_update(args), entity_table=table)

    rev = sub.add_parser("review", help="Show excluded record counts")
    rev.add_argument(
        "entity",
        nargs="?",
        default=None,
        choices=list(REVIEW_ENTITIES),
        help="Filter to a specific entity type",
    )
    rev.set_defaults(func=lambda args: _handle_review(args))

    imp = sub.add_parser("import", help="Apply vectorize flags from a JSON file")
    imp.add_argument("path", help="Path to JSON file")
    imp.set_defaults(func=lambda args: _handle_import(args))

    parser.set_defaults(func=lambda args: parser.print_help())
