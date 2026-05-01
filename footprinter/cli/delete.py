"""fp delete — soft-delete entity records via the service layer.

Routes ``fp delete client 42`` through the service layer's ``delete()``
function, which sets ``status='removed'``.  Requires confirmation unless
``--yes`` is passed.
"""

import sys

from footprinter.cli._common import (
    FORMATTER,
    add_json_flag,
    console,
    open_db,
    output_json,
)

# ---------------------------------------------------------------------------
# Entity dispatch table
# ---------------------------------------------------------------------------

#: Maps each deletable noun to (service_module, name_key).
DELETABLE_ENTITIES: dict[str, tuple[str, str]] = {
    "client": ("client_service", "name"),
    "project": ("project_service", "project_name"),
}

# ---------------------------------------------------------------------------
# Service resolution
# ---------------------------------------------------------------------------


def _get_service(service_name: str):
    """Lazy-import and return a service module from footprinter.services."""
    import footprinter.services as svc

    return getattr(svc, service_name)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _handle_delete(args) -> None:
    """Handle ``fp delete <noun> <id>``."""
    from footprinter.services.roles import Role

    noun = args.noun
    svc_name, name_key = DELETABLE_ENTITIES[noun]
    service = _get_service(svc_name)

    try:
        entity_id = int(args.id)
    except ValueError:
        console.print(f"[red]Invalid ID: {args.id!r} — expected an integer.[/red]")
        sys.exit(1)

    with open_db() as conn:
        record = service.get(conn, entity_id, role=Role.ADMIN)

        if record is None:
            console.print(f"[red]{noun.title()} {args.id} not found.[/red]")
            sys.exit(1)

        entity_name = record.get(name_key, "")

        if not args.yes:
            from footprinter.cli._prompt import SafeConfirm

            if not SafeConfirm.ask(
                f"Delete {noun} #{entity_id} ({entity_name})?",
                default=False,
            ):
                console.print("[dim]Cancelled.[/dim]")
                sys.exit(0)

        result = service.delete(conn, entity_id, role=Role.ADMIN)

    if getattr(args, "json", False):
        output_json(result)
    else:
        console.print(f"Deleted {noun} #{entity_id} ({entity_name}).")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``delete`` subcommand with noun sub-subparsers."""
    parser = subparsers.add_parser(
        "delete",
        help="Soft-delete a record",
        description="Soft-delete a record by setting status to 'removed'.",
        epilog=(
            "examples:\n"
            "  fp delete client 42          Delete client #42\n"
            "  fp delete project 7 --yes    Skip confirmation\n"
            "  fp delete client 1 --json    JSON output\n"
        ),
        formatter_class=FORMATTER,
    )
    noun_subs = parser.add_subparsers(
        dest="noun",
        metavar="NOUN",
        title="entity nouns (one required)",
    )
    parser.set_defaults(func=lambda args: parser.print_help())

    for noun in DELETABLE_ENTITIES:
        p = noun_subs.add_parser(
            noun,
            help=f"Delete a {noun}",
            description=f"Soft-delete a {noun} record by ID.",
            formatter_class=FORMATTER,
        )
        p.add_argument("id", help=f"{noun.title()} ID")
        p.add_argument(
            "--yes",
            "-y",
            action="store_true",
            default=False,
            help="Skip confirmation prompt",
        )
        add_json_flag(p)
        p.set_defaults(func=_handle_delete)
