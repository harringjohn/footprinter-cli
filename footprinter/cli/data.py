"""fp data — export, template, and import commands for entity CSV data.

Export current data:
    ``fp data export clients``
    ``fp data export files --status active --limit 100``

Generate import-compatible templates:
    ``fp data template clients``
    ``fp data template files --file template.csv``

Import metadata corrections for data-source entities:
    ``fp data import files corrections.csv``
    ``fp data import files corrections.csv --commit``
"""

import csv
import sys
from dataclasses import dataclass, field

from footprinter.cli._common import FORMATTER, add_json_flag, console, open_db, output_json
from footprinter.cli.upsert import CSV_COLUMNS

# ---------------------------------------------------------------------------
# Export column specs — derived from CSV_COLUMNS (import column spec)
# ---------------------------------------------------------------------------

#: Export columns per entity: required + optional from CSV_COLUMNS,
#: minus client_id for projects (internal DB ID, not user-facing).
EXPORT_COLUMNS: dict[str, list[str]] = {
    "client": CSV_COLUMNS["client"][0] + CSV_COLUMNS["client"][1],
    "project": [c for c in CSV_COLUMNS["project"][0] + CSV_COLUMNS["project"][1] if c != "client_id"],
}

# ---------------------------------------------------------------------------
# Export SQL queries (clients/projects only — data-source uses registry)
# ---------------------------------------------------------------------------


def _export_query(entity_type: str, status_filter: str | None) -> tuple[str, list]:
    """Return (sql, params) for an unbounded export query."""
    params: list = []
    if entity_type == "client":
        sql = "SELECT name, client_type, slug, path_pattern, status FROM clients"
        if status_filter:
            sql += " WHERE status = ?"
            params.append(status_filter)
        else:
            sql += " WHERE status != 'removed'"
        sql += " ORDER BY name"
    else:
        sql = (
            "SELECT p.project_name, p.root_path, "
            "COALESCE(c.name, '') AS client, "
            "p.project_type, p.description, p.github_url, p.status "
            "FROM projects p LEFT JOIN clients c ON p.client_id = c.id"
        )
        if status_filter:
            sql += " WHERE p.status = ?"
            params.append(status_filter)
        else:
            sql += " WHERE p.status != 'removed'"
        sql += " ORDER BY p.project_name"
    return sql, params


# ---------------------------------------------------------------------------
# Template example rows (clients/projects only)
# ---------------------------------------------------------------------------

TEMPLATE_ROWS: dict[str, list[dict]] = {
    "client": [
        {
            "name": "Acme Corp",
            "client_type": "external",
            "slug": "",
            "path_pattern": "~/Work/clients/acme/",
            "status": "active",
        },
        {"name": "Internal Tools", "client_type": "internal", "slug": "", "path_pattern": "", "status": "active"},
        {"name": "Side Project", "client_type": "personal", "slug": "", "path_pattern": "", "status": "active"},
    ],
    "project": [
        {
            "project_name": "My Web App",
            "root_path": "~/Work/projects/my-app",
            "client": "Acme Corp",
            "project_type": "python",
            "description": "A web application",
            "github_url": "",
            "status": "active",
        },
        {
            "project_name": "Documentation",
            "root_path": "~/Work/docs",
            "client": "",
            "project_type": "docs",
            "description": "Internal documentation",
            "github_url": "",
            "status": "active",
        },
        {
            "project_name": "Mobile App",
            "root_path": "~/Work/mobile",
            "client": "Internal Tools",
            "project_type": "typescript",
            "description": "Mobile app",
            "github_url": "",
            "status": "active",
        },
    ],
}

VALID_VALUES_NOTES: dict[str, dict[str, str]] = {
    "client": {
        "client_type": "external, internal, personal",
        "status": "active, hidden, removed",
    },
    "project": {
        "status": "active, paused, completed, abandoned, removed",
    },
}


# ---------------------------------------------------------------------------
# Data-source entity registry
# ---------------------------------------------------------------------------


@dataclass
class DataSourceSpec:
    """Specification for a data-source entity's CSV operations."""

    table: str
    export_columns: list[str]
    writable_columns: list[str]
    order_by: str
    has_status: bool
    template_rows: list[dict] = field(default_factory=list)
    valid_values: dict[str, str] = field(default_factory=dict)


DATA_SOURCE_SPECS: dict[str, DataSourceSpec] = {
    "files": DataSourceSpec(
        table="files",
        export_columns=[
            "id",
            "name",
            "path",
            "source",
            "status",
            "content_type",
            "size_bytes",
            "modified_at",
            "project_id",
            "client_id",
            "mcp_view",
            "mcp_read",
        ],
        writable_columns=["status", "project_id", "client_id", "mcp_view", "mcp_read"],
        order_by="id",
        has_status=True,
        template_rows=[
            {
                "id": "1",
                "name": "readme.md",
                "path": "/Users/me/Work/readme.md",
                "source": "local",
                "status": "active",
                "content_type": "markdown",
                "size_bytes": "1024",
                "modified_at": "2026-01-15T10:00:00Z",
                "project_id": "1",
                "client_id": "1",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
            {
                "id": "2",
                "name": "notes.txt",
                "path": "/Users/me/Work/notes.txt",
                "source": "local",
                "status": "hidden",
                "content_type": "text",
                "size_bytes": "512",
                "modified_at": "2026-02-01T10:00:00Z",
                "project_id": "",
                "client_id": "",
                "mcp_view": "inherit",
                "mcp_read": "inherit",
            },
        ],
        valid_values={
            "status": "active, hidden, removed",
            "mcp_view": "hidden, opaque, visible, inherit",
            "mcp_read": "allow, deny, inherit",
        },
    ),
    "folders": DataSourceSpec(
        table="folders",
        export_columns=[
            "id",
            "path",
            "relative_path",
            "name",
            "source",
            "status",
            "project_id",
            "client_id",
            "mcp_view",
            "mcp_read",
        ],
        writable_columns=["status", "project_id", "client_id", "mcp_view", "mcp_read"],
        order_by="id",
        has_status=True,
        template_rows=[
            {
                "id": "1",
                "path": "/Users/me/Work",
                "relative_path": "Work",
                "name": "Work",
                "source": "local",
                "status": "active",
                "project_id": "1",
                "client_id": "",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
            {
                "id": "2",
                "path": "/Users/me/Personal",
                "relative_path": "Personal",
                "name": "Personal",
                "source": "local",
                "status": "active",
                "project_id": "",
                "client_id": "",
                "mcp_view": "inherit",
                "mcp_read": "inherit",
            },
        ],
        valid_values={
            "status": "active, hidden, removed",
            "mcp_view": "hidden, opaque, visible, inherit",
            "mcp_read": "allow, deny, inherit",
        },
    ),
    "emails": DataSourceSpec(
        table="emails",
        export_columns=[
            "id",
            "message_id",
            "account",
            "subject",
            "from_address",
            "received_at",
            "status",
            "project_id",
            "client_id",
            "mcp_view",
            "mcp_read",
        ],
        writable_columns=["status", "project_id", "client_id", "mcp_view", "mcp_read"],
        order_by="id",
        has_status=True,
        template_rows=[
            {
                "id": "1",
                "message_id": "msg-001@example.com",
                "account": "work",
                "subject": "Project Update",
                "from_address": "sender@example.com",
                "received_at": "2026-02-01T09:00:00Z",
                "status": "active",
                "project_id": "1",
                "client_id": "1",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
            {
                "id": "2",
                "message_id": "msg-002@example.com",
                "account": "personal",
                "subject": "Newsletter",
                "from_address": "news@example.com",
                "received_at": "2026-02-02T09:00:00Z",
                "status": "active",
                "project_id": "",
                "client_id": "",
                "mcp_view": "inherit",
                "mcp_read": "inherit",
            },
        ],
        valid_values={
            "status": "active, hidden, removed",
            "mcp_view": "hidden, opaque, visible, inherit",
            "mcp_read": "allow, deny, inherit",
        },
    ),
    "chats": DataSourceSpec(
        table="chats",
        export_columns=[
            "id",
            "external_id",
            "account",
            "title",
            "message_count",
            "status",
            "created_at",
            "updated_at",
            "project_id",
            "client_id",
            "mcp_view",
            "mcp_read",
        ],
        writable_columns=["status", "project_id", "client_id", "mcp_view", "mcp_read"],
        order_by="id",
        has_status=True,
        template_rows=[
            {
                "id": "1",
                "external_id": "conv-001",
                "account": "personal",
                "title": "Architecture Chat",
                "message_count": "5",
                "status": "active",
                "created_at": "2026-01-10T08:00:00Z",
                "updated_at": "2026-01-10T09:00:00Z",
                "project_id": "1",
                "client_id": "1",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
            {
                "id": "2",
                "external_id": "conv-002",
                "account": "personal",
                "title": "Random Chat",
                "message_count": "3",
                "status": "active",
                "created_at": "2026-01-11T08:00:00Z",
                "updated_at": "2026-01-11T09:00:00Z",
                "project_id": "",
                "client_id": "",
                "mcp_view": "inherit",
                "mcp_read": "inherit",
            },
        ],
        valid_values={
            "status": "active, hidden, removed, merged",
            "mcp_view": "hidden, opaque, visible, inherit",
            "mcp_read": "allow, deny, inherit",
        },
    ),
    "messages": DataSourceSpec(
        table="messages",
        export_columns=[
            "id",
            "chat_id",
            "message_id",
            "role",
            "created_at",
            "mcp_view",
            "mcp_read",
        ],
        writable_columns=["mcp_view", "mcp_read"],
        order_by="id",
        has_status=False,
        template_rows=[
            {
                "id": "1",
                "chat_id": "1",
                "message_id": "msg-1",
                "role": "user",
                "created_at": "2026-01-10T08:01:00Z",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
            {
                "id": "2",
                "chat_id": "1",
                "message_id": "msg-2",
                "role": "assistant",
                "created_at": "2026-01-10T08:02:00Z",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
        ],
        valid_values={
            "mcp_view": "hidden, opaque, visible, inherit",
            "mcp_read": "allow, deny, inherit",
        },
    ),
    "visits": DataSourceSpec(
        table="visits",
        export_columns=[
            "id",
            "url",
            "title",
            "visit_time",
            "browser",
            "status",
            "project_id",
            "client_id",
            "mcp_view",
            "mcp_read",
        ],
        writable_columns=["status", "project_id", "client_id", "mcp_view", "mcp_read"],
        order_by="id",
        has_status=True,
        template_rows=[
            {
                "id": "1",
                "url": "https://example.com",
                "title": "Example",
                "visit_time": "2026-03-01T12:00:00Z",
                "browser": "safari",
                "status": "active",
                "project_id": "1",
                "client_id": "1",
                "mcp_view": "visible",
                "mcp_read": "allow",
            },
            {
                "id": "2",
                "url": "https://news.com",
                "title": "News",
                "visit_time": "2026-03-02T12:00:00Z",
                "browser": "chrome",
                "status": "active",
                "project_id": "",
                "client_id": "",
                "mcp_view": "inherit",
                "mcp_read": "inherit",
            },
        ],
        valid_values={
            "status": "active, hidden, removed",
            "mcp_view": "hidden, opaque, visible, inherit",
            "mcp_read": "allow, deny, inherit",
        },
    ),
}

#: All entity nouns accepted by export/template (clients, projects + data-source)
ALL_EXPORT_NOUNS = ["clients", "projects"] + list(DATA_SOURCE_SPECS.keys())

#: Entity nouns that support import (data-source only)
IMPORT_NOUNS = list(DATA_SOURCE_SPECS.keys())


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _write_csv(columns: list[str], rows: list[dict], file_path: str | None) -> None:
    """Write CSV to file or stdout."""
    if file_path:
        with open(file_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: v if (v := row.get(k)) is not None else "" for k in columns})
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: v if (v := row.get(k)) is not None else "" for k in columns})


def _handle_export(args) -> None:
    """Handle ``fp data export <noun>``."""
    noun = args.noun

    # Data-source entities go through the registry
    if noun in DATA_SOURCE_SPECS:
        _handle_export_data_source(args)
        return

    # Existing client/project path
    entity_type = "client" if noun == "clients" else "project"
    columns = EXPORT_COLUMNS[entity_type]

    status_filter = getattr(args, "status", None)
    if status_filter:
        valid = VALID_VALUES_NOTES.get(entity_type, {}).get("status", "")
        valid_set = {v.strip() for v in valid.split(",")} if valid else set()
        if valid_set and status_filter not in valid_set:
            print(
                f"Unknown status '{status_filter}'. Valid values: {', '.join(sorted(valid_set))}",
                file=sys.stderr,
            )
            sys.exit(1)

    sql, params = _export_query(entity_type, status_filter)

    # Apply limit/offset if provided (OFFSET requires LIMIT in SQLite)
    limit = getattr(args, "limit", None)
    offset = getattr(args, "offset", None)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    elif offset is not None:
        sql += " LIMIT -1"
    if offset is not None:
        sql += " OFFSET ?"
        params.append(offset)

    with open_db() as conn:
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]

    _write_csv(columns, rows, getattr(args, "file", None))


def _handle_export_data_source(args) -> None:
    """Export a data-source entity via the registry."""
    noun = args.noun
    spec = DATA_SOURCE_SPECS[noun]
    columns = spec.export_columns
    status_filter = getattr(args, "status", None)

    col_list = ", ".join(columns)
    sql = f"SELECT {col_list} FROM {spec.table}"  # noqa: S608
    params: list = []

    # Validate status filter against known values
    if status_filter and spec.has_status:
        valid = spec.valid_values.get("status", "")
        valid_set = {v.strip() for v in valid.split(",")} if valid else set()
        if valid_set and status_filter not in valid_set:
            print(
                f"Unknown status '{status_filter}'. Valid values: {', '.join(sorted(valid_set))}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Default: exclude removed rows (for entities with status)
    if spec.has_status:
        if status_filter:
            sql += " WHERE status = ?"
            params.append(status_filter)
        else:
            sql += " WHERE status != 'removed'"
    elif status_filter:
        print(
            f"Entity '{noun}' does not have a status column.",
            file=sys.stderr,
        )
        sys.exit(1)

    sql += f" ORDER BY {spec.order_by}"

    # Apply limit/offset (OFFSET requires LIMIT in SQLite)
    limit = getattr(args, "limit", None)
    offset = getattr(args, "offset", None)
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    elif offset is not None:
        sql += " LIMIT -1"
    if offset is not None:
        sql += " OFFSET ?"
        params.append(offset)

    with open_db() as conn:
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]

    _write_csv(columns, rows, getattr(args, "file", None))


def _handle_template(args) -> None:
    """Handle ``fp data template <noun>``."""
    noun = args.noun

    # Data-source entities go through the registry
    if noun in DATA_SOURCE_SPECS:
        spec = DATA_SOURCE_SPECS[noun]
        _write_csv(spec.export_columns, spec.template_rows, getattr(args, "file", None))
        notes = spec.valid_values
        if notes:
            print("\nValid values:", file=sys.stderr)
            for fld, values in notes.items():
                print(f"  {fld}: {values}", file=sys.stderr)
        return

    # Existing client/project path
    entity_type = "client" if noun == "clients" else "project"
    columns = EXPORT_COLUMNS[entity_type]
    rows = TEMPLATE_ROWS[entity_type]

    _write_csv(columns, rows, getattr(args, "file", None))

    # Print valid value notes to stderr
    notes = VALID_VALUES_NOTES.get(entity_type, {})
    if notes:
        print("\nValid values:", file=sys.stderr)
        for fld, values in notes.items():
            print(f"  {fld}: {values}", file=sys.stderr)


def _handle_import(args) -> None:
    """Handle ``fp data import <noun> <file>``."""
    from pathlib import Path

    from rich.table import Table

    noun = args.noun
    spec = DATA_SOURCE_SPECS[noun]
    csv_path = Path(args.file)
    has_dry_run = getattr(args, "dry_run", False)
    has_commit = getattr(args, "commit", False)
    if has_dry_run and has_commit:
        console.print("[red]Cannot use --dry-run and --commit together.[/red]")
        sys.exit(1)
    dry_run = not has_commit

    # Read and validate CSV
    if not csv_path.exists():
        console.print(f"[red]File not found: {csv_path}[/red]")
        sys.exit(1)

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            console.print("[red]Empty or invalid CSV file.[/red]")
            sys.exit(1)
        if "id" not in reader.fieldnames:
            console.print("[red]CSV must contain an 'id' column.[/red]")
            sys.exit(1)
        rows = list(reader)

    if not rows:
        if getattr(args, "json", False):
            output_json({"total": 0, "updated": 0, "skipped": 0, "errors": 0})
        else:
            console.print("[dim]No rows in CSV — nothing to do.[/dim]")
        return

    # Determine which writable columns are present in the CSV
    csv_writable = [c for c in spec.writable_columns if c in reader.fieldnames]

    if not csv_writable:
        console.print(
            f"[red]No writable columns found in CSV. "
            f"Writable columns for {noun}: {', '.join(spec.writable_columns)}[/red]"
        )
        sys.exit(1)

    # Process rows
    updated = 0
    skipped = 0
    errors = 0
    error_details: list[dict] = []

    with open_db() as conn:
        # Begin ingest tracking before data writes (matches upsert.py pattern)
        ingest_svc = None
        ingest_id = None
        if not dry_run:
            from footprinter.services.ingest_service import IngestService

            ingest_svc = IngestService(conn)
            ingest_id = ingest_svc.begin(
                f"import_{noun}",
                mode="bulk",
                trigger="cli:data:import",
            )

        for i, row in enumerate(rows, 1):
            row_id = row.get("id", "").strip()
            if not row_id:
                errors += 1
                error_details.append({"row": i, "error": "Missing id value"})
                continue

            try:
                row_id_int = int(row_id)
            except ValueError:
                errors += 1
                error_details.append({"row": i, "error": f"Invalid id: {row_id!r}"})
                continue

            # Check record exists
            existing = conn.execute(
                f"SELECT id FROM {spec.table} WHERE id = ?",  # noqa: S608
                (row_id_int,),
            ).fetchone()
            if existing is None:
                errors += 1
                error_details.append({"row": i, "error": f"ID {row_id_int} not found"})
                continue

            # Build SET clause from writable columns with non-empty values
            set_parts: list[str] = []
            set_params: list = []
            for col in csv_writable:
                val = row.get(col, "")
                if val == "":
                    continue  # Empty = skip (preserve existing)
                # Sentinel: "0" for project_id/client_id clears to NULL
                if col in ("project_id", "client_id") and val == "0":
                    set_parts.append(f"{col} = ?")
                    set_params.append(None)
                else:
                    set_parts.append(f"{col} = ?")
                    set_params.append(val)

            if not set_parts:
                skipped += 1
                continue

            if dry_run:
                updated += 1
            else:
                update_sql = (
                    f"UPDATE {spec.table} SET {', '.join(set_parts)} "  # noqa: S608
                    f"WHERE id = ?"
                )
                set_params.append(row_id_int)
                conn.execute(update_sql, set_params)
                updated += 1

        # Complete ingest tracking and commit everything together
        if ingest_svc is not None and ingest_id is not None:
            ingest_svc.complete(
                ingest_id,
                result={
                    "items_processed": updated + skipped + errors,
                    "items_updated": updated,
                    "items_skipped": skipped,
                    "errors": errors,
                },
                metadata={"error_details": error_details} if error_details else None,
            )

    summary: dict = {
        "total": updated + skipped + errors,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
    if error_details:
        summary["error_details"] = error_details

    if getattr(args, "json", False):
        output_json(summary)
    elif dry_run:
        table = Table(title=f"Dry run — import {noun}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Would update", str(updated))
        table.add_row("Skipped (no changes)", str(skipped))
        table.add_row("Errors", str(errors))
        table.add_row("Total rows", str(updated + skipped + errors))
        console.print(table)
        if error_details:
            for ed in error_details:
                console.print(f"  [red]Row {ed['row']}: {ed['error']}[/red]")
        console.print("[dim]Pass --commit to apply these changes.[/dim]")
    else:
        table = Table(title=f"Import {noun}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Updated", str(updated))
        table.add_row("Skipped (no changes)", str(skipped))
        table.add_row("Errors", str(errors))
        table.add_row("Total rows", str(updated + skipped + errors))
        console.print(table)
        if error_details:
            for ed in error_details:
                console.print(f"  [red]Row {ed['row']}: {ed['error']}[/red]")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register ``fp data`` with ``export``, ``template``, and ``import`` subcommands."""
    data_parser = subparsers.add_parser(
        "data",
        help="Export data, generate templates, or import metadata corrections",
        formatter_class=FORMATTER,
    )
    data_parser.set_defaults(func=lambda args: data_parser.print_help())
    data_sub = data_parser.add_subparsers(dest="data_action", metavar="ACTION")

    # -- fp data export ---------------------------------------------------
    export_parser = data_sub.add_parser(
        "export",
        help="Export entity data as CSV",
        formatter_class=FORMATTER,
    )
    export_parser.add_argument(
        "noun",
        choices=ALL_EXPORT_NOUNS,
        help="Entity type to export",
    )
    export_parser.add_argument(
        "--file",
        default=None,
        help="Write output to file instead of stdout",
    )
    export_parser.add_argument(
        "--status",
        default=None,
        help="Filter by status (e.g., active)",
    )
    export_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of rows to export",
    )
    export_parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Number of rows to skip before exporting",
    )
    export_parser.set_defaults(func=_handle_export)

    # -- fp data template -------------------------------------------------
    template_parser = data_sub.add_parser(
        "template",
        help="Generate an import-compatible CSV template",
        formatter_class=FORMATTER,
    )
    template_parser.add_argument(
        "noun",
        choices=ALL_EXPORT_NOUNS,
        help="Entity type for template",
    )
    template_parser.add_argument(
        "--file",
        default=None,
        help="Write template to file instead of stdout",
    )
    template_parser.set_defaults(func=_handle_template)

    # -- fp data import ---------------------------------------------------
    import_parser = data_sub.add_parser(
        "import",
        help="Import metadata corrections from CSV",
        description=(
            "Import metadata corrections for data-source entities.\n\n"
            "Reads a CSV file with an 'id' column and updates writable metadata\n"
            "columns. Pipeline-managed fields (path, external_id, etc.) are\n"
            "read-only and ignored during import.\n\n"
            "Default mode is dry-run (preview only). Pass --commit to apply."
        ),
        formatter_class=FORMATTER,
    )
    import_parser.add_argument(
        "noun",
        choices=IMPORT_NOUNS,
        help="Entity type to import",
    )
    import_parser.add_argument(
        "file",
        help="Path to CSV file",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview changes without writing (default behavior)",
    )
    import_parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Apply changes to the database",
    )
    add_json_flag(import_parser)
    import_parser.set_defaults(func=_handle_import)
