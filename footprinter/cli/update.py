"""fp update — update existing entity records.

Modify super entities (client, project) by ID, assign data entity
relationships, update file status, or bulk-update via CSV.

Single:      ``fp update client 5 --name "New Name"``
Assign:      ``fp update file 42 --project-id 3``
Status:      ``fp update file 42 --status unlisted``
Bulk assign: ``fp update files --folder /path --project-id 3``
Bulk CSV:    ``fp update files corrections.csv``
"""

import csv
import sqlite3
import sys
from pathlib import Path

from rich.table import Table

from footprinter.cli._common import (
    FORMATTER,
    add_json_flag,
    console,
    open_db,
    output_json,
)
from footprinter.cli.data import DATA_SOURCE_SPECS
from footprinter.cli.upsert import VALID_STATUSES_BY_ENTITY

# ---------------------------------------------------------------------------
# Entity dispatch table
# ---------------------------------------------------------------------------

ENTITY_MAP: dict[str, tuple[str, str, str]] = {
    # singular super entities → field update by ID
    "client": ("client_service", "client", "single"),
    "project": ("project_service", "project", "single"),
    # singular data entities → assign + status
    "file": ("file_service", "file", "data_single"),
    "email": ("email_service", "email", "data_single"),
    "chat": ("chat_service", "chat", "data_single"),
    "visit": ("visit_service", "visit", "data_single"),
    "folder": ("folder_service", "folder", "data_single"),
    # plural data entities → bulk assign or CSV
    "files": ("file_service", "file", "bulk"),
    "folders": ("folder_service", "folder", "bulk_folder"),
}

# ---------------------------------------------------------------------------
# Per-entity argument specs for single mode (all optional — update, not create)
# ---------------------------------------------------------------------------

UPDATE_ARGS: dict[str, list[tuple[str, dict, str]]] = {
    "client": [
        ("--name", {"default": None, "help": "Client name"}, "name"),
        (
            "--type",
            {"default": None, "help": "Client type (external, internal, personal)", "dest": "client_type"},
            "client_type",
        ),
        ("--status", {"default": None, "help": "Client status (listed, unlisted, removed)"}, "status"),
    ],
    "project": [
        ("--name", {"default": None, "help": "Project name"}, "name"),
        ("--client-id", {"default": None, "type": int, "help": "Client ID"}, "client_id"),
        ("--description", {"default": None, "help": "Project description"}, "description"),
        ("--status", {"default": None, "help": "Project status (listed, unlisted, removed)"}, "status"),
    ],
}

# ---------------------------------------------------------------------------
# DB update dispatch
# ---------------------------------------------------------------------------

_DB_UPDATE_FN: dict[str, tuple[str, str]] = {
    "client": ("footprinter.db.clients", "update_client"),
    "project": ("footprinter.db.projects", "update_project"),
}


def _update_entity(conn: sqlite3.Connection, entity_id: int, entity_type: str, **fields):
    """Call the DB-layer update function for a super entity."""
    import importlib

    module_path, fn_name = _DB_UPDATE_FN[entity_type]
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    return fn(conn, entity_id, **fields)


def _update_file_status(conn: sqlite3.Connection, file_id: int, status: str, *, reason: str | None = None):
    """Call db.update_file_status()."""
    from footprinter.db.files import update_file_status

    return update_file_status(conn, file_id, status, reason=reason)


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
    """Handle super entity update: ``fp update client 5 --name "New"``."""
    noun = args.noun
    _svc_name, entity_type, _mode = ENTITY_MAP[noun]
    arg_specs = UPDATE_ARGS[entity_type]

    entity_id = args.id

    kwargs: dict = {}
    for _flag, _ap_kwargs, svc_kwarg in arg_specs:
        val = getattr(args, svc_kwarg, None)
        if val is not None:
            kwargs[svc_kwarg] = val

    if not kwargs:
        if getattr(args, "json", False):
            output_json({"error": f"At least one field flag is required for {entity_type} update"})
        else:
            console.print(f"[red]At least one field flag is required for {entity_type} update.[/red]")
        sys.exit(1)

    if "status" in kwargs:
        valid = VALID_STATUSES_BY_ENTITY.get(entity_type)
        if valid and kwargs["status"] not in valid:
            if getattr(args, "json", False):
                output_json({"error": f"Invalid status '{kwargs['status']}' for {entity_type}. Valid: {', '.join(sorted(valid))}"})
            else:
                console.print(
                    f"[red]Invalid status '{kwargs['status']}' for {entity_type}. Valid: {', '.join(sorted(valid))}[/red]"
                )
            sys.exit(1)
        if kwargs["status"] == "removed":
            kwargs["status_reason"] = "cli:update"

    with open_db() as conn:
        try:
            result = _update_entity(conn, entity_id, entity_type, **kwargs)
        except ValueError as e:
            if getattr(args, "json", False):
                output_json({"error": str(e)})
            else:
                console.print(f"[red]{e}[/red]")
            sys.exit(1)

    if result is None:
        if getattr(args, "json", False):
            output_json({"error": f"{entity_type.title()} {entity_id} not found"})
        else:
            console.print(f"[red]{entity_type.title()} {entity_id} not found.[/red]")
        sys.exit(1)

    if getattr(args, "json", False):
        output_json({"id": entity_id, "action": "updated"})
    else:
        console.print(f"[green]{entity_type.title()} {entity_id} updated.[/green]")


def _handle_data_single(args) -> None:
    """Handle data entity update: assign, status, or both."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]

    entity_id = args.id
    project_id = getattr(args, "project_id", None)
    client_id = getattr(args, "client_id", None)
    status = getattr(args, "status", None)

    has_assign = project_id is not None or client_id is not None
    has_status = status is not None and entity_type == "file"

    if not has_assign and not has_status:
        if getattr(args, "json", False):
            output_json({"error": f"At least one of --project-id, --client-id, or --status is required"})
        else:
            console.print("[red]At least one of --project-id, --client-id, or --status is required.[/red]")
        sys.exit(1)

    with open_db() as conn:
        if has_assign:
            service = _get_service(svc_name)
            try:
                result = service.assign(
                    conn,
                    entity_id,
                    role=Role.ADMIN,
                    project_id=project_id,
                    client_id=client_id,
                )
            except (ValueError, PermissionError) as e:
                if getattr(args, "json", False):
                    output_json({"error": str(e)})
                else:
                    console.print(f"[red]{e}[/red]")
                sys.exit(1)

            if result is None:
                if getattr(args, "json", False):
                    output_json({"error": f"{entity_type.title()} {entity_id} not found"})
                else:
                    console.print(f"[red]{entity_type.title()} {entity_id} not found.[/red]")
                sys.exit(1)

        if has_status:
            try:
                status_result = _update_file_status(conn, entity_id, status, reason="cli:update")
            except ValueError as e:
                if getattr(args, "json", False):
                    output_json({"error": str(e)})
                else:
                    console.print(f"[red]{e}[/red]")
                sys.exit(1)

            if status_result is None:
                if getattr(args, "json", False):
                    output_json({"error": f"{entity_type.title()} {entity_id} not found"})
                else:
                    console.print(f"[red]{entity_type.title()} {entity_id} not found.[/red]")
                sys.exit(1)

    if getattr(args, "json", False):
        summary: dict = {"id": entity_id}
        if has_assign:
            if project_id is not None:
                summary["project_id"] = project_id
            if client_id is not None:
                summary["client_id"] = client_id
        if has_status:
            summary["status"] = status
        output_json(summary)
    else:
        parts = []
        if has_assign:
            assign_parts = []
            if project_id is not None:
                assign_parts.append(f"project {project_id}")
            if client_id is not None:
                assign_parts.append(f"client {client_id}")
            parts.append(f"assigned to {' and '.join(assign_parts)}")
        if has_status:
            parts.append(f"status set to {status}")
        console.print(f"[green]{entity_type.title()} {entity_id} {', '.join(parts)}.[/green]")


# ---------------------------------------------------------------------------
# Bulk handlers
# ---------------------------------------------------------------------------


def _handle_bulk_dispatch(args) -> None:
    """Route ``fp update files`` to CSV or folder-path handler."""
    csv_file = getattr(args, "file", None)
    folder_flag = getattr(args, "folder", None)

    if csv_file and folder_flag:
        console.print("[red]Cannot use both a CSV file and --folder.[/red]")
        sys.exit(1)

    if csv_file:
        _handle_bulk_csv(args)
    elif folder_flag:
        _handle_bulk_assign(args)
    else:
        console.print("[red]Provide a CSV file or use --folder.[/red]")
        sys.exit(1)


def _handle_bulk_assign(args) -> None:
    """Handle bulk path-based assignment for files."""
    from footprinter.db.files import list_file_ids_under_path
    from footprinter.services.roles import Role

    svc_name, entity_type, _mode = ENTITY_MAP["files"]
    service = _get_service(svc_name)

    raw_folder = args.folder.strip()
    folder_path = Path(raw_folder).expanduser()
    if not folder_path.is_absolute():
        folder_path = Path.home() / folder_path
    folder_path_str = str(folder_path).rstrip("/")
    project_id = getattr(args, "project_id", None)
    client_id = getattr(args, "client_id", None)

    if project_id is None and client_id is None:
        console.print("[red]At least one of --project-id or --client-id is required.[/red]")
        sys.exit(1)

    files_touched = 0

    with open_db() as conn:
        try:
            file_ids = list_file_ids_under_path(conn, folder_path_str)
            for fid in file_ids:
                result = service.assign(
                    conn,
                    fid,
                    role=Role.ADMIN,
                    project_id=project_id,
                    client_id=client_id,
                )
                if result is not None:
                    files_touched += 1
        except (ValueError, PermissionError, sqlite3.OperationalError) as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

    summary: dict = {"assigned": files_touched}
    if project_id is not None:
        summary["project_assigned"] = files_touched
    if client_id is not None:
        summary["client_assigned"] = files_touched

    if getattr(args, "json", False):
        output_json(summary)
    else:
        console.print(f"[green]{files_touched} file(s) assigned.[/green]")


def _handle_bulk_folder_assign(args) -> None:
    """Handle bulk folder assignment via --folder flag."""
    from footprinter.db.folders import cascade_client_id, cascade_project_id

    raw_folder = args.folder.strip()
    project_id = getattr(args, "project_id", None)
    client_id = getattr(args, "client_id", None)

    if project_id is None and client_id is None:
        console.print("[red]At least one of --project-id or --client-id is required.[/red]")
        sys.exit(1)

    project_assigned = 0
    client_assigned = 0

    with open_db() as conn:
        try:
            folder_row, resolved = _resolve_folder_by_path(conn, raw_folder)
            if folder_row is None:
                console.print(f"[red]Folder not found: {resolved}[/red]")
                sys.exit(1)
            folder_id = folder_row["id"]

            if project_id is not None:
                result = cascade_project_id(conn, folder_id, project_id)
                project_assigned = result["folders_updated"] + result["files_updated"]
            if client_id is not None:
                result = cascade_client_id(conn, folder_id, client_id)
                client_assigned = result["folders_updated"] + result["files_updated"]
        except (ValueError, PermissionError, sqlite3.OperationalError) as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

    assigned = max(project_assigned, client_assigned)
    summary: dict = {"assigned": assigned}
    if project_id is not None:
        summary["project_assigned"] = project_assigned
    if client_id is not None:
        summary["client_assigned"] = client_assigned

    if getattr(args, "json", False):
        output_json(summary)
    elif project_id is not None and client_id is not None:
        console.print(
            f"[green]Project assigned to {project_assigned} folder(s)/file(s). "
            f"Client assigned to {client_assigned} folder(s)/file(s).[/green]"
        )
    else:
        console.print(f"[green]{assigned} folder(s)/file(s) assigned.[/green]")


def _resolve_folder_by_path(conn: sqlite3.Connection, raw_path: str) -> tuple[dict | None, str]:
    """Resolve a folder path string to a folder row dict."""
    from footprinter.db.folders import get_folder_by_path, get_folder_by_relative_path

    folder_path = Path(raw_path).expanduser()
    if not folder_path.is_absolute():
        folder_path = Path.home() / folder_path
    path_str = str(folder_path).rstrip("/")

    result = get_folder_by_path(conn, path_str)
    if result is not None:
        return result, path_str

    home_str = str(Path.home())
    rel_path = path_str[len(home_str):] if path_str.startswith(home_str) else path_str
    return get_folder_by_relative_path(conn, rel_path), path_str


def _handle_bulk_csv(args) -> None:
    """Handle bulk CSV update: ``fp update files corrections.csv``."""
    noun = args.noun
    spec = DATA_SOURCE_SPECS.get(noun)
    if spec is None:
        console.print(f"[red]CSV update not supported for {noun}.[/red]")
        sys.exit(1)

    csv_path = Path(args.file)

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

    csv_writable = [c for c in spec.writable_columns if c in reader.fieldnames]

    if not spec.writable_columns:
        if getattr(args, "json", False):
            output_json({"total": 0, "updated": 0, "skipped": 0, "errors": 0})
        else:
            console.print(f"[dim]{noun} has no writable columns — nothing to update.[/dim]")
        return

    if not csv_writable:
        console.print(
            f"[red]No writable columns found in CSV. "
            f"Writable columns for {noun}: {', '.join(spec.writable_columns)}[/red]"
        )
        sys.exit(1)

    updated = 0
    skipped = 0
    errors = 0
    error_details: list[dict] = []

    with open_db() as conn:
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

            existing = conn.execute(
                f"SELECT id FROM {spec.table} WHERE id = ?",  # noqa: S608
                (row_id_int,),
            ).fetchone()
            if existing is None:
                errors += 1
                error_details.append({"row": i, "error": f"ID {row_id_int} not found"})
                continue

            set_parts: list[str] = []
            set_params: list = []
            for col in csv_writable:
                val = row.get(col, "")
                if val == "":
                    continue
                if col in ("project_id", "client_id") and val == "0":
                    set_parts.append(f"{col} = ?")
                    set_params.append(None)
                else:
                    set_parts.append(f"{col} = ?")
                    set_params.append(val)

            if not set_parts:
                skipped += 1
                continue

            update_sql = (
                f"UPDATE {spec.table} SET {', '.join(set_parts)} "  # noqa: S608
                f"WHERE id = ?"
            )
            set_params.append(row_id_int)
            conn.execute(update_sql, set_params)
            updated += 1

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
    else:
        table = Table(title=f"Update {noun}")
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
    """Register the ``update`` subcommand with noun sub-subparsers."""
    parser = subparsers.add_parser(
        "update",
        help="Update existing entity records",
        description=(
            "Update existing records by ID, assign relationships, or bulk-update via CSV.\n\n"
            "Single:      fp update client 5 --name \"New Name\"\n"
            "Assign:      fp update file 42 --project-id 3\n"
            "Status:      fp update file 42 --status unlisted\n"
            "Bulk assign: fp update files --folder /path --project-id 3\n"
            "Bulk CSV:    fp update files corrections.csv"
        ),
        epilog=(
            "examples:\n"
            "  fp update client 5 --name 'New Name'           Update client name\n"
            "  fp update project 3 --status unlisted           Update project status\n"
            "  fp update file 42 --project-id 3                Assign file to project\n"
            "  fp update file 42 --status unlisted             Update file status\n"
            "  fp update files --folder ~/Work/acme --project-id 3  Assign files under folder\n"
            "  fp update files corrections.csv                 Bulk update from CSV\n"
            "\n"
            "entity nouns:\n"
            "  field update: client, project\n"
            "  assign:       file, email, chat, visit, folder\n"
            "  bulk:         files, folders\n"
            "\n"
            "tip: use 'fp update <noun> --help' for details on any noun."
        ),
        formatter_class=FORMATTER,
    )
    noun_subs = parser.add_subparsers(
        dest="noun",
        metavar="NOUN",
        title="entity nouns (one required)",
    )
    parser.set_defaults(func=lambda args: parser.print_help())

    # Super entity nouns — positional ID + optional flags
    for noun in ["client", "project"]:
        entity_type = ENTITY_MAP[noun][1]
        p = noun_subs.add_parser(
            noun,
            help=f"Update an existing {entity_type} by ID",
            description=f"Update an existing {entity_type} record by ID.",
            formatter_class=FORMATTER,
        )
        p.add_argument("id", type=int, help=f"{entity_type.title()} ID")
        for flag, ap_kwargs, _svc_kwarg in UPDATE_ARGS[entity_type]:
            p.add_argument(flag, **ap_kwargs)
        add_json_flag(p)
        p.set_defaults(func=_handle_single)

    # Data entity singular nouns — assign + status
    for noun in ["file", "email", "chat", "visit", "folder"]:
        entity_type = ENTITY_MAP[noun][1]
        p = noun_subs.add_parser(
            noun,
            help=f"Update a {entity_type} (assign or change status)",
            description=f"Assign a {entity_type} to a project/client, or update its status.",
            formatter_class=FORMATTER,
        )
        p.add_argument("id", type=int, help=f"{entity_type.title()} ID")
        p.add_argument("--project-id", type=int, default=None, dest="project_id", help="Project ID to assign")
        p.add_argument("--client-id", type=int, default=None, dest="client_id", help="Client ID to assign")
        if entity_type == "file":
            p.add_argument("--status", default=None, help="File status (listed, unlisted, removed)")
        add_json_flag(p)
        p.set_defaults(func=_handle_data_single)

    # files — bulk CSV or folder-path assignment
    p = noun_subs.add_parser(
        "files",
        help="Bulk update files from CSV or assign under a folder",
        description=(
            "Bulk update files. Two modes:\n"
            "  CSV:    fp update files corrections.csv\n"
            "  Path:   fp update files --folder /path --project-id N"
        ),
        epilog=(
            "CSV mode:\n"
            "  Requires an 'id' column. Writable columns: status, project_id, client_id.\n"
            "  Empty values are skipped. Use '0' for project_id/client_id to clear to NULL.\n"
            "\n"
            "Path mode:\n"
            "  Assigns all files under the folder to the given project and/or client."
        ),
        formatter_class=FORMATTER,
    )
    p.add_argument("file", nargs="?", default=None, help="Path to CSV file")
    p.add_argument("--folder", default=None, help="Folder path to assign under")
    p.add_argument("--project-id", type=int, default=None, dest="project_id", help="Project ID to assign")
    p.add_argument("--client-id", type=int, default=None, dest="client_id", help="Client ID to assign")
    add_json_flag(p)
    p.set_defaults(func=_handle_bulk_dispatch, noun="files")

    # folders — bulk folder assignment
    p = noun_subs.add_parser(
        "folders",
        help="Bulk assign folders via folder path",
        description="Assign folders to projects/clients by folder path.",
        formatter_class=FORMATTER,
    )
    p.add_argument("--folder", required=True, help="Folder path to assign under")
    p.add_argument("--project-id", type=int, default=None, dest="project_id", help="Project ID to assign")
    p.add_argument("--client-id", type=int, default=None, dest="client_id", help="Client ID to assign")
    add_json_flag(p)
    p.set_defaults(func=_handle_bulk_folder_assign, noun="folders")
