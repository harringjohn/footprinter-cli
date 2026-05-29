"""fp add — create-only entity records.

Create super entities (client, project) or data entities from CSV.
Errors if a record already exists — clean separation from ``fp update``.

Single:     ``fp add client --name Acme --type external``
Bulk CSV:   ``fp add clients data.csv``
Data CSV:   ``fp add files data.csv``
Chat import: ``fp add chats export.zip``
"""

import csv
import importlib
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
from footprinter.cli.upsert import (
    CSV_COLUMNS,
    SINGLE_ARGS,
    VALID_STATUSES_BY_ENTITY,
    _check_exists,
    _validate_and_read_csv,
)
from footprinter.services.ingest_service import IngestService

# ---------------------------------------------------------------------------
# Entity dispatch table
# ---------------------------------------------------------------------------

ENTITY_MAP: dict[str, tuple[str, str, str]] = {
    # singular → single record creation
    "client": ("client_service", "client", "single"),
    "project": ("project_service", "project", "single"),
    # plural → bulk CSV creation (super entities)
    "clients": ("client_service", "client", "bulk"),
    "projects": ("project_service", "project", "bulk"),
    # data entity plural → bulk CSV creation (data entities)
    "files": ("", "file", "data_bulk"),
    "emails": ("", "email", "data_bulk"),
    "messages": ("", "message", "data_bulk"),
    "visits": ("", "visit", "data_bulk"),
    "folders": ("", "folder", "data_bulk"),
    # chat archive import
    "chats": ("", "chat", "chat_import"),
}

# ---------------------------------------------------------------------------
# Data entity CSV specs
# ---------------------------------------------------------------------------

DATA_CSV_SPECS: dict[str, tuple[list[str], list[str], str, str]] = {
    # noun → (required_cols, optional_cols, module_path, function_name)
    "files": (
        ["file_path", "file_name"],
        ["source", "content_type", "file_type", "mime_type", "size_bytes",
         "sha256_hash", "md5_hash", "created_at", "modified_at", "content_preview"],
        "footprinter.db.files",
        "insert_file",
    ),
    "emails": (
        ["message_id", "thread_id", "account", "received_at"],
        ["from_address", "from_name", "to_addresses", "cc_addresses",
         "subject", "body_preview", "labels", "has_attachments", "is_read"],
        "footprinter.db.emails",
        "insert_email",
    ),
    "messages": (
        ["chat_id", "role"],
        ["message_id", "content", "created_at", "status"],
        "footprinter.db.chats",
        "insert_message",
    ),
    "visits": (
        ["url", "visit_time", "browser"],
        ["title", "visit_count"],
        "footprinter.db.browser",
        "insert_visit",
    ),
    "folders": (
        ["source", "external_id", "path", "relative_path", "name", "account", "web_link"],
        [],
        "footprinter.db.folders",
        "insert_drive_folder",
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
# Insert function resolution
# ---------------------------------------------------------------------------


def _get_insert_fn(module_path: str, fn_name: str):
    """Lazy-import and return a DB insert function."""
    mod = importlib.import_module(module_path)
    return getattr(mod, fn_name)


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------


def _normalize_insert_result(result) -> tuple[str, int | None]:
    """Normalize heterogeneous DB insert function return values."""
    if result is None:
        return ("error", None)
    if result is False:
        return ("duplicate", None)
    if isinstance(result, tuple):
        action, entity_id = result
        return (action, entity_id)
    if isinstance(result, int):
        return ("created", result)
    return ("unknown", None)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_single(args) -> None:
    """Handle singular noun: ``fp add client --name X --type Y``."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)
    arg_specs = SINGLE_ARGS[entity_type]

    kwargs: dict = {}
    for _flag, _ap_kwargs, svc_kwarg in arg_specs:
        val = getattr(args, svc_kwarg, None)
        if val is not None:
            kwargs[svc_kwarg] = val

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
            kwargs["status_reason"] = "cli:add"

    with open_db() as conn:
        if _check_exists(conn, entity_type, kwargs):
            name = kwargs.get("name", "")
            if getattr(args, "json", False):
                output_json({"error": f"{entity_type.title()} '{name}' already exists"})
            else:
                console.print(f"[red]{entity_type.title()} '{name}' already exists.[/red]")
            sys.exit(1)

        try:
            result = service.upsert(conn, role=Role.ADMIN, **kwargs)
        except ValueError as e:
            if getattr(args, "json", False):
                output_json({"error": str(e)})
            else:
                console.print(f"[red]{e}[/red]")
            sys.exit(1)

    if getattr(args, "json", False):
        output_json(result)
    else:
        action = result.get("action", "done")
        console.print(f"[green]{entity_type.title()} {result['id']} {action}.[/green]")


def _handle_bulk(args) -> None:
    """Handle plural noun: ``fp add clients data.csv``.

    Create-only: rows that match existing records are counted as errors.
    """
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, entity_type, _mode = ENTITY_MAP[noun]
    service = _get_service(svc_name)
    required_cols, optional_cols, int_cols = CSV_COLUMNS[entity_type]
    csv_path = Path(args.file)

    rows = _validate_and_read_csv(csv_path, required_cols)

    if not rows:
        if getattr(args, "json", False):
            output_json({"total": 0, "created": 0, "errors": 0})
        else:
            console.print("[dim]No rows in CSV — nothing to do.[/dim]")
        return

    pipe_name = f"add_{entity_type}"
    created = 0
    errors = 0
    error_details: list[dict] = []

    with open_db() as conn:
        ingest_svc = IngestService(conn)
        ingest_id = ingest_svc.begin(pipe_name, mode="bulk", trigger="cli:add")

        try:
            for i, row in enumerate(rows, 1):
                kwargs: dict = {}
                for col in required_cols + optional_cols:
                    val = row.get(col)
                    if val is not None and val != "":
                        kwargs[col] = val

                row_bad = False
                for col in int_cols:
                    if col in kwargs:
                        try:
                            kwargs[col] = int(kwargs[col])
                        except (ValueError, TypeError):
                            errors += 1
                            error_details.append({"row": i, "error": f"Invalid {col}: {kwargs[col]!r}"})
                            row_bad = True
                            break
                if row_bad:
                    continue

                missing_vals = [c for c in required_cols if c not in kwargs]
                if missing_vals:
                    errors += 1
                    error_details.append({"row": i, "error": f"Missing required values: {', '.join(missing_vals)}"})
                    continue

                if _check_exists(conn, entity_type, kwargs):
                    errors += 1
                    name = kwargs.get("name", f"row {i}")
                    error_details.append({"row": i, "error": f"{entity_type.title()} '{name}' already exists"})
                    continue

                kwargs.pop("client", None)
                kwargs.pop("slug", None)

                try:
                    result = service.upsert(conn, role=Role.ADMIN, **kwargs)
                    created += 1
                except ValueError as e:
                    errors += 1
                    error_details.append({"row": i, "error": str(e)})

            ingest_svc.complete(
                ingest_id,
                result={
                    "items_processed": created + errors,
                    "items_new": created,
                    "errors": errors,
                },
                metadata={"error_details": error_details} if error_details else None,
            )

        except Exception as e:
            ingest_svc.fail(ingest_id, error=str(e))
            console.print(f"[red]Bulk add failed: {e}[/red]")
            sys.exit(1)

    summary: dict = {
        "total": created + errors,
        "created": created,
        "errors": errors,
    }
    if error_details:
        summary["error_details"] = error_details

    if getattr(args, "json", False):
        output_json(summary)
    else:
        table = Table(title=f"Add {noun}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Created", str(created))
        table.add_row("Errors", str(errors))
        table.add_row("Total", str(created + errors))
        console.print(table)


def _handle_data_bulk(args) -> None:
    """Handle data entity plural noun: ``fp add files data.csv``.

    Routes CSV rows to the appropriate DB insert function.
    """
    noun = args.noun
    required_cols, optional_cols, mod_path, fn_name = DATA_CSV_SPECS[noun]
    csv_path = Path(args.file)

    rows = _validate_and_read_csv(csv_path, required_cols)

    if not rows:
        if getattr(args, "json", False):
            output_json({"total": 0, "created": 0, "errors": 0})
        else:
            console.print("[dim]No rows in CSV — nothing to do.[/dim]")
        return

    insert_fn = _get_insert_fn(mod_path, fn_name)
    pipe_name = f"add_{noun}"
    created = 0
    errors = 0
    error_details: list[dict] = []

    with open_db() as conn:
        ingest_svc = IngestService(conn)
        ingest_id = ingest_svc.begin(pipe_name, mode="bulk", trigger="cli:add")

        try:
            for i, row in enumerate(rows, 1):
                data: dict = {}
                for col in required_cols + optional_cols:
                    val = row.get(col)
                    if val is not None and val != "":
                        data[col] = val

                missing_vals = [c for c in required_cols if c not in data]
                if missing_vals:
                    errors += 1
                    error_details.append({"row": i, "error": f"Missing required values: {', '.join(missing_vals)}"})
                    continue

                try:
                    result = insert_fn(conn, data)
                    action, _entity_id = _normalize_insert_result(result)
                    if action in ("inserted", "created"):
                        created += 1
                    elif action == "duplicate":
                        errors += 1
                        error_details.append({"row": i, "error": "Duplicate record"})
                    elif action == "error":
                        errors += 1
                        error_details.append({"row": i, "error": "Insert returned None"})
                    else:
                        created += 1
                except Exception as e:
                    errors += 1
                    error_details.append({"row": i, "error": str(e)})

            ingest_svc.complete(
                ingest_id,
                result={
                    "items_processed": created + errors,
                    "items_new": created,
                    "errors": errors,
                },
                metadata={"error_details": error_details} if error_details else None,
            )

        except Exception as e:
            ingest_svc.fail(ingest_id, error=str(e))
            console.print(f"[red]Bulk add failed: {e}[/red]")
            sys.exit(1)

    summary: dict = {
        "total": created + errors,
        "created": created,
        "errors": errors,
    }
    if error_details:
        summary["error_details"] = error_details

    if getattr(args, "json", False):
        output_json(summary)
    else:
        table = Table(title=f"Add {noun}")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Created", str(created))
        table.add_row("Errors", str(errors))
        table.add_row("Total", str(created + errors))
        console.print(table)


def _handle_chat_import(args) -> None:
    """Handle ``fp add chats export.zip`` — delegate to ChatIndexer.upload()."""
    from footprinter.ingest.chat_indexer import ChatIndexer
    from footprinter.ingest.database import Database
    from footprinter.paths import get_db_path

    quiet = getattr(args, "quiet", False)

    try:
        db = Database(str(get_db_path()))
        manager = ChatIndexer(db)
        result = manager.upload(Path(args.file), console=None if quiet else console)

        status = result.get("status", "unknown")
        if not quiet:
            if status == "duplicate":
                prev = result.get("previous_upload", {})
                console.print(
                    f"[yellow]Already imported[/yellow] (uploaded {prev.get('uploaded_at', 'unknown')})"
                )
            else:
                added = result.get("chats_added", 0)
                updated = result.get("chats_updated", 0)
                messages = result.get("messages_imported", 0)
                errs = result.get("errors", 0)
                console.print(
                    f"[green]Imported[/green] {added + updated} chats"
                    f" ({added} new, {updated} updated), {messages} messages"
                )
                if errs:
                    console.print(f"[yellow]Warning:[/yellow] {errs} chats failed to import")
    except Exception as e:
        if not quiet:
            console.print(f"[red]Import failed:[/red] {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``add`` subcommand with noun sub-subparsers."""
    parser = subparsers.add_parser(
        "add",
        help="Create new entity records",
        description=(
            "Create new records. Errors if a record already exists.\n\n"
            "Single:      fp add client --name Acme --type external\n"
            "Bulk CSV:    fp add clients data.csv\n"
            "Data CSV:    fp add files data.csv\n"
            "Chat import: fp add chats export.zip"
        ),
        epilog=(
            "examples:\n"
            "  fp add client --name Acme --type external     Create a client\n"
            "  fp add project --name my-proj                  Create a project\n"
            "  fp add clients data.csv                        Bulk create clients from CSV\n"
            "  fp add files data.csv                          Bulk create file records from CSV\n"
            "  fp add chats export.zip                        Import chat archive\n"
            "\n"
            "entity nouns:\n"
            "  create:      client, project\n"
            "  bulk create: clients, projects\n"
            "  data CSV:    files, emails, messages, visits, folders\n"
            "  import:      chats\n"
            "\n"
            "tip: use 'fp add <noun> --help' for details on any noun."
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
            help=f"Create a new {entity_type}",
            description=f"Create a new {entity_type} record from CLI flags. Errors if it already exists.",
            formatter_class=FORMATTER,
        )
        for flag, ap_kwargs, _svc_kwarg in SINGLE_ARGS[entity_type]:
            p.add_argument(flag, **ap_kwargs)
        add_json_flag(p)
        p.set_defaults(func=_handle_single)

    # Plural super entity nouns — CSV file argument
    _BULK_EPILOGS = {
        "clients": (
            "CSV columns:\n"
            "  required: name, client_type\n"
            "  optional: slug, status\n"
            "\n"
            "  slug is auto-derived from name; any value supplied is ignored.\n"
            "  client_type values: external, internal, personal\n"
            "  status values:      listed (default), unlisted, removed\n"
            "\n"
            "example CSV:\n"
            "  name,client_type\n"
            "  Acme Corp,external\n"
            "  Internal Tools,internal\n"
            "\n"
            "Rows matching existing records (by name) are counted as errors."
        ),
        "projects": (
            "CSV columns:\n"
            "  required: name\n"
            "  optional: client_id, client, description, status\n"
            "\n"
            "  client: client name (resolved to client_id)\n"
            "  status values: listed (default), unlisted, removed\n"
            "\n"
            "example CSV:\n"
            "  name,client,description\n"
            "  my-api,Acme Corp,Acme public API\n"
            "  docs-site,,Documentation site\n"
            "\n"
            "Rows matching existing records (by name) are counted as errors."
        ),
    }
    for noun in ["clients", "projects"]:
        entity_type = ENTITY_MAP[noun][1]
        p = noun_subs.add_parser(
            noun,
            help=f"Bulk create {noun} from CSV",
            description=f"Bulk create {noun} from a CSV file. Existing records are errors, not updates.",
            epilog=_BULK_EPILOGS[noun],
            formatter_class=FORMATTER,
        )
        p.add_argument("file", help="Path to CSV file")
        add_json_flag(p)
        p.set_defaults(func=_handle_bulk)

    # Data entity plural nouns — CSV file argument
    _DATA_EPILOGS = {
        "files": (
            "CSV columns:\n"
            "  required: file_path, file_name\n"
            "  optional: source, content_type, file_type, mime_type, size_bytes,\n"
            "            sha256_hash, md5_hash, created_at, modified_at, content_preview"
        ),
        "emails": (
            "CSV columns:\n"
            "  required: message_id, thread_id, account, received_at\n"
            "  optional: from_address, from_name, to_addresses, cc_addresses,\n"
            "            subject, body_preview, labels, has_attachments, is_read"
        ),
        "messages": (
            "CSV columns:\n"
            "  required: chat_id, role\n"
            "  optional: message_id, content, created_at, status"
        ),
        "visits": (
            "CSV columns:\n"
            "  required: url, visit_time, browser\n"
            "  optional: title, visit_count"
        ),
        "folders": (
            "CSV columns:\n"
            "  required: source, external_id, path, relative_path, name, account, web_link"
        ),
    }
    for noun in ["files", "emails", "messages", "visits", "folders"]:
        p = noun_subs.add_parser(
            noun,
            help=f"Bulk create {noun} records from CSV",
            description=f"Bulk create {noun} records from a CSV file.",
            epilog=_DATA_EPILOGS.get(noun, ""),
            formatter_class=FORMATTER,
        )
        p.add_argument("file", help="Path to CSV file")
        add_json_flag(p)
        p.set_defaults(func=_handle_data_bulk)

    # Chat archive import
    p = noun_subs.add_parser(
        "chats",
        help="Import a chat archive (zip or directory)",
        description="Import a chat archive from a zip file or extracted directory.",
        formatter_class=FORMATTER,
    )
    p.add_argument("file", help="Path to chat export (.zip or directory)")
    p.add_argument("--quiet", action="store_true", default=False, help="Suppress output")
    p.set_defaults(func=_handle_chat_import)
