"""fp status — show data counts and system health.

Delegates to the existing functions in ``footprinter.cli.status`` so the
standalone ``fp status`` command and this router entry share one code path.
"""

from footprinter.cli._common import add_json_flag, console, output_json
from footprinter.cli.status import (
    get_data_counts,
    get_source_health,
    print_status,
)
from footprinter.paths import get_config_path, get_db_path
from footprinter.source_registry import get_config


def register(subparsers) -> None:
    """Register ``fp status`` on the CLI router."""
    from footprinter.cli._common import FORMATTER

    parser = subparsers.add_parser(
        "status",
        help="Show data counts and system health",
        description=(
            "Display data counts, database info, and source health.\n"
            "Shows a summary of all indexed content and connector status."
        ),
        epilog=(
            "examples:\n"
            "  fp status                  Full status overview\n"
            "  fp status --last-run       Details from the last pipeline run\n"
            "  fp status --json           Machine-readable output"
        ),
        formatter_class=FORMATTER,
    )
    parser.add_argument(
        "--last-run",
        action="store_true",
        help="Show details from the last pipeline run",
    )
    add_json_flag(parser)
    parser.set_defaults(func=_handle)


def _handle(args) -> None:
    """Route ``fp status`` to the appropriate handler."""
    # --last-run takes priority over everything
    if getattr(args, "last_run", False):
        from footprinter.cli.status import print_last_run
        from footprinter.ingest.run_record import load_run_record

        print_last_run(load_run_record())
        return

    db_path = get_db_path()
    config_path = get_config_path()

    # Full status — build structured data dict
    data: dict = {
        "database": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "size_mb": (round(db_path.stat().st_size / 1024 / 1024, 1) if db_path.exists() else 0),
        },
        "config": {
            "path": str(config_path),
            "exists": config_path.exists(),
        },
    }

    if not db_path.exists():
        if getattr(args, "json", False):
            data["counts"] = {}
            data["health"] = {}
            data["last_run"] = None
            output_json(data)
        else:
            from rich.panel import Panel

            console.print(
                Panel(
                    f"No database found at [cyan]{db_path}[/cyan]\nRun [bold]fp ingest[/bold] to start indexing.",
                    title="Footprinter Status",
                    expand=False,
                )
            )
        return

    try:
        config = get_config()
    except Exception:
        config = None

    counts = get_data_counts(db_path)
    health = get_source_health(config)

    data["counts"] = counts
    data["health"] = health
    data["last_run"] = counts.get("last_run")

    if getattr(args, "json", False):
        output_json(data)
    else:
        print_status(data, health)
