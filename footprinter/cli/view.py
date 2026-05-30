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
    add_template_flag,
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

_FILTER_SUPPORT: dict[str, set[str]] = {
    "files": {"project_id"},
    "folders": {"project_id"},
    "emails": {"project_id", "client_id"},
    "chats": {"project_id", "client_id"},
    "visits": {"project_id", "client_id"},
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

#: Full export column sets per entity type — used by --csv and --template.
#: Migrated from data.py DATA_SOURCE_SPECS.export_columns and EXPORT_COLUMNS.
EXPORT_COLUMNS: dict[str, list[str]] = {
    "client": ["name", "client_type", "slug", "status"],
    "project": ["name", "client", "description", "status"],
    "file": [
        "id", "name", "path", "source", "status", "content_type",
        "size_bytes", "modified_at", "project_id", "client_id", "visibility", "access",
    ],
    "folder": [
        "id", "path", "relative_path", "name", "source", "status",
        "project_id", "client_id", "visibility", "access",
    ],
    "email": [
        "id", "message_id", "account", "subject", "from_address", "received_at",
        "status", "project_id", "client_id", "visibility", "access",
    ],
    "chat": [
        "id", "external_id", "account", "title", "message_count", "status",
        "created_at", "updated_at", "project_id", "client_id", "visibility", "access",
    ],
    "visit": [
        "id", "url", "title", "visit_time", "browser", "status",
        "project_id", "client_id", "visibility", "access",
    ],
}

#: Service-layer dict key → export CSV column name (where they differ).
EXPORT_KEY_MAP: dict[str, dict[str, str]] = {}

#: Template example rows per entity type — used by --template.
TEMPLATE_ROWS: dict[str, list[dict]] = {
    "client": [
        {"name": "Acme Corp", "client_type": "external", "slug": "", "status": "listed"},
        {"name": "Internal Tools", "client_type": "internal", "slug": "", "status": "listed"},
        {"name": "Side Project", "client_type": "personal", "slug": "", "status": "listed"},
    ],
    "project": [
        {"name": "My Web App", "client": "Acme Corp", "description": "A web application", "status": "listed"},
        {"name": "Documentation", "client": "", "description": "Internal documentation", "status": "listed"},
        {"name": "Mobile App", "client": "Internal Tools", "description": "Mobile app", "status": "listed"},
    ],
    "file": [
        {
            "id": "1", "name": "readme.md", "path": "/Users/me/Work/readme.md",
            "source": "local", "status": "listed", "content_type": "markdown",
            "size_bytes": "1024", "modified_at": "2026-01-15T10:00:00Z",
            "project_id": "1", "client_id": "1",
        },
        {
            "id": "2", "name": "notes.txt", "path": "/Users/me/Work/notes.txt",
            "source": "local", "status": "hidden", "content_type": "text",
            "size_bytes": "512", "modified_at": "2026-02-01T10:00:00Z",
            "project_id": "", "client_id": "",
        },
    ],
    "folder": [
        {
            "id": "1", "path": "/Users/me/Work", "relative_path": "Work", "name": "Work",
            "source": "local", "status": "listed", "project_id": "1", "client_id": "",
        },
        {
            "id": "2", "path": "/Users/me/Personal", "relative_path": "Personal",
            "name": "Personal", "source": "local", "status": "listed",
            "project_id": "", "client_id": "",
        },
    ],
    "email": [
        {
            "id": "1", "message_id": "msg-001@example.com", "account": "work",
            "subject": "Project Update", "from_address": "sender@example.com",
            "received_at": "2026-02-01T09:00:00Z", "status": "listed",
            "project_id": "1", "client_id": "1",
        },
        {
            "id": "2", "message_id": "msg-002@example.com", "account": "personal",
            "subject": "Newsletter", "from_address": "news@example.com",
            "received_at": "2026-02-02T09:00:00Z", "status": "listed",
            "project_id": "", "client_id": "",
        },
    ],
    "chat": [
        {
            "id": "1", "external_id": "conv-001", "account": "personal",
            "title": "Architecture Chat", "message_count": "5", "status": "listed",
            "created_at": "2026-01-10T08:00:00Z", "updated_at": "2026-01-10T09:00:00Z",
            "project_id": "1", "client_id": "1",
        },
        {
            "id": "2", "external_id": "conv-002", "account": "personal",
            "title": "Random Chat", "message_count": "3", "status": "listed",
            "created_at": "2026-01-11T08:00:00Z", "updated_at": "2026-01-11T09:00:00Z",
            "project_id": "", "client_id": "",
        },
    ],
    "visit": [
        {
            "id": "1", "url": "https://example.com", "title": "Example",
            "visit_time": "2026-03-01T12:00:00Z", "browser": "safari",
            "status": "listed", "project_id": "1", "client_id": "1",
        },
        {
            "id": "2", "url": "https://news.com", "title": "News",
            "visit_time": "2026-03-02T12:00:00Z", "browser": "chrome",
            "status": "listed", "project_id": "", "client_id": "",
        },
    ],
}

_NON_TEMPLATE_COLS = {"visibility", "access"}

_POLICY_VALUES = "hidden, opaque, full, inherit"
_ACCESS_VALUES = "allow, deny, inherit"
_STATUS_VALUES = "listed, unlisted, removed"

#: Valid-values notes per entity type — printed to stderr by --template.
VALID_VALUES_NOTES: dict[str, dict[str, str]] = {
    "client": {"client_type": "external, internal, personal", "status": _STATUS_VALUES},
    "project": {"status": _STATUS_VALUES},
    "file": {"status": _STATUS_VALUES, "visibility": _POLICY_VALUES, "access": _ACCESS_VALUES},
    "folder": {"status": _STATUS_VALUES, "visibility": _POLICY_VALUES, "access": _ACCESS_VALUES},
    "email": {"status": _STATUS_VALUES, "visibility": _POLICY_VALUES, "access": _ACCESS_VALUES},
    "chat": {"status": _STATUS_VALUES, "visibility": _POLICY_VALUES, "access": _ACCESS_VALUES},
    "visit": {"status": _STATUS_VALUES, "visibility": _POLICY_VALUES, "access": _ACCESS_VALUES},
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

    if getattr(args, "template", False):
        export_cols = EXPORT_COLUMNS.get(entity_type)
        template_cols = [c for c in export_cols if c not in _NON_TEMPLATE_COLS]
        template_data = TEMPLATE_ROWS.get(entity_type, [])
        output_csv(template_data, columns=template_cols)
        notes = {k: v for k, v in VALID_VALUES_NOTES.get(entity_type, {}).items()
                 if k not in _NON_TEMPLATE_COLS}
        if notes:
            print("\nValid values:", file=sys.stderr)
            for fld, values in notes.items():
                print(f"  {fld}: {values}", file=sys.stderr)
        return

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

    project_id = getattr(args, "project_id", None)
    if project_id is not None:
        list_kwargs["project_id"] = project_id

    client_id = getattr(args, "client_id", None)
    if client_id is not None:
        list_kwargs["client_id"] = client_id

    with open_db() as conn:
        if list_kwargs.get("project_id") is not None:
            from footprinter.services import project_service

            if project_service.get(conn, list_kwargs["project_id"], role=Role.ADMIN) is None:
                console.print(f"[red]Project {list_kwargs['project_id']} not found.[/red]")
                sys.exit(1)
        if list_kwargs.get("client_id") is not None:
            from footprinter.services import client_service

            if client_service.get(conn, list_kwargs["client_id"], role=Role.ADMIN) is None:
                console.print(f"[red]Client {list_kwargs['client_id']} not found.[/red]")
                sys.exit(1)

        result = service.list_(conn, **list_kwargs)
        rows = result[list_key]
        if (verbose or getattr(args, "json", False)) and rows:
            enrich_verbose_access(rows, entity_type)

    if getattr(args, "json", False):
        output_json(result)
        return

    if getattr(args, "csv", False):
        export_cols = EXPORT_COLUMNS.get(entity_type)
        key_map = EXPORT_KEY_MAP.get(entity_type, {})
        if key_map:
            rows = [{key_map.get(k, k): v for k, v in row.items()} for row in rows]
        output_csv(rows, columns=export_cols)
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
        table.add_column("visibility_raw")
        table.add_column("access_raw")
        table.add_column("Visibility")
        table.add_column("Access")
        table.add_column("Source")
        table.add_column("Vis Source")

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
            "  fp view files --project 3        Files in project 3\n"
            "  fp view emails --client 1        Emails for client 1\n"
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
        supported = _FILTER_SUPPORT.get(noun, set())
        if "project_id" in supported:
            p.add_argument(
                "--project",
                type=int,
                default=None,
                dest="project_id",
                metavar="ID",
                help="Filter by project ID",
            )
        if "client_id" in supported:
            p.add_argument(
                "--client",
                type=int,
                default=None,
                dest="client_id",
                metavar="ID",
                help="Filter by client ID",
            )
        add_verbose_flag(p)

        # --json, --csv, and --template are mutually exclusive
        fmt_group = p.add_mutually_exclusive_group()
        add_json_flag(fmt_group)
        add_csv_flag(fmt_group)
        add_template_flag(fmt_group)

        p.set_defaults(func=_handle_collection)
