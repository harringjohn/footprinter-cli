"""fp permission — access policy commands.

Subcommands:
    fp permission list                   Show all configured policies
    fp permission set <scope>            Set visibility and/or access for a scope
    fp permission reset <scope>          Remove policy (fall back to inheritance)
    fp permission check <path>           Resolve access for a target
    fp permission recalculate [scope]    Re-resolve access stamps from the policy chain
"""

import time

from rich.table import Table

from footprinter.access_stamper import count_affected_entities
from footprinter.cli._common import FORMATTER, add_json_flag, console, output_json
from footprinter.cli._policy_helpers import (
    check_client,
    check_file_path,
    check_folder,
    check_project,
    confirm_recalculation,
    get_policy_db,
    recalculate_with_progress,
)
from footprinter.db.policies import (
    PERMISSION_SETTINGS,
    clear_permission_policies,
    clear_visibility_policies,
    delete_permission_policy,
    delete_visibility_policy,
    list_permission_policies,
    list_visibility_policies,
    seed_permission_defaults,
    seed_visibility_defaults,
    set_permission_policy,
    set_visibility_policy,
)

_VISIBILITY_INPUT = {"full": "full", "opaque": "opaque", "hidden": "hidden"}
_VISIBILITY_DISPLAY = {"full": "full", "opaque": "opaque", "hidden": "hidden"}


# ---------------------------------------------------------------------------
# Shared stats helpers
# ---------------------------------------------------------------------------


def _print_recalc_stats(stats: dict[str, int], elapsed: float | None = None) -> None:
    if not stats:
        return
    parts = [
        f"{count} {etype}{'s' if count != 1 else ''}"
        for etype, count in stats.items()
        if count
    ]
    if parts:
        suffix = f" in {elapsed:.1f}s" if elapsed is not None else ""
        console.print(f"  [dim]Recalculated: {', '.join(parts)}{suffix}[/dim]")


# ---------------------------------------------------------------------------
# Handler: recalculate (existing — FPR-1855)
# ---------------------------------------------------------------------------


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
        _print_recalc_stats(stats, elapsed)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Handler: list
# ---------------------------------------------------------------------------


def _list(args) -> None:
    json_output = getattr(args, "json", False)

    conn = get_policy_db()
    if conn is None:
        if json_output:
            output_json({"visibility": [], "permission": []})
        else:
            console.print("[yellow]No database found.[/yellow]")
        return

    try:
        vis_rows = list_visibility_policies(conn)
        perm_rows = list_permission_policies(conn)

        if json_output:
            output_json({"visibility": vis_rows, "permission": perm_rows})
            return

        if not vis_rows and not perm_rows:
            console.print("No policies configured.")
            console.print(
                "  [dim]Run: fp permission set global --visibility full --access allow[/dim]"
            )
            return

        merged: dict[str, dict] = {}
        for row in vis_rows:
            scope = row["scope"]
            merged.setdefault(
                scope, {"visibility": None, "access": None, "updated_at": None}
            )
            merged[scope]["visibility"] = _VISIBILITY_DISPLAY.get(
                row["setting"], row["setting"]
            )
            merged[scope]["updated_at"] = row.get("updated_at")
        for row in perm_rows:
            scope = row["scope"]
            merged.setdefault(
                scope, {"visibility": None, "access": None, "updated_at": None}
            )
            merged[scope]["access"] = row["setting"]
            ts = row.get("updated_at")
            if ts and (
                not merged[scope]["updated_at"]
                or ts > merged[scope]["updated_at"]
            ):
                merged[scope]["updated_at"] = ts

        table = Table(title="Access Policies")
        table.add_column("Scope", style="cyan")
        table.add_column("Visibility")
        table.add_column("Access")
        table.add_column("Updated", style="dim")
        for scope in sorted(merged, key=lambda s: (s != "global", s)):
            entry = merged[scope]
            table.add_row(
                scope,
                entry["visibility"] or "-",
                entry["access"] or "-",
                str(entry["updated_at"] or ""),
            )
        console.print(table)

        console.print()
        console.print(
            "[dim]Baselines (when no policy matches): visibility=opaque, access=allow[/dim]"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Handler: set
# ---------------------------------------------------------------------------


def _set(args) -> None:
    visibility = getattr(args, "visibility", None)
    access = getattr(args, "access", None)
    dry_run = getattr(args, "dry_run", False)

    if not visibility and not access:
        console.print(
            "[red]Specify at least one setting:[/red] --visibility or --access"
        )
        raise SystemExit(1)

    if visibility and visibility not in _VISIBILITY_INPUT:
        console.print(
            f"[red]Invalid visibility setting:[/red] {visibility}\n"
            f"  Valid: {', '.join(sorted(_VISIBILITY_INPUT))}"
        )
        raise SystemExit(1)

    if access and access not in PERMISSION_SETTINGS:
        console.print(
            f"[red]Invalid access setting:[/red] {access}\n"
            f"  Valid: {', '.join(sorted(PERMISSION_SETTINGS))}"
        )
        raise SystemExit(1)

    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)

    try:
        counts = count_affected_entities(conn, args.scope)
        total = sum(counts.values())
        count_parts = [
            f"{c:,} {t}{'s' if c != 1 else ''}" for t, c in counts.items() if c
        ]

        settings_desc = []
        if visibility:
            settings_desc.append(f"visibility=[bold]{visibility}[/bold]")
        if access:
            settings_desc.append(f"access=[bold]{access}[/bold]")

        console.print(
            f"\nScope: [cyan]{args.scope}[/cyan]  ({total:,} entities: {', '.join(count_parts)})"
            if count_parts
            else f"\nScope: [cyan]{args.scope}[/cyan]  (0 entities)"
        )
        console.print(f"  Setting: {', '.join(settings_desc)}")

        if dry_run:
            console.print("\n[dim]Dry run — no changes made.[/dim]")
            return

        if visibility:
            set_visibility_policy(conn, args.scope, _VISIBILITY_INPUT[visibility])
        if access:
            set_permission_policy(conn, args.scope, access)

        console.print(
            f"Set [cyan]{args.scope}[/cyan]: {', '.join(settings_desc)}"
        )
        stats = recalculate_with_progress(conn, args.scope)
        _print_recalc_stats(stats)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Handler: reset
# ---------------------------------------------------------------------------


def _reset(args) -> None:
    reset_all = getattr(args, "all", False)
    scope = getattr(args, "scope", None)

    if reset_all and scope:
        console.print(
            "[red]Cannot combine --all with a scope.[/red] Use one or the other."
        )
        raise SystemExit(1)

    if not reset_all and not scope:
        console.print("[red]Specify a scope to reset, or use --all.[/red]")
        console.print()
        console.print("Usage:")
        console.print(
            "  fp permission reset <scope>     Remove policy for a scope (fall back to inheritance)"
        )
        console.print(
            "  fp permission reset --all       Clear all policies and re-seed defaults"
        )
        raise SystemExit(1)

    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)

    try:
        if reset_all:
            yes = getattr(args, "yes", False)
            if not confirm_recalculation(conn, "global", yes=yes):
                console.print("[dim]Cancelled.[/dim]")
                return
            vis_count = clear_visibility_policies(conn)
            perm_count = clear_permission_policies(conn)
            console.print(
                f"Cleared {vis_count} visibility + {perm_count} access policies"
            )
            seed_visibility_defaults(conn)
            seed_permission_defaults(conn)
            console.print(
                "Re-seeded defaults (global: visibility=full, access=allow)"
            )
            stats = recalculate_with_progress(conn, "global")
            _print_recalc_stats(stats)
            return

        assert scope is not None
        vis_exists = conn.execute(
            "SELECT 1 FROM visibility_policies WHERE scope = ?", (scope,)
        ).fetchone()
        perm_exists = conn.execute(
            "SELECT 1 FROM permission_policies WHERE scope = ?", (scope,)
        ).fetchone()

        if not vis_exists and not perm_exists:
            console.print(f"No policies found for scope [cyan]{scope}[/cyan]")
            return

        parts = []
        if vis_exists:
            delete_visibility_policy(conn, scope)
            parts.append("visibility")
        if perm_exists:
            delete_permission_policy(conn, scope)
            parts.append("access")

        console.print(f"Reset [cyan]{scope}[/cyan]: removed {' + '.join(parts)}")
        stats = recalculate_with_progress(conn, scope)
        _print_recalc_stats(stats)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Handler: check
# ---------------------------------------------------------------------------


def _check(args) -> None:
    from footprinter.permissions import BASELINE_PERMISSION
    from footprinter.visibility import BASELINE_VISIBILITY

    path = getattr(args, "path", None)
    folder = getattr(args, "folder", None)
    project = getattr(args, "project", None)
    client = getattr(args, "client", None)
    json_output = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    targets = []
    if path:
        targets.append("path")
    if folder:
        targets.append("folder")
    if project is not None:
        targets.append("project")
    if client is not None:
        targets.append("client")

    if len(targets) == 0:
        console.print(
            "[red]Specify a target to check.[/red] Use a file path, --folder, --project, or --client."
        )
        console.print()
        console.print("Usage:")
        console.print("  fp permission check ~/Work/file.py")
        console.print("  fp permission check --folder ~/Work")
        console.print("  fp permission check --project 3")
        raise SystemExit(1)

    if len(targets) > 1:
        console.print(
            "[red]Specify only one target.[/red] Got: " + ", ".join(targets)
        )
        raise SystemExit(1)

    conn = get_policy_db()
    if conn is None:
        perm_str = "allow" if BASELINE_PERMISSION else "deny"
        vis_str = BASELINE_VISIBILITY
        if json_output:
            output_json(
                {
                    "path": path or folder or str(project) or str(client),
                    "found_in_db": False,
                    "permission": {"resolved": perm_str, "source": "baseline"},
                    "visibility": {"resolved": vis_str, "source": "baseline"},
                    "chain": [
                        {
                            "scope": "baseline",
                            "permission": perm_str,
                            "visibility": vis_str,
                        }
                    ],
                }
            )
        else:
            console.print(
                "[yellow]No database found.[/yellow] Showing baseline defaults."
            )
            console.print(f"  Access: [bold]{perm_str}[/bold]  (baseline)")
            console.print(f"  Visibility: [bold]{vis_str}[/bold]  (baseline)")
        return

    try:
        if path:
            check_file_path(conn, path, json_output, verbose)
        elif folder:
            check_folder(conn, folder, json_output, verbose)
        elif project is not None:
            check_project(conn, project, json_output, verbose)
        elif client is not None:
            check_client(conn, client, json_output, verbose)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``permission`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "permission",
        help="Access policy commands",
        description=(
            "Manage access control policies.\n\n"
            "Use list/set/reset to manage policies, check to inspect resolution,\n"
            "and recalculate to re-resolve access stamps."
        ),
        epilog=(
            "examples:\n"
            "  fp permission list                              Show all policies\n"
            "  fp permission set global --visibility full --access allow\n"
            "  fp permission set folder:~/Work --visibility hidden --dry-run\n"
            "  fp permission reset folder:~/Work               Remove folder policy\n"
            "  fp permission check ~/Work/file.py              Check access resolution\n"
            "  fp permission recalculate                       Full recalculation\n"
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

    # -- list --
    list_parser = sub.add_parser(
        "list",
        help="Show all configured access policies",
        description=(
            "Show all configured access policies.\n\n"
            "Displays a merged table of visibility and access policies by scope."
        ),
        epilog=(
            "examples:\n"
            "  fp permission list            Human-readable table\n"
            "  fp permission list --json     Machine-readable JSON"
        ),
        formatter_class=FORMATTER,
    )
    add_json_flag(list_parser)
    list_parser.set_defaults(func=_list)

    # -- set --
    set_parser = sub.add_parser(
        "set",
        help="Set policy for a scope",
        description=(
            "Set visibility and/or access for a scope.\n\n"
            "At least one of --visibility or --access is required.\n"
            "Scopes: global, folder:~/path, project:<id>, client:<id>, source:<type>."
        ),
        epilog=(
            "examples:\n"
            "  fp permission set global --visibility full --access allow\n"
            "  fp permission set folder:~/Work --visibility hidden\n"
            "  fp permission set folder:~/Work --access deny --dry-run\n"
            "  fp permission set project:3 --access deny\n"
            "  fp permission set source:emails --visibility opaque --access deny"
        ),
        formatter_class=FORMATTER,
    )
    set_parser.add_argument(
        "scope",
        help="Policy scope (e.g. global, folder:~/Work, project:3)",
    )
    set_parser.add_argument(
        "--visibility",
        default=None,
        choices=["full", "opaque", "hidden"],
        help="Visibility: full, opaque, or hidden",
    )
    set_parser.add_argument(
        "--access",
        default=None,
        choices=["allow", "deny"],
        help="Access: allow or deny",
    )
    set_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview changes without applying",
    )
    set_parser.set_defaults(func=_set)

    # -- reset --
    reset_parser = sub.add_parser(
        "reset",
        help="Remove policy for a scope (fall back to inheritance)",
        description=(
            "Remove the explicit policy for a scope, reverting to inherited resolution.\n\n"
            "With --all, clear all policies and re-seed defaults."
        ),
        epilog=(
            "examples:\n"
            "  fp permission reset global              Remove global policy\n"
            "  fp permission reset folder:~/Work        Remove folder policy\n"
            "  fp permission reset --all               Clear all and re-seed defaults"
        ),
        formatter_class=FORMATTER,
    )
    reset_parser.add_argument(
        "scope", nargs="?", default=None, help="Scope to reset"
    )
    reset_parser.add_argument(
        "--all", action="store_true", help="Clear ALL policies and re-seed defaults"
    )
    reset_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation prompt"
    )
    reset_parser.set_defaults(func=_reset)

    # -- check --
    check_parser = sub.add_parser(
        "check",
        help="Check access resolution for a target",
        description=(
            "Check both access and visibility resolution for a target.\n\n"
            "Specify exactly one target: a file path, --folder, --project, or --client."
        ),
        epilog=(
            "examples:\n"
            "  fp permission check ~/Work/file.py           Check a file path\n"
            "  fp permission check --folder ~/Work           Check a folder\n"
            "  fp permission check --project 3               Check a project\n"
            "  fp permission check --folder ~/Work --verbose Show per-file details"
        ),
        formatter_class=FORMATTER,
    )
    check_parser.add_argument(
        "path", nargs="?", default=None, help="File path to check"
    )
    check_parser.add_argument(
        "--folder", default=None, help="Folder path to check"
    )
    check_parser.add_argument(
        "--project", type=int, default=None, help="Project ID to check"
    )
    check_parser.add_argument(
        "--client", type=int, default=None, help="Client ID to check"
    )
    check_parser.add_argument(
        "--verbose", action="store_true", help="Show per-file details"
    )
    add_json_flag(check_parser)
    check_parser.set_defaults(func=_check)

    # -- recalculate (existing — FPR-1855) --
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
