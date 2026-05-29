"""fp view — unified entity viewer with singular/plural noun convention.

Routes ``fp view client 42`` (single record) and ``fp view clients``
(paginated collection) through the service layer.  Supports ``--json``,
``--csv``, and ``--verbose`` output flags.
"""

import sys

from rich.panel import Panel
from rich.table import Table

from footprinter.cli._common import (
    FORMATTER,
    add_csv_flag,
    add_json_flag,
    add_verbose_flag,
    console,
    enrich_verbose_access,
    format_size,
    open_db,
    output_csv,
    output_json,
    verbose_access_cells,
)

# ---------------------------------------------------------------------------
# Entity dispatch table
# ---------------------------------------------------------------------------

#: Maps every recognised noun to (service_module, list_key, entity_type, mode).
ENTITY_MAP: dict[str, tuple[str, str, str, str]] = {
    # singular → single record
    "client": ("client_service", "clients", "client", "single"),
    "project": ("project_service", "projects", "project", "single"),
    "file": ("file_service", "files", "file", "single"),
    "folder": ("folder_service", "folders", "folder", "single"),
    "chat": ("chat_service", "chats", "chat", "single"),
    "email": ("email_service", "emails", "email", "single"),
    "visit": ("visit_service", "visits", "visit", "single"),
    # plural → paginated collection
    "clients": ("client_service", "clients", "client", "collection"),
    "projects": ("project_service", "projects", "project", "collection"),
    "files": ("file_service", "files", "file", "collection"),
    "folders": ("folder_service", "folders", "folder", "collection"),
    "chats": ("chat_service", "chats", "chat", "collection"),
    "emails": ("email_service", "emails", "email", "collection"),
    "visits": ("visit_service", "visits", "visit", "collection"),
}

# ---------------------------------------------------------------------------
# Column specs for Rich table rendering
# ---------------------------------------------------------------------------

#: (header, dict_key, style, justify)
_Col = tuple[str, str, str | None, str | None]

ENTITY_COLUMNS: dict[str, list[_Col]] = {
    "client": [
        ("ID", "id", "dim", "right"),
        ("Name", "name", "cyan", None),
        ("Type", "client_type", None, None),
        ("Status", "status", None, None),
        ("Projects", "project_count", None, "right"),
        ("Files", "file_count", None, "right"),
    ],
    "project": [
        ("ID", "id", "dim", "right"),
        ("Name", "name", "cyan", None),
        ("Client", "client", None, None),
        ("Status", "status", None, None),
        ("Files", "file_count", None, "right"),
    ],
    "file": [
        ("ID", "id", "dim", "right"),
        ("Name", "name", None, None),
        ("Source", "source", None, None),
        ("Status", "status", None, None),
        ("Project", "project_name", None, None),
    ],
    "folder": [
        ("ID", "id", "dim", "right"),
        ("Path", "relative_path", None, None),
        ("Files", "direct_files", None, "right"),
        ("Size", "total_size_bytes", None, "right"),
        ("Project", "project_name", "cyan", None),
    ],
    "chat": [
        ("ID", "id", "cyan", "right"),
        ("Account", "account", "magenta", None),
        ("Msgs", "message_count", None, "right"),
        ("Title", "title", None, None),
    ],
    "email": [
        ("ID", "id", "dim", "right"),
        ("From", "from_address", None, None),
        ("Subject", "subject", None, None),
        ("Account", "account", None, None),
        ("Date", "received_at", None, None),
    ],
    "visit": [
        ("ID", "id", "dim", "right"),
        ("Title", "title", None, None),
        ("URL", "url", None, None),
        ("Browser", "browser", None, None),
        ("Time", "visit_time", None, None),
    ],
}

# ---------------------------------------------------------------------------
# Service resolution
# ---------------------------------------------------------------------------


def _get_service(service_name: str):
    """Lazy-import and return a service module from footprinter.services."""
    import footprinter.services as svc

    return getattr(svc, service_name)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_single(args) -> None:
    """Handle singular noun: ``fp view client 42``."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, _list_key, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)

    try:
        entity_id = int(args.id)
    except ValueError:
        console.print(f"[red]Invalid ID: {args.id!r} — expected an integer.[/red]")
        sys.exit(1)

    with open_db() as conn:
        record = service.get(conn, entity_id, role=Role.ADMIN)

    if record is None:
        console.print(f"[red]{entity_type.title()} {args.id} not found.[/red]")
        sys.exit(1)

    if getattr(args, "json", False):
        enrich_verbose_access([record], entity_type)
        output_json(record)
        return

    # Rich panel — show all key-value pairs
    lines = []
    for key, value in record.items():
        if key.startswith("mcp_") or isinstance(value, (list, dict)):
            continue
        display_val = str(value) if value is not None else "—"
        lines.append(f"[bold]{key}:[/bold] {display_val}")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"{entity_type.title()} #{record['id']}",
            border_style="cyan",
        )
    )


def _handle_collection(args) -> None:
    """Handle plural noun: ``fp view clients``."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, list_key, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)

    verbose = getattr(args, "verbose", False)
    limit = getattr(args, "limit", 50)
    page = getattr(args, "page", 1)

    # --all bypasses pagination (CLI-only; bulk export for --csv / --json).
    if getattr(args, "all", False):
        limit = 1_000_000
        page = 1

    list_kwargs: dict = {"role": Role.ADMIN, "limit": limit, "page": page}
    if noun == "folders":
        list_kwargs["depth"] = getattr(args, "depth", None)

    with open_db() as conn:
        result = service.list_(conn, **list_kwargs)
        rows = result[list_key]
        if (verbose or getattr(args, "json", False)) and rows:
            enrich_verbose_access(rows, entity_type)

    if getattr(args, "json", False):
        output_json(result)
        return

    if getattr(args, "csv", False):
        cols = ENTITY_COLUMNS.get(entity_type)
        col_keys = [c[1] for c in cols] if cols else None
        output_csv(rows, columns=col_keys)
        return

    if not rows:
        console.print(f"[dim]No {list_key} found.[/dim]")
        return

    # Build Rich table from column specs
    pag = result["pagination"]
    table = Table(
        title=f"{list_key.title()} (page {pag['page']}/{pag['total_pages']}, {pag['total']} total)",
    )

    cols = ENTITY_COLUMNS.get(entity_type, [])
    for header, _key, style, justify in cols:
        kwargs: dict = {}
        if style:
            kwargs["style"] = style
        if justify:
            kwargs["justify"] = justify
        table.add_column(header, **kwargs)
    if verbose:
        table.add_column("mcp_view")
        table.add_column("mcp_read")
        table.add_column("Visibility")
        table.add_column("Access")
        table.add_column("Source")

    for row in rows:
        cells = []
        for _header, key, _style, _justify in cols:
            val = row.get(key)
            if key == "total_size_bytes" and isinstance(val, (int, float)):
                cells.append(format_size(int(val)))
            elif key == "file_count" and isinstance(val, int):
                cells.append(f"{val:,}")
            else:
                cells.append(str(val) if val is not None else "")
        if verbose:
            cells.extend(verbose_access_cells(row))
        table.add_row(*cells)

    console.print(table)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

SINGULAR_NOUNS = ["client", "project", "file", "folder", "chat", "email", "visit"]
PLURAL_NOUNS = ["clients", "projects", "files", "folders", "chats", "emails", "visits"]


def register(subparsers) -> None:
    """Register the ``view`` subcommand with noun sub-subparsers."""
    parser = subparsers.add_parser(
        "view",
        help="View entity records",
        description=(
            "View entity records — singular noun for one record, plural for a list.\n\n"
            "Singular:  fp view client 42     Single record by ID\n"
            "Plural:    fp view clients        Paginated collection\n"
            "Export:    fp view clients --csv   Bulk CSV export"
        ),
        epilog=(
            "examples:\n"
            "  fp view client 42               View a single client\n"
            "  fp view clients                  List all clients\n"
            "  fp view clients --json           JSON output\n"
            "  fp view clients --csv            CSV export\n"
            "  fp view files --limit 10         First 10 files\n"
            "  fp view projects --verbose       Include access columns\n"
            "\n"
            "entity nouns:\n"
            "  singular: client, project, file, folder, chat, email, visit\n"
            "  plural:   clients, projects, files, folders, chats, emails, visits\n"
            "\n"
            "tip: use 'fp view <noun> --help' for details on any noun."
        ),
        formatter_class=FORMATTER,
    )
    noun_subs = parser.add_subparsers(
        dest="noun",
        metavar="NOUN",
        title="entity nouns (one required)",
    )
    parser.set_defaults(func=lambda args: parser.print_help())

    # Singular nouns — require an ID positional arg
    for noun in SINGULAR_NOUNS:
        entity_type = ENTITY_MAP[noun][2]
        p = noun_subs.add_parser(
            noun,
            help=f"View a single {entity_type}",
            description=f"Show details for a single {entity_type} record by ID.",
            formatter_class=FORMATTER,
        )
        p.add_argument("id", help=f"{entity_type.title()} ID")
        add_json_flag(p)
        p.set_defaults(func=_handle_single)

    # Plural nouns — pagination + format flags
    for noun in PLURAL_NOUNS:
        entity_type = ENTITY_MAP[noun][2]
        p = noun_subs.add_parser(
            noun,
            help=f"List {noun}",
            description=f"List {noun} with pagination.",
            formatter_class=FORMATTER,
        )
        p.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Max rows to return (default: 50)",
        )
        p.add_argument(
            "--page",
            type=int,
            default=1,
            help="Page number (default: 1)",
        )
        p.add_argument(
            "--all",
            action="store_true",
            help="Bypass pagination — fetch every row (intended for --csv / --json bulk export)",
        )
        if noun == "folders":
            p.add_argument(
                "--depth",
                type=int,
                default=None,
                help="Max folder path depth below home (default: no limit)",
            )
        add_verbose_flag(p)

        # --json and --csv are mutually exclusive
        fmt_group = p.add_mutually_exclusive_group()
        add_json_flag(fmt_group)
        add_csv_flag(fmt_group)

        p.set_defaults(func=_handle_collection)
