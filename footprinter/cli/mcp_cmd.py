"""fp mcp — MCP server and access policy management.

Subcommands:
    fp mcp                    Start the MCP server
    fp mcp check              Show all policies / resolve combined access
    fp mcp set <scope>        Set visibility and/or permission for a scope
    fp mcp reset <scope>      Remove policy (fall back to inheritance)
"""

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
    VISIBILITY_SETTINGS,
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


def _print_recalc_stats(stats: dict[str, int]) -> None:
    """Print a dim summary of recalculation results."""
    if not stats:
        return
    parts = [f"{count} {etype}{'s' if count != 1 else ''}" for etype, count in stats.items() if count]
    if parts:
        console.print(f"  [dim]Recalculated: {', '.join(parts)}[/dim]")


# ---------------------------------------------------------------------------
# Handler: server start
# ---------------------------------------------------------------------------


def _start_server(args) -> None:
    from footprinter.mcp.server import main

    main()


# ---------------------------------------------------------------------------
# Check handler: show all policies (no args) or resolve target
# ---------------------------------------------------------------------------


def _check_show_all(args) -> None:
    """Show all configured policies from both tables."""
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
            console.print("  [dim]Run: fp mcp set global --visibility visible --permission allow[/dim]")
            return

        merged: dict[str, dict] = {}
        for row in vis_rows:
            scope = row["scope"]
            merged.setdefault(scope, {"visibility": None, "permission": None, "updated_at": None})
            merged[scope]["visibility"] = row["setting"]
            merged[scope]["updated_at"] = row.get("updated_at")
        for row in perm_rows:
            scope = row["scope"]
            merged.setdefault(scope, {"visibility": None, "permission": None, "updated_at": None})
            merged[scope]["permission"] = row["setting"]
            ts = row.get("updated_at")
            if ts and (not merged[scope]["updated_at"] or ts > merged[scope]["updated_at"]):
                merged[scope]["updated_at"] = ts

        table = Table(title="Access Policies")
        table.add_column("Scope", style="cyan")
        table.add_column("Visibility")
        table.add_column("Permission")
        table.add_column("Updated", style="dim")
        for scope in sorted(merged, key=lambda s: (s != "global", s)):
            entry = merged[scope]
            table.add_row(
                scope,
                entry["visibility"] or "-",
                entry["permission"] or "-",
                str(entry["updated_at"] or ""),
            )
        console.print(table)

        console.print()
        console.print("[dim]Baselines (when no policy matches): visibility=opaque, permission=allow[/dim]")
    finally:
        conn.close()


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
        return _check_show_all(args)

    if len(targets) > 1:
        console.print("[red]Specify only one target.[/red] Got: " + ", ".join(targets))
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
                    "chain": [{"scope": "baseline", "permission": perm_str, "visibility": vis_str}],
                }
            )
        else:
            console.print("[yellow]No database found.[/yellow] Showing baseline defaults.")
            console.print(f"  Permission: [bold]{perm_str}[/bold]  (baseline)")
            console.print(f"  Visibility: [bold]{vis_str}[/bold]  (baseline)")
        return

    try:
        if path:
            check_file_path(conn, path, json_output, verbose)
        elif folder:
            check_folder(conn, folder, json_output, verbose)
        elif project is not None:
            check_project(conn, project, json_output)
        elif client is not None:
            check_client(conn, client, json_output)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Set handler: unified policy setter
# ---------------------------------------------------------------------------


def _set(args) -> None:
    visibility = getattr(args, "visibility", None)
    permission = getattr(args, "permission", None)

    if not visibility and not permission:
        console.print("[red]Specify at least one setting:[/red] --visibility or --permission")
        raise SystemExit(1)

    if visibility and visibility not in VISIBILITY_SETTINGS:
        console.print(
            f"[red]Invalid visibility setting:[/red] {visibility}\n"
            f"  Valid: {', '.join(sorted(VISIBILITY_SETTINGS))}"
        )
        raise SystemExit(1)

    if permission and permission not in PERMISSION_SETTINGS:
        console.print(
            f"[red]Invalid permission setting:[/red] {permission}\n"
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
        count_parts = [f"{c:,} {t}{'s' if c != 1 else ''}" for t, c in counts.items() if c]

        settings_desc = []
        if visibility:
            settings_desc.append(f"visibility=[bold]{visibility}[/bold]")
        if permission:
            settings_desc.append(f"permission=[bold]{permission}[/bold]")

        console.print(
            f"\nScope: [cyan]{args.scope}[/cyan]  ({total:,} entities: {', '.join(count_parts)})"
            if count_parts
            else f"\nScope: [cyan]{args.scope}[/cyan]  (0 entities)"
        )
        console.print(f"  Setting: {', '.join(settings_desc)}")

        if visibility:
            set_visibility_policy(conn, args.scope, visibility)
        if permission:
            set_permission_policy(conn, args.scope, permission)

        console.print(f"Set [cyan]{args.scope}[/cyan]: {', '.join(settings_desc)}")
        stats = recalculate_with_progress(conn, args.scope)
        _print_recalc_stats(stats)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reset handler: unified policy delete / reseed
# ---------------------------------------------------------------------------


def _reset(args) -> None:
    reset_all = getattr(args, "all", False)
    scope = getattr(args, "scope", None)

    if reset_all and scope:
        console.print("[red]Cannot combine --all with a scope.[/red] Use one or the other.")
        raise SystemExit(1)

    if not reset_all and not scope:
        console.print("[red]Specify a scope to reset, or use --all.[/red]")
        console.print()
        console.print("Usage:")
        console.print("  fp mcp reset <scope>     Remove policy for a scope (fall back to inheritance)")
        console.print("  fp mcp reset --all       Clear all policies and re-seed defaults")
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
            console.print(f"Cleared {vis_count} visibility + {perm_count} permission policies")
            seed_visibility_defaults(conn)
            seed_permission_defaults(conn)
            console.print("Re-seeded defaults (global: visibility=visible, permission=allow)")
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
            parts.append("permission")

        console.print(f"Reset [cyan]{scope}[/cyan]: removed {' + '.join(parts)}")
        stats = recalculate_with_progress(conn, scope)
        _print_recalc_stats(stats)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register the ``mcp`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "mcp",
        help="MCP server and access policies",
        description=(
            "Start the MCP server or manage access control policies.\n\n"
            "With no subcommand, starts the MCP server for Claude Desktop.\n"
            "Use check/set/reset to manage unified access policies."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp                              Start the MCP server\n"
            "  fp mcp check                        Show all policies\n"
            "  fp mcp check ~/Work/file.py          Check combined resolution\n"
            "  fp mcp set global --visibility visible --permission allow\n"
            "  fp mcp set folder:~/Work --visibility hidden --dry-run\n"
            "  fp mcp reset folder:~/Work           Remove folder policy\n"
            "\n"
            "tip: use 'fp mcp <command> --help' for details on any command."
        ),
        formatter_class=FORMATTER,
    )
    parser.set_defaults(func=_start_server)

    sub = parser.add_subparsers(dest="mcp_command", metavar="COMMAND", title="commands (one required)")

    # -- check (combined: show all or resolve target) --
    check_parser = sub.add_parser(
        "check",
        help="Show all policies or check resolution for a target",
        description=(
            "With no arguments, show all configured access policies.\n\n"
            "With a target, check both permission and visibility resolution.\n"
            "Specify exactly one target: a file path, --folder, --project, or --client."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp check                           Show all policies\n"
            "  fp mcp check ~/Work/file.py             Check a file path\n"
            "  fp mcp check --folder ~/Work             Check a folder\n"
            "  fp mcp check --project 3                 Check a project\n"
            "  fp mcp check --folder ~/Work --verbose    Show per-file details"
        ),
        formatter_class=FORMATTER,
    )
    check_parser.add_argument("path", nargs="?", default=None, help="File path to check")
    check_parser.add_argument("--folder", default=None, help="Folder path to check")
    check_parser.add_argument("--project", type=int, default=None, help="Project ID to check")
    check_parser.add_argument("--client", type=int, default=None, help="Client ID to check")
    check_parser.add_argument("--verbose", action="store_true", help="Show per-file details")
    add_json_flag(check_parser)
    check_parser.set_defaults(func=_check)

    # -- set (unified policy setter) --
    set_parser = sub.add_parser(
        "set",
        help="Set policy for a scope",
        description=(
            "Set visibility and/or permission for a scope.\n\n"
            "At least one of --visibility or --permission is required.\n"
            "Scopes: global, folder:~/path, project:<id>, client:<id>, source:<type>."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp set global --visibility visible --permission allow\n"
            "  fp mcp set folder:~/Work --visibility hidden\n"
            "  fp mcp set folder:~/Work --permission deny\n"
            "  fp mcp set project:3 --permission deny\n"
            "  fp mcp set source:emails --visibility opaque --permission deny"
        ),
        formatter_class=FORMATTER,
    )
    set_parser.add_argument("scope", help="Policy scope (e.g. global, folder:~/Work, project:3)")
    set_parser.add_argument("--visibility", default=None, help="Visibility: visible, opaque, or hidden")
    set_parser.add_argument("--permission", default=None, help="Permission: allow or deny")
    set_parser.set_defaults(func=_set)

    # -- reset (unified delete / reseed) --
    reset_parser = sub.add_parser(
        "reset",
        help="Remove policy for a scope (fall back to inheritance)",
        description=(
            "Remove the explicit policy for a scope, reverting to inherited resolution.\n\n"
            "With --all, clear all policies and re-seed defaults."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp reset global                  Remove global policy\n"
            "  fp mcp reset folder:~/Work            Remove folder policy\n"
            "  fp mcp reset --all                   Clear all and re-seed defaults"
        ),
        formatter_class=FORMATTER,
    )
    reset_parser.add_argument("scope", nargs="?", default=None, help="Scope to reset")
    reset_parser.add_argument("--all", action="store_true", help="Clear ALL policies and re-seed defaults")
    reset_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    reset_parser.set_defaults(func=_reset)
