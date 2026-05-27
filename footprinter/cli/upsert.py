"""fp upsert — create, update, or assign entity records.

Create/update (routes through ``service.upsert()``):
    ``fp upsert client --name Acme --type external``
    ``fp upsert clients data.csv``   (bulk CSV, tracked via IngestService)

Assign relationships (routes through ``service.assign()``):
    ``fp upsert file 42 --project-id 3``
    ``fp upsert files --folder /path --project-id 3``   (bulk path)
"""

import csv
import os
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
from footprinter.db.clients import VALID_STATUSES as VALID_CLIENT_STATUSES
from footprinter.db.projects import VALID_STATUSES as VALID_PROJECT_STATUSES

# ---------------------------------------------------------------------------
# Entity dispatch table
# ---------------------------------------------------------------------------

#: Maps recognised nouns to (service_module, entity_type, mode).
ENTITY_MAP: dict[str, tuple[str, str, str]] = {
    # singular → single record
    "client": ("client_service", "client", "single"),
    "project": ("project_service", "project", "single"),
    # plural → bulk CSV import
    "clients": ("client_service", "client", "bulk"),
    "projects": ("project_service", "project", "bulk"),
    # data entity singular → relationship assignment
    "file": ("file_service", "file", "assign"),
    "email": ("email_service", "email", "assign"),
    "chat": ("chat_service", "chat", "assign"),
    "visit": ("visit_service", "visit", "assign"),
    "folder": ("folder_service", "folder", "assign"),
    # data entity plural → bulk path assignment
    "files": ("file_service", "file", "bulk_assign"),
    "folders": ("folder_service", "folder", "bulk_assign"),
}

# ---------------------------------------------------------------------------
# Status validation — imported from db layer (single source of truth)
# ---------------------------------------------------------------------------

VALID_STATUSES_BY_ENTITY: dict[str, frozenset[str]] = {
    "client": VALID_CLIENT_STATUSES,
    "project": VALID_PROJECT_STATUSES,
}

# ---------------------------------------------------------------------------
# Per-entity argument specs for single mode
# ---------------------------------------------------------------------------

#: Each entry: (cli_flag, argparse_kwargs, service_kwarg_name)
SINGLE_ARGS: dict[str, list[tuple[str, dict, str]]] = {
    "client": [
        ("--name", {"required": True, "help": "Client name"}, "name"),
        (
            "--type",
            {"required": True, "help": "Client type (external, internal, personal)", "dest": "client_type"},
            "client_type",
        ),
        ("--path-pattern", {"default": None, "help": "Path pattern for client files"}, "path_pattern"),
        ("--status", {"default": None, "help": "Client status (listed, unlisted, removed)"}, "status"),
    ],
    "project": [
        ("--name", {"required": True, "help": "Project name", "dest": "project_name"}, "project_name"),
        ("--root-path", {"default": None, "help": "Project root path"}, "root_path"),
        ("--client-id", {"default": None, "type": int, "help": "Client ID"}, "client_id"),
        ("--project-type", {"default": None, "help": "Project type (python, node, etc.)"}, "project_type"),
        ("--description", {"default": None, "help": "Project description"}, "description"),
        ("--github-url", {"default": None, "help": "GitHub repository URL"}, "github_url"),
        (
            "--status",
            {
                "default": None,
                "help": "Project status (listed, unlisted, removed)",
            },
            "status",
        ),
    ],
}

# ---------------------------------------------------------------------------
# Per-entity CSV column specs for bulk mode
# ---------------------------------------------------------------------------

#: (required_columns, optional_columns, int_columns)
CSV_COLUMNS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "client": (
        ["name", "client_type"],
        ["slug", "path_pattern", "status"],
        [],
    ),
    "project": (
        ["project_name"],
        ["root_path", "client_id", "client", "project_type", "description", "github_url", "status"],
        ["client_id"],
    ),
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
    """Handle singular noun: ``fp upsert client --name X --type Y``."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)
    arg_specs = SINGLE_ARGS[entity_type]

    # Build kwargs from CLI flags
    kwargs: dict = {}
    for _flag, _ap_kwargs, svc_kwarg in arg_specs:
        val = getattr(args, svc_kwarg, None)
        if val is not None:
            kwargs[svc_kwarg] = val

    # Validate status against entity-specific allowed values
    if "status" in kwargs:
        valid = VALID_STATUSES_BY_ENTITY.get(entity_type)
        if valid and kwargs["status"] not in valid:
            console.print(
                f"[red]Invalid status '{kwargs['status']}' for {entity_type}. Valid: {', '.join(sorted(valid))}[/red]"
            )
            sys.exit(1)
        # Preserve audit trail when setting removed status
        if kwargs["status"] == "removed":
            kwargs["status_reason"] = "cli:upsert"

    with open_db() as conn:
        try:
            result = service.upsert(conn, role=Role.ADMIN, **kwargs)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

    if getattr(args, "json", False):
        output_json(result)
    else:
        action = result.get("action", "done")
        console.print(f"[green]{entity_type.title()} {result['id']} {action}.[/green]")


def _validate_and_read_csv(
    csv_path: Path,
    required_cols: list[str],
) -> list[dict]:
    """Read and validate CSV structure. Returns rows or exits on error."""
    if not csv_path.exists():
        console.print(f"[red]File not found: {csv_path}[/red]")
        sys.exit(1)

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            console.print("[red]Empty or invalid CSV file.[/red]")
            sys.exit(1)

        missing = set(required_cols) - set(reader.fieldnames)
        if missing:
            console.print(f"[red]Missing required columns: {', '.join(sorted(missing))}[/red]")
            sys.exit(1)

        return list(reader)


def _process_csv_rows(
    conn,
    rows: list[dict],
    service,
    entity_type: str,
    required_cols: list[str],
    optional_cols: list[str],
    int_cols: list[str],
) -> tuple[int, int, int, list[dict]]:
    """Process CSV rows through the service layer.

    Returns (created, updated, errors, error_details).
    """
    from footprinter.services.roles import Role

    created = 0
    updated = 0
    errors = 0
    error_details: list[dict] = []

    for i, row in enumerate(rows, 1):
        # Build service kwargs from CSV columns
        kwargs: dict = {}
        for col in required_cols + optional_cols:
            val = row.get(col)
            if val is not None and val != "":
                kwargs[col] = val

        # Coerce int columns
        row_bad = False
        for col in int_cols:
            if col in kwargs:
                try:
                    kwargs[col] = int(kwargs[col])
                except (ValueError, TypeError):
                    errors += 1
                    error_details.append(
                        {
                            "row": i,
                            "error": f"Invalid {col}: {kwargs[col]!r}",
                        }
                    )
                    row_bad = True
                    break
        if row_bad:
            continue

        # Skip if missing required columns (after filtering empty strings)
        missing_vals = [c for c in required_cols if c not in kwargs]
        if missing_vals:
            errors += 1
            error_details.append(
                {
                    "row": i,
                    "error": f"Missing required values: {', '.join(missing_vals)}",
                }
            )
            continue

        # Resolve client name → client_id for projects
        if entity_type == "project" and "client" in kwargs and "client_id" not in kwargs:
            from footprinter.db.clients import find_client_id_by_name

            client_name = kwargs.pop("client")
            resolved_id = find_client_id_by_name(conn, client_name)
            if resolved_id is None:
                errors += 1
                error_details.append(
                    {
                        "row": i,
                        "error": f"Client not found: {client_name!r}",
                    }
                )
                continue
            kwargs["client_id"] = resolved_id

        # Remove 'client' if both client and client_id were provided
        kwargs.pop("client", None)

        try:
            result = service.upsert(conn, role=Role.ADMIN, **kwargs)
            if result["action"] == "created":
                created += 1
            else:
                updated += 1
        except ValueError as e:
            errors += 1
            error_details.append({"row": i, "error": str(e)})

    return created, updated, errors, error_details


def _resolve_folder_by_path(
    conn: sqlite3.Connection, raw_path: str
) -> tuple[dict | None, str]:
    """Resolve a folder path string to a folder row dict.

    Returns (folder_row, resolved_path_str).  *resolved_path_str* is the
    absolute path that was looked up (useful for error messages).

    Resolution order:
    1. expanduser() to handle ~/
    2. If still relative, prepend Path.home() to make absolute
    3. Try exact match on folders.path
    4. If no match, strip the home prefix to derive relative_path
       (e.g. /Work/demo) and try folders.relative_path.  Non-home
       absolute paths fall through with a no-op fallback.
    """
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


def _resolve_folder_row(conn, row: dict, i: int) -> tuple[dict | None, dict | None]:
    """Resolve a folder CSV row to (kwargs, error).

    Returns (kwargs_dict, None) on success or (None, error_dict) on failure.
    kwargs_dict has folder_id, project_id, client_id ready for assign().
    """
    raw_path = row.get("folder_path", "").strip()
    if not raw_path:
        return None, {"row": i, "error": "Missing folder_path value"}

    folder_row, resolved = _resolve_folder_by_path(conn, raw_path)
    if folder_row is None:
        return None, {"row": i, "error": f"Folder not found: {resolved!r}"}

    project_id: int | None = None
    client_id: int | None = None

    raw_pid = row.get("project_id", "").strip()
    raw_pname = row.get("project_name", "").strip()
    raw_cid = row.get("client_id", "").strip()
    raw_cname = row.get("client_name", "").strip()

    if raw_pid:
        try:
            project_id = int(raw_pid)
        except (ValueError, TypeError):
            return None, {"row": i, "error": f"Invalid project_id: {raw_pid!r}"}
    elif raw_pname:
        from footprinter.db.projects import find_project_id_by_key

        project_id = find_project_id_by_key(conn, project_name=raw_pname)
        if project_id is None:
            return None, {"row": i, "error": f"Project not found: {raw_pname!r}"}

    if raw_cid:
        try:
            client_id = int(raw_cid)
        except (ValueError, TypeError):
            return None, {"row": i, "error": f"Invalid client_id: {raw_cid!r}"}
    elif raw_cname:
        from footprinter.db.clients import find_client_id_by_name

        client_id = find_client_id_by_name(conn, raw_cname)
        if client_id is None:
            return None, {"row": i, "error": f"Client not found: {raw_cname!r}"}

    if project_id is None and client_id is None:
        return None, {"row": i, "error": "No project or client specified"}

    return {
        "folder_id": folder_row["id"],
        "project_id": project_id,
        "client_id": client_id,
        "folder_row": folder_row,
    }, None


def _process_folder_csv_rows(
    conn,
    rows: list[dict],
    service,
) -> tuple[int, int, list[dict]]:
    """Process folder CSV rows through folder_service.assign().

    Returns (assigned, errors, error_details).
    """
    from footprinter.services.roles import Role

    assigned = 0
    errors = 0
    error_details: list[dict] = []

    for i, row in enumerate(rows, 1):
        kwargs, err = _resolve_folder_row(conn, row, i)
        if err or kwargs is None:
            errors += 1
            error_details.append(err or {"row": i, "error": "Unknown"})
            continue

        try:
            result = service.assign(
                conn,
                kwargs["folder_id"],
                role=Role.ADMIN,
                project_id=kwargs["project_id"],
                client_id=kwargs["client_id"],
            )
            if result is None:
                errors += 1
                error_details.append({"row": i, "error": "Folder not found during assign"})
            else:
                assigned += 1
        except (ValueError, PermissionError) as e:
            errors += 1
            error_details.append({"row": i, "error": str(e)})

    return assigned, errors, error_details


def _dry_run_folder_csv_rows(
    conn,
    rows: list[dict],
) -> tuple[int, int, int, list[dict]]:
    """Validate folder CSV rows without writing.

    Returns (would_assign, already_matched, errors, error_details).
    """
    would_assign = 0
    already_matched = 0
    errors = 0
    error_details: list[dict] = []

    for i, row in enumerate(rows, 1):
        kwargs, err = _resolve_folder_row(conn, row, i)
        if err or kwargs is None:
            errors += 1
            error_details.append(err or {"row": i, "error": "Unknown"})
            continue

        folder_row = kwargs["folder_row"]
        current_pid = folder_row.get("project_id")
        current_cid = folder_row.get("client_id")
        matches = True
        if kwargs["project_id"] is not None and current_pid != kwargs["project_id"]:
            matches = False
        if kwargs["client_id"] is not None and current_cid != kwargs["client_id"]:
            matches = False
        if matches:
            already_matched += 1
        else:
            would_assign += 1

    return would_assign, already_matched, errors, error_details


def _check_exists(conn, entity_type: str, kwargs: dict) -> bool:
    """Check whether a record matching *kwargs* already exists."""
    if entity_type == "client":
        from footprinter.db.clients import find_client_id_by_name

        return find_client_id_by_name(conn, kwargs.get("name", "")) is not None
    if entity_type == "project":
        from footprinter.db.projects import find_project_id_by_key

        return (
            find_project_id_by_key(
                conn,
                root_path=kwargs.get("root_path"),
                project_name=kwargs.get("project_name"),
            )
            is not None
        )
    return False


def _dry_run_csv_rows(
    conn,
    rows: list[dict],
    service,
    entity_type: str,
    required_cols: list[str],
    optional_cols: list[str],
    int_cols: list[str],
) -> tuple[int, int, int, list[dict]]:
    """Validate CSV rows without writing. Returns (would_create, would_update, errors, error_details)."""
    from footprinter.db.clients import VALID_CLIENT_TYPES

    would_create = 0
    would_update = 0
    errors = 0
    error_details: list[dict] = []

    for i, row in enumerate(rows, 1):
        kwargs: dict = {}
        for col in required_cols + optional_cols:
            val = row.get(col)
            if val is not None and val != "":
                kwargs[col] = val

        # Coerce int columns
        row_bad = False
        for col in int_cols:
            if col in kwargs:
                try:
                    kwargs[col] = int(kwargs[col])
                except (ValueError, TypeError):
                    errors += 1
                    error_details.append(
                        {
                            "row": i,
                            "error": f"Invalid {col}: {kwargs[col]!r}",
                        }
                    )
                    row_bad = True
                    break
        if row_bad:
            continue

        # Check required values
        missing_vals = [c for c in required_cols if c not in kwargs]
        if missing_vals:
            errors += 1
            error_details.append(
                {
                    "row": i,
                    "error": f"Missing required values: {', '.join(missing_vals)}",
                }
            )
            continue

        # Validate controlled values
        if entity_type == "client":
            ct = kwargs.get("client_type", "")
            if ct not in VALID_CLIENT_TYPES:
                errors += 1
                error_details.append(
                    {
                        "row": i,
                        "error": (
                            f"Invalid client_type: {ct!r}."
                            f" Must be one of: {', '.join(sorted(VALID_CLIENT_TYPES))}"
                        ),
                    }
                )
                continue

        # Probe existence
        if _check_exists(conn, entity_type, kwargs):
            would_update += 1
        else:
            would_create += 1

    return would_create, would_update, errors, error_details


def _handle_bulk(args) -> None:
    """Handle plural noun: ``fp upsert clients data.csv``.

    Default mode is dry-run (validate without writing). Pass ``--commit``
    to apply changes.
    """
    from footprinter.services.ingest_service import IngestService

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)
    required_cols, optional_cols, int_cols = CSV_COLUMNS[entity_type]
    csv_path = Path(args.file)
    has_dry_run = getattr(args, "dry_run", False)
    has_commit = getattr(args, "commit", False)
    if has_dry_run and has_commit:
        console.print("[red]Cannot use --dry-run and --commit together.[/red]")
        sys.exit(1)
    dry_run = not has_commit

    rows = _validate_and_read_csv(csv_path, required_cols)

    if not rows:
        if getattr(args, "json", False):
            output_json({"total": 0, "created": 0, "updated": 0, "errors": 0})
        else:
            console.print("[dim]No rows in CSV — nothing to do.[/dim]")
        return

    if dry_run:
        with open_db() as conn:
            would_create, would_update, errors, error_details = _dry_run_csv_rows(
                conn,
                rows,
                service,
                entity_type,
                required_cols,
                optional_cols,
                int_cols,
            )

        summary = {
            "total": would_create + would_update + errors,
            "would_create": would_create,
            "would_update": would_update,
            "errors": errors,
        }
        if error_details:
            summary["error_details"] = error_details

        if getattr(args, "json", False):
            output_json(summary)
        else:
            table = Table(title=f"Dry run — {noun}")
            table.add_column("Metric", style="cyan")
            table.add_column("Count", justify="right")
            table.add_row("Would create", str(would_create))
            table.add_row("Would update", str(would_update))
            table.add_row("Errors", str(errors))
            table.add_row("Total", str(would_create + would_update + errors))
            console.print(table)
            console.print("[dim]Pass --commit to apply these changes.[/dim]")
        return

    # Commit mode — write through service layer with ingest tracking
    pipe_name = f"upsert_{entity_type}"

    with open_db() as conn:
        ingest_svc = IngestService(conn)
        ingest_id = ingest_svc.begin(pipe_name, mode="bulk", trigger="cli:upsert")

        try:
            created, updated, errors, error_details = _process_csv_rows(
                conn,
                rows,
                service,
                entity_type,
                required_cols,
                optional_cols,
                int_cols,
            )

            ingest_svc.complete(
                ingest_id,
                result={
                    "items_processed": created + updated + errors,
                    "items_new": created,
                    "items_updated": updated,
                    "errors": errors,
                },
                metadata={"error_details": error_details} if error_details else None,
            )

        except Exception as e:
            ingest_svc.fail(ingest_id, error=str(e))
            console.print(f"[red]Bulk upsert failed: {e}[/red]")
            sys.exit(1)

    summary = {
        "total": created + updated + errors,
        "created": created,
        "updated": updated,
        "errors": errors,
    }
    if error_details:
        summary["error_details"] = error_details

    if getattr(args, "json", False):
        output_json(summary)
    else:
        table = Table(title=f"Upsert {noun}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Created", str(created))
        table.add_row("Updated", str(updated))
        table.add_row("Errors", str(errors))
        table.add_row("Total", str(created + updated + errors))
        console.print(table)


def _handle_assign(args) -> None:
    """Handle data entity noun: ``fp upsert file 42 --project-id 3``."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)

    entity_id = args.id
    project_id = getattr(args, "project_id", None)
    client_id = getattr(args, "client_id", None)

    if project_id is None and client_id is None:
        console.print("[red]At least one of --project-id or --client-id is required.[/red]")
        sys.exit(1)

    with open_db() as conn:
        try:
            result = service.assign(
                conn,
                entity_id,
                role=Role.ADMIN,
                project_id=project_id,
                client_id=client_id,
            )
        except (ValueError, PermissionError) as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

    if result is None:
        console.print(f"[red]{entity_type.title()} {entity_id} not found.[/red]")
        sys.exit(1)

    if getattr(args, "json", False):
        output_json(result)
    else:
        parts = []
        if project_id is not None:
            parts.append(f"project {project_id}")
        if client_id is not None:
            parts.append(f"client {client_id}")
        console.print(f"[green]{entity_type.title()} {entity_id} assigned to {' and '.join(parts)}.[/green]")


def _handle_bulk_assign(args) -> None:
    """Handle bulk path-based assignment for files and folders.

    Files: iterates files under a folder path via ``service.assign()``.
    Folders: cascades project/client via ``cascade_project_id`` /
    ``cascade_client_id`` in the db layer.
    """
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)

    raw_folder = args.folder.strip()
    folder_path = Path(raw_folder).expanduser()
    if not folder_path.is_absolute():
        folder_path = Path.home() / folder_path
    folder_path = str(folder_path).rstrip("/")
    project_id = getattr(args, "project_id", None)
    client_id = getattr(args, "client_id", None)

    if project_id is None and client_id is None:
        console.print("[red]At least one of --project-id or --client-id is required.[/red]")
        sys.exit(1)

    project_assigned = 0
    client_assigned = 0
    files_touched = 0

    with open_db() as conn:
        try:
            if entity_type == "file":
                from footprinter.db.files import list_file_ids_under_path

                file_ids = list_file_ids_under_path(conn, folder_path)
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
                # assign() is atomic — one call sets both fields per file
                if project_id is not None:
                    project_assigned = files_touched
                if client_id is not None:
                    client_assigned = files_touched
            elif entity_type == "folder":
                from footprinter.db.folders import (
                    cascade_client_id,
                    cascade_project_id,
                )

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

    # assigned = unique entities touched (not total field-writes).
    # Both cascades walk the same tree, so max() avoids double-counting.
    if entity_type == "file":
        assigned = files_touched
    else:
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
            f"[green]Project assigned to {project_assigned} {entity_type}(s). "
            f"Client assigned to {client_assigned} {entity_type}(s).[/green]"
        )
    else:
        console.print(f"[green]{assigned} {entity_type}(s) assigned.[/green]")


def _handle_folder_csv(args) -> None:
    """Handle CSV-based folder-to-project/client assignment."""
    from footprinter.services.ingest_service import IngestService

    service = _get_service("folder_service")
    csv_path = Path(args.file)
    has_dry_run = getattr(args, "dry_run", False)
    has_commit = getattr(args, "commit", False)
    if has_dry_run and has_commit:
        console.print("[red]Cannot use --dry-run and --commit together.[/red]")
        sys.exit(1)
    dry_run = not has_commit

    rows = _validate_and_read_csv(csv_path, ["folder_path"])

    if not rows:
        if getattr(args, "json", False):
            output_json({"total": 0, "assigned": 0, "errors": 0})
        else:
            console.print("[dim]No rows in CSV — nothing to do.[/dim]")
        return

    if dry_run:
        with open_db() as conn:
            would_assign, already_matched, errors, error_details = _dry_run_folder_csv_rows(
                conn, rows,
            )

        summary: dict = {
            "total": would_assign + already_matched + errors,
            "would_assign": would_assign,
            "already_matched": already_matched,
            "errors": errors,
        }
        if error_details:
            summary["error_details"] = error_details

        if getattr(args, "json", False):
            output_json(summary)
        else:
            table = Table(title="Dry run — folder assignments")
            table.add_column("Metric", style="cyan")
            table.add_column("Count", justify="right")
            table.add_row("Would assign", str(would_assign))
            table.add_row("Already matched", str(already_matched))
            table.add_row("Errors", str(errors))
            table.add_row("Total", str(would_assign + already_matched + errors))
            console.print(table)
            console.print("[dim]Pass --commit to apply these changes.[/dim]")
        return

    with open_db() as conn:
        ingest_svc = IngestService(conn)
        ingest_id = ingest_svc.begin("upsert_folder_assign", mode="bulk", trigger="cli:upsert")

        try:
            assigned, errors, error_details = _process_folder_csv_rows(
                conn, rows, service,
            )

            ingest_svc.complete(
                ingest_id,
                result={
                    "items_processed": assigned + errors,
                    "items_new": assigned,
                    "items_updated": 0,
                    "errors": errors,
                },
                metadata={"error_details": error_details} if error_details else None,
            )

        except Exception as e:
            ingest_svc.fail(ingest_id, error=str(e))
            console.print(f"[red]Folder CSV import failed: {e}[/red]")
            sys.exit(1)

    summary = {
        "total": assigned + errors,
        "assigned": assigned,
        "errors": errors,
    }
    if error_details:
        summary["error_details"] = error_details

    if getattr(args, "json", False):
        output_json(summary)
    else:
        table = Table(title="Upsert folder assignments")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Assigned", str(assigned))
        table.add_row("Errors", str(errors))
        table.add_row("Total", str(assigned + errors))
        console.print(table)


def _handle_folders_dispatch(args) -> None:
    """Route folders subcommand to CSV or legacy --folder path."""
    csv_file = getattr(args, "file", None)
    folder_flag = getattr(args, "folder", None)

    if csv_file and folder_flag:
        console.print("[red]Cannot use both a CSV file and --folder.[/red]")
        sys.exit(1)

    if csv_file:
        _handle_folder_csv(args)
    elif folder_flag:
        _handle_bulk_assign(args)
    else:
        console.print("[red]Provide a CSV file or use --folder.[/red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``upsert`` subcommand with noun sub-subparsers."""
    parser = subparsers.add_parser(
        "upsert",
        help="Create or update entity records",
        description=(
            "Create or update records, or assign relationships.\n\n"
            "Single:  fp upsert client --name Acme --type external\n"
            "Bulk:    fp upsert clients data.csv\n"
            "Assign:  fp upsert file 42 --project-id 3\n"
            "Bulk assign: fp upsert files --folder ~/Work/acme/ --project-id 3"
        ),
        epilog=(
            "examples:\n"
            "  fp upsert client --name Acme --type external     Create or update a client\n"
            "  fp upsert project --name my-proj                  Create or update a project\n"
            "  fp upsert clients data.csv                        Bulk import clients from CSV\n"
            "  fp upsert file 42 --project-id 3                  Assign file to project\n"
            "  fp upsert email 10 --client-id 1                  Assign email to client\n"
            "  fp upsert files --folder ~/Work/acme --project-id 3  Assign files under folder\n"
            "\n"
            "entity nouns:\n"
            "  create/update: client, project, clients, projects\n"
            "  assign:        file, email, chat, visit, folder\n"
            "  bulk assign:   files, folders\n"
            "\n"
            "tip: use 'fp upsert <noun> --help' for details on any noun."
        ),
        formatter_class=FORMATTER,
    )
    noun_subs = parser.add_subparsers(
        dest="noun",
        metavar="NOUN",
        title="entity nouns (one required)",
    )
    parser.set_defaults(func=lambda args: parser.print_help())

    # Singular nouns — per-entity CLI flags
    for noun in ["client", "project"]:
        entity_type = ENTITY_MAP[noun][1]
        p = noun_subs.add_parser(
            noun,
            help=f"Create or update a single {entity_type}",
            description=f"Upsert a single {entity_type} record from CLI flags.",
            formatter_class=FORMATTER,
        )
        for flag, ap_kwargs, _svc_kwarg in SINGLE_ARGS[entity_type]:
            p.add_argument(flag, **ap_kwargs)
        add_json_flag(p)
        p.set_defaults(func=_handle_single)

    # Plural nouns — CSV file argument
    _BULK_EPILOGS = {
        "clients": (
            "CSV columns:\n"
            "  required: name, client_type\n"
            "  optional: slug, path_pattern, status\n"
            "\n"
            "  client_type values: external, internal, personal\n"
            "  status values:      listed (default), unlisted, removed\n"
            "\n"
            "example CSV:\n"
            "  name,client_type,path_pattern\n"
            "  Acme Corp,external,/Work/acme\n"
            "  Internal Tools,internal,\n"
            "\n"
            "modes:\n"
            "  Default is dry-run (validate only). Pass --commit to write.\n"
            "  Existing records (matched by name) are updated, new ones created."
        ),
        "projects": (
            "CSV columns:\n"
            "  required: project_name\n"
            "  optional: root_path, client_id, client, project_type,\n"
            "            description, github_url, status\n"
            "\n"
            "  client: client name (resolved to client_id)\n"
            "  status values: listed (default), unlisted, removed\n"
            "\n"
            "example CSV:\n"
            "  project_name,client,project_type,root_path\n"
            "  my-api,Acme Corp,python,/Work/acme/api\n"
            "  docs-site,,node,/Work/docs\n"
            "\n"
            "modes:\n"
            "  Default is dry-run (validate only). Pass --commit to write.\n"
            "  Existing records (matched by root_path or project_name) are\n"
            "  updated, new ones created."
        ),
    }
    for noun in ["clients", "projects"]:
        entity_type = ENTITY_MAP[noun][1]
        p = noun_subs.add_parser(
            noun,
            help=f"Bulk import {noun} from CSV",
            description=f"Bulk import {noun} from a CSV file.",
            epilog=_BULK_EPILOGS[noun],
            formatter_class=FORMATTER,
        )
        p.add_argument("file", help="Path to CSV file")
        p.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Validate and preview changes without writing (default behavior)",
        )
        p.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Apply validated changes to the database",
        )
        add_json_flag(p)
        p.set_defaults(func=_handle_bulk)

    # Data entity singular nouns — relationship assignment
    for noun in ["file", "email", "chat", "visit", "folder"]:
        entity_type = ENTITY_MAP[noun][1]
        p = noun_subs.add_parser(
            noun,
            help=f"Assign a {entity_type} to a project or client",
            description=f"Assign a {entity_type} to a project and/or client by ID.",
            formatter_class=FORMATTER,
        )
        p.add_argument("id", type=int, help=f"{entity_type.title()} ID")
        p.add_argument("--project-id", type=int, default=None, dest="project_id", help="Project ID to assign")
        p.add_argument("--client-id", type=int, default=None, dest="client_id", help="Client ID to assign")
        add_json_flag(p)
        p.set_defaults(func=_handle_assign)

    # Data entity plural nouns — bulk path assignment (files only)
    p = noun_subs.add_parser(
        "files",
        help="Bulk assign files under a folder",
        description="Assign all files under a folder to a project and/or client.",
        formatter_class=FORMATTER,
    )
    p.add_argument("--folder", required=True, help="Folder path to assign under")
    p.add_argument("--project-id", type=int, default=None, dest="project_id", help="Project ID to assign")
    p.add_argument("--client-id", type=int, default=None, dest="client_id", help="Client ID to assign")
    add_json_flag(p)
    p.set_defaults(func=_handle_bulk_assign)

    # Folders — CSV import OR legacy --folder path
    p = noun_subs.add_parser(
        "folders",
        help="Assign folders via CSV or under a folder path",
        description=(
            "Assign folders to projects/clients. Two modes:\n"
            "  CSV:    fp upsert folders assignments.csv [--commit]\n"
            "  Path:   fp upsert folders --folder /path --project-id N\n"
        ),
        epilog=(
            "CSV columns:\n"
            "  required: folder_path\n"
            "  optional: project_name, project_id, client_name, client_id\n"
            "\n"
            "  Provide project by name OR id (id takes precedence).\n"
            "  Same for client.\n"
            "\n"
            "example CSV:\n"
            "  folder_path,project_name\n"
            "  /Work/acme/docs,Acme Docs\n"
            "  /Work/internal/api,Internal API\n"
            "\n"
            "modes:\n"
            "  Default is dry-run (validate only). Pass --commit to write."
        ),
        formatter_class=FORMATTER,
    )
    p.add_argument("file", nargs="?", default=None, help="Path to CSV file for bulk folder assignment")
    p.add_argument("--folder", default=None, help="Folder path to assign under (legacy mode)")
    p.add_argument("--project-id", type=int, default=None, dest="project_id", help="Project ID to assign")
    p.add_argument("--client-id", type=int, default=None, dest="client_id", help="Client ID to assign")
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate and preview changes without writing (default behavior, CSV mode)",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Apply validated changes to the database (CSV mode)",
    )
    add_json_flag(p)
    p.set_defaults(func=_handle_folders_dispatch)
