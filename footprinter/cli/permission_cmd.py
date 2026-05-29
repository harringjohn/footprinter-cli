"""fp permission — access policy commands.

Subcommands:
    fp permission recalculate [scope]   Re-resolve access stamps from the policy chain
"""

import time

from footprinter.cli._common import FORMATTER, console
from footprinter.cli._policy_helpers import (
    get_policy_db,
    recalculate_with_progress,
)


def _print_stats(stats: dict[str, int], elapsed: float) -> None:
    if not stats:
        return
    parts = [
        f"{count} {etype}{'s' if count != 1 else ''}"
        for etype, count in stats.items()
        if count
    ]
    if parts:
        console.print(
            f"  [dim]Recalculated: {', '.join(parts)} in {elapsed:.1f}s[/dim]"
        )


def _recalculate(args) -> None:
    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)
    try:
        scope = getattr(args, "scope", "global") or "global"
        t0 = time.monotonic()
        stats = recalculate_with_progress(conn, scope)
        elapsed = time.monotonic() - t0
        _print_stats(stats, elapsed)
    finally:
        conn.close()


def register(subparsers) -> None:
    """Register the ``permission`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "permission",
        help="Access policy commands",
        description=(
            "Manage access control policies.\n\n"
            "Use recalculate to re-resolve access stamps from the policy chain."
        ),
        epilog=(
            "examples:\n"
            "  fp permission recalculate              Full recalculation (global scope)\n"
            "  fp permission recalculate folder:~/Work Scoped to a folder\n"
            "\n"
            "tip: use 'fp permission <command> --help' for details."
        ),
        formatter_class=FORMATTER,
    )
    parser.set_defaults(func=lambda args: parser.print_help())

    sub = parser.add_subparsers(
        dest="permission_command",
        metavar="COMMAND",
        title="commands (one required)",
    )

    recalc_parser = sub.add_parser(
        "recalculate",
        help="Re-resolve access stamps from the policy chain",
        description=(
            "Re-resolve access stamps from the policy chain.\n\n"
            "Recalculation is non-destructive — it reads the current policy\n"
            "chain and updates cached access columns to match."
        ),
        epilog=(
            "examples:\n"
            "  fp permission recalculate              Global scope (all entities)\n"
            "  fp permission recalculate folder:~/Work Scoped to folder\n"
            "  fp permission recalculate project:3     Scoped to project"
        ),
        formatter_class=FORMATTER,
    )
    recalc_parser.add_argument(
        "scope",
        nargs="?",
        default="global",
        help="Scope to recalculate (default: global). Examples: global, folder:~/Work, project:3",
    )
    recalc_parser.set_defaults(func=_recalculate)
