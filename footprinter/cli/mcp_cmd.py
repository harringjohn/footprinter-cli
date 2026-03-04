"""fp mcp — MCP server and access policy management.

Subcommands:
    fp mcp                    Start the MCP server
    fp mcp view {show,set,delete,check,reset}   Visibility policy management
    fp mcp read {show,set,delete,check,reset}   Permission policy management
    fp mcp check [path]       Combined resolution (both layers)
    fp mcp bulk               Bulk policy changes
"""

import os

from rich.table import Table

from footprinter.cli._common import FORMATTER, add_json_flag, console, output_json
from footprinter.cli._policy_helpers import (
    abbreviate_home,
    bulk_apply,
    check_client,
    check_file_path,
    check_folder,
    check_project,
    confirm_recalculation,
    get_policy_db,
    recalculate_with_progress,
    simulate_path_permission,
    simulate_path_visibility,
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
# View handlers (visibility layer only)
# ---------------------------------------------------------------------------


def _view_show(args) -> None:
    json_output = getattr(args, "json", False)

    conn = get_policy_db()
    if conn is None:
        if json_output:
            output_json([])
        else:
            console.print("[yellow]No database found.[/yellow]")
        return

    try:
        rows = list_visibility_policies(conn)

        if json_output:
            output_json(rows)
            return

        if not rows:
            console.print("No visibility policies configured.")
            console.print("  [dim]Run: fp mcp view reset[/dim]")
            return

        table = Table(title="Visibility Policies")
        table.add_column("Scope", style="cyan")
        table.add_column("Setting")
        table.add_column("Updated", style="dim")
        for row in rows:
            table.add_row(row["scope"], row["setting"], str(row["updated_at"] or ""))
        console.print(table)

        console.print()
        console.print("[dim]Baseline (when no policy matches): visibility=opaque[/dim]")
    finally:
        conn.close()


def _view_set(args) -> None:
    setting = args.level
    if setting not in VISIBILITY_SETTINGS:
        console.print(
            f"[red]Invalid visibility setting:[/red] {setting}\n  Valid: {', '.join(sorted(VISIBILITY_SETTINGS))}"
        )
        raise SystemExit(1)

    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)

    try:
        yes = getattr(args, "yes", False)
        if not confirm_recalculation(conn, args.scope, yes=yes):
            console.print("[dim]Cancelled.[/dim]")
            return
        set_visibility_policy(conn, args.scope, setting)
        console.print(f"Set visibility_policies: [cyan]{args.scope}[/cyan] = [bold]{setting}[/bold]")
        stats = recalculate_with_progress(conn, args.scope)
        _print_recalc_stats(stats)
    finally:
        conn.close()


def _view_delete(args) -> None:
    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        return

    try:
        exists = conn.execute("SELECT 1 FROM visibility_policies WHERE scope = ?", (args.scope,)).fetchone()
        if not exists:
            console.print(f"No visibility policy found for scope [cyan]{args.scope}[/cyan]")
            return

        yes = getattr(args, "yes", False)
        if not confirm_recalculation(conn, args.scope, yes=yes):
            console.print("[dim]Cancelled.[/dim]")
            return
        deleted = delete_visibility_policy(conn, args.scope)
        if deleted:
            console.print(f"Deleted visibility policy for [cyan]{args.scope}[/cyan]")
            stats = recalculate_with_progress(conn, args.scope)
            _print_recalc_stats(stats)
        else:
            console.print(f"No visibility policy found for scope [cyan]{args.scope}[/cyan]")
    finally:
        conn.close()


def _view_check(args) -> None:
    from footprinter.visibility import BASELINE_VISIBILITY, resolve_visibility_with_source

    path = getattr(args, "path", None)
    json_output = getattr(args, "json", False)

    conn = get_policy_db()
    if conn is None:
        vis_str = BASELINE_VISIBILITY
        if json_output:
            output_json(
                {
                    "path": path or "(none)",
                    "visibility": {"resolved": vis_str, "source": "baseline"},
                }
            )
        else:
            console.print("[yellow]No database found.[/yellow] Showing baseline.")
            console.print(f"  Visibility: [bold]{vis_str}[/bold]  (baseline)")
        return

    try:
        if not path:
            # Show global resolution
            row = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
            resolved = row["setting"] if row else BASELINE_VISIBILITY
            source = "global" if row else "baseline"
            if json_output:
                output_json({"visibility": {"resolved": resolved, "source": source}})
            else:
                console.print(f"  Visibility: [bold]{resolved}[/bold]  (from {source})")
            return

        expanded = os.path.expanduser(os.path.normpath(path))
        display = abbreviate_home(expanded)

        # Try to find file in DB
        row = conn.execute(
            "SELECT id FROM files WHERE path = ? AND status != 'removed'",
            (expanded,),
        ).fetchone()

        found_in_db = row is not None
        if row:
            vis_val, vis_src = resolve_visibility_with_source(conn, "file", row["id"])
        else:
            # Fall back to folders table
            folder_row = conn.execute(
                "SELECT id FROM folders WHERE path = ?",
                (expanded,),
            ).fetchone()
            if folder_row:
                found_in_db = True
                vis_val, vis_src = resolve_visibility_with_source(conn, "folder", folder_row["id"])
            else:
                vis_val, vis_src = simulate_path_visibility(conn, expanded)

        if json_output:
            output_json(
                {
                    "path": display,
                    "found_in_db": found_in_db,
                    "visibility": {"resolved": vis_val, "source": vis_src},
                }
            )
        else:
            console.print(f"\nVisibility Check: [bold]{display}[/bold]")
            if not found_in_db:
                console.print("  [dim]Not found in files or folders — resolving from policy chain[/dim]")
            console.print(f"  Visibility: [bold]{vis_val}[/bold]  (from {vis_src})")
    finally:
        conn.close()


def _view_reset(args) -> None:
    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        return

    try:
        yes = getattr(args, "yes", False)
        if not confirm_recalculation(conn, "global", yes=yes):
            console.print("[dim]Cancelled.[/dim]")
            return
        deleted = clear_visibility_policies(conn)
        console.print(f"Cleared {deleted} visibility policies")
        seed_visibility_defaults(conn)
        console.print("Re-seeded visibility defaults (global=visible)")
        stats = recalculate_with_progress(conn, "global")
        _print_recalc_stats(stats)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read handlers (permission layer only)
# ---------------------------------------------------------------------------


def _read_show(args) -> None:
    json_output = getattr(args, "json", False)

    conn = get_policy_db()
    if conn is None:
        if json_output:
            output_json([])
        else:
            console.print("[yellow]No database found.[/yellow]")
        return

    try:
        rows = list_permission_policies(conn)

        if json_output:
            output_json(rows)
            return

        if not rows:
            console.print("No permission policies configured.")
            console.print("  [dim]Run: fp mcp read reset[/dim]")
            return

        table = Table(title="Permission Policies")
        table.add_column("Scope", style="cyan")
        table.add_column("Setting")
        table.add_column("Updated", style="dim")
        for row in rows:
            table.add_row(row["scope"], row["setting"], str(row["updated_at"] or ""))
        console.print(table)

        console.print()
        console.print("[dim]Baseline (when no policy matches): permission=allow[/dim]")
    finally:
        conn.close()


def _read_set(args) -> None:
    setting = args.level
    if setting not in PERMISSION_SETTINGS:
        console.print(
            f"[red]Invalid permission setting:[/red] {setting}\n  Valid: {', '.join(sorted(PERMISSION_SETTINGS))}"
        )
        raise SystemExit(1)

    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)

    try:
        yes = getattr(args, "yes", False)
        if not confirm_recalculation(conn, args.scope, yes=yes):
            console.print("[dim]Cancelled.[/dim]")
            return
        set_permission_policy(conn, args.scope, setting)
        console.print(f"Set permission_policies: [cyan]{args.scope}[/cyan] = [bold]{setting}[/bold]")
        stats = recalculate_with_progress(conn, args.scope)
        _print_recalc_stats(stats)
    finally:
        conn.close()


def _read_delete(args) -> None:
    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        return

    try:
        exists = conn.execute("SELECT 1 FROM permission_policies WHERE scope = ?", (args.scope,)).fetchone()
        if not exists:
            console.print(f"No permission policy found for scope [cyan]{args.scope}[/cyan]")
            return

        yes = getattr(args, "yes", False)
        if not confirm_recalculation(conn, args.scope, yes=yes):
            console.print("[dim]Cancelled.[/dim]")
            return
        deleted = delete_permission_policy(conn, args.scope)
        if deleted:
            console.print(f"Deleted permission policy for [cyan]{args.scope}[/cyan]")
            stats = recalculate_with_progress(conn, args.scope)
            _print_recalc_stats(stats)
        else:
            console.print(f"No permission policy found for scope [cyan]{args.scope}[/cyan]")
    finally:
        conn.close()


def _read_check(args) -> None:
    from footprinter.permissions import BASELINE_PERMISSION, resolve_permission_with_source

    path = getattr(args, "path", None)
    json_output = getattr(args, "json", False)

    conn = get_policy_db()
    if conn is None:
        perm_str = "allow" if BASELINE_PERMISSION else "deny"
        if json_output:
            output_json(
                {
                    "path": path or "(none)",
                    "permission": {"resolved": perm_str, "source": "baseline"},
                }
            )
        else:
            console.print("[yellow]No database found.[/yellow] Showing baseline.")
            console.print(f"  Permission: [bold]{perm_str}[/bold]  (baseline)")
        return

    try:
        if not path:
            row = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
            resolved = row["setting"] if row else ("allow" if BASELINE_PERMISSION else "deny")
            source = "global" if row else "baseline"
            if json_output:
                output_json({"permission": {"resolved": resolved, "source": source}})
            else:
                console.print(f"  Permission: [bold]{resolved}[/bold]  (from {source})")
            return

        expanded = os.path.expanduser(os.path.normpath(path))
        display = abbreviate_home(expanded)

        row = conn.execute(
            "SELECT id FROM files WHERE path = ? AND status != 'removed'",
            (expanded,),
        ).fetchone()

        found_in_db = row is not None
        if row:
            perm_val, perm_src = resolve_permission_with_source(conn, "file", row["id"])
            perm_str = "allow" if perm_val else "deny"
        else:
            # Fall back to folders table
            folder_row = conn.execute(
                "SELECT id FROM folders WHERE path = ?",
                (expanded,),
            ).fetchone()
            if folder_row:
                found_in_db = True
                perm_val, perm_src = resolve_permission_with_source(conn, "folder", folder_row["id"])
                perm_str = "allow" if perm_val else "deny"
            else:
                perm_str, perm_src = simulate_path_permission(conn, expanded)

        if json_output:
            output_json(
                {
                    "path": display,
                    "found_in_db": found_in_db,
                    "permission": {"resolved": perm_str, "source": perm_src},
                }
            )
        else:
            console.print(f"\nPermission Check: [bold]{display}[/bold]")
            if not found_in_db:
                console.print("  [dim]Not found in files or folders — resolving from policy chain[/dim]")
            console.print(f"  Permission: [bold]{perm_str}[/bold]  (from {perm_src})")
    finally:
        conn.close()


def _read_reset(args) -> None:
    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        return

    try:
        yes = getattr(args, "yes", False)
        if not confirm_recalculation(conn, "global", yes=yes):
            console.print("[dim]Cancelled.[/dim]")
            return
        deleted = clear_permission_policies(conn)
        console.print(f"Cleared {deleted} permission policies")
        seed_permission_defaults(conn)
        console.print("Re-seeded permission defaults (global=allow)")
        stats = recalculate_with_progress(conn, "global")
        _print_recalc_stats(stats)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Combined check handler (both layers)
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
        console.print("[red]No target specified.[/red]")
        console.print()
        console.print("Usage: fp mcp check <path>")
        console.print()
        console.print("Examples:")
        console.print("  fp mcp check ~/Work/file.py       Check a file or folder")
        console.print("  fp mcp check --folder ~/Work      Check a folder (aggregate view)")
        console.print("  fp mcp check --project 3          Check a project")
        raise SystemExit(1)
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
# Bulk handler (both layers)
# ---------------------------------------------------------------------------


def _bulk(args) -> None:
    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)

    try:
        rc = bulk_apply(
            conn,
            folder=getattr(args, "folder", None),
            project=getattr(args, "project", None),
            permission=getattr(args, "permission", None),
            visibility=getattr(args, "visibility", None),
            dry_run=getattr(args, "dry_run", False),
            yes=getattr(args, "yes", False),
        )
        if rc:
            raise SystemExit(rc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def _add_set_parser(subparsers, *, dest: str, handler) -> None:
    """Add a ``set <scope> <level>`` parser to *subparsers*."""
    p = subparsers.add_parser(
        "set",
        help="Set a policy",
        description=("Set a policy for a scope.\n\nScopes: global, folder:~/path, project:<id>, client:<id>."),
        epilog=(
            "examples:\n"
            "  fp mcp view set global visible\n"
            "  fp mcp read set folder:~/Work allow\n"
            "  fp mcp read set project:3 deny"
        ),
        formatter_class=FORMATTER,
    )
    p.add_argument("scope", help="Policy scope (e.g. global, folder:~/Work, project:3)")
    p.add_argument("level", help="Policy value (e.g. allow, deny, visible, opaque, hidden)")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p.set_defaults(func=handler)


def _add_delete_parser(subparsers, *, handler) -> None:
    p = subparsers.add_parser(
        "delete",
        help="Delete a policy for a scope",
        description="Remove a policy entry, reverting the scope to inherited resolution.",
        formatter_class=FORMATTER,
    )
    p.add_argument("scope", help="Policy scope to delete (e.g. folder:~/Work)")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p.set_defaults(func=handler)


def _add_check_parser(subparsers, *, handler) -> None:
    p = subparsers.add_parser(
        "check",
        help="Check resolution for a path",
        description="Show how a policy resolves for a specific file path.",
        formatter_class=FORMATTER,
    )
    p.add_argument("path", nargs="?", default=None, help="File path to check")
    add_json_flag(p)
    p.set_defaults(func=handler)


def register(subparsers) -> None:
    """Register the ``mcp`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "mcp",
        help="MCP server and access policies",
        description=(
            "Start the MCP server or manage access control policies.\n\n"
            "With no subcommand, starts the MCP server for Claude Desktop.\n"
            "Use view/read subcommands to manage visibility and permission\n"
            "policies, or check/bulk for resolution and batch operations."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp                              Start the MCP server\n"
            "  fp mcp view show                    List visibility policies\n"
            "  fp mcp read show                    List permission policies\n"
            "  fp mcp check ~/Work/file.py          Check combined resolution\n"
            "  fp mcp bulk --folder ~/Work --permission allow\n"
            "\n"
            "tip: use 'fp mcp <command> --help' for details on any command."
        ),
        formatter_class=FORMATTER,
    )
    parser.set_defaults(func=_start_server)

    sub = parser.add_subparsers(dest="mcp_command", metavar="COMMAND", title="commands (one required)")

    # -- view subgroup (visibility) --
    view_parser = sub.add_parser(
        "view",
        help="Visibility policy management",
        description=(
            "Manage visibility policies that control what metadata\nClaude can see (visible, opaque, or hidden)."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp view show                    List all visibility policies\n"
            "  fp mcp view set global visible       Set global visibility\n"
            "  fp mcp view check ~/Work/file.py     Check resolution for a path\n"
            "  fp mcp view reset                   Re-seed defaults"
        ),
        formatter_class=FORMATTER,
    )
    view_parser.set_defaults(func=lambda args: view_parser.print_help())
    view_sub = view_parser.add_subparsers(dest="view_command", metavar="COMMAND", title="commands (one required)")

    show_v = view_sub.add_parser(
        "show",
        help="Show visibility policies",
        description="List all configured visibility policies.",
        formatter_class=FORMATTER,
    )
    add_json_flag(show_v)
    show_v.set_defaults(func=_view_show)

    _add_set_parser(view_sub, dest="view_command", handler=_view_set)
    _add_delete_parser(view_sub, handler=_view_delete)
    _add_check_parser(view_sub, handler=_view_check)

    reset_v = view_sub.add_parser(
        "reset",
        help="Clear and re-seed visibility defaults",
        description="Delete all visibility policies and re-seed with defaults (global=visible).",
        formatter_class=FORMATTER,
    )
    reset_v.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    reset_v.set_defaults(func=_view_reset)

    # -- read subgroup (permission) --
    read_parser = sub.add_parser(
        "read",
        help="Permission policy management",
        description=("Manage permission policies that control whether Claude\ncan read file content (allow or deny)."),
        epilog=(
            "examples:\n"
            "  fp mcp read show                    List all permission policies\n"
            "  fp mcp read set folder:~/Work allow  Allow reading Work files\n"
            "  fp mcp read check ~/Work/file.py     Check resolution for a path\n"
            "  fp mcp read reset                   Re-seed defaults"
        ),
        formatter_class=FORMATTER,
    )
    read_parser.set_defaults(func=lambda args: read_parser.print_help())
    read_sub = read_parser.add_subparsers(dest="read_command", metavar="COMMAND", title="commands (one required)")

    show_r = read_sub.add_parser(
        "show",
        help="Show permission policies",
        description="List all configured permission policies.",
        formatter_class=FORMATTER,
    )
    add_json_flag(show_r)
    show_r.set_defaults(func=_read_show)

    _add_set_parser(read_sub, dest="read_command", handler=_read_set)
    _add_delete_parser(read_sub, handler=_read_delete)
    _add_check_parser(read_sub, handler=_read_check)

    reset_r = read_sub.add_parser(
        "reset",
        help="Clear and re-seed permission defaults",
        description="Delete all permission policies and re-seed with defaults (global=allow).",
        formatter_class=FORMATTER,
    )
    reset_r.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    reset_r.set_defaults(func=_read_reset)

    # -- check (combined) --
    check_parser = sub.add_parser(
        "check",
        help="Check combined access resolution",
        description=(
            "Check both permission and visibility resolution for a target.\n\n"
            "Specify exactly one target: a file path, --folder, --project, or --client."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp check ~/Work/file.py           Check a file path\n"
            "  fp mcp check --folder ~/Work           Check a folder\n"
            "  fp mcp check --project 3               Check a project\n"
            "  fp mcp check --folder ~/Work --verbose  Show per-file details"
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

    # -- bulk --
    bulk_parser = sub.add_parser(
        "bulk",
        help="Bulk policy changes",
        description=(
            "Apply permission and/or visibility policies in bulk.\n\n"
            "Scope by --folder or --project. Set --permission and/or --visibility.\n"
            "Use --dry-run to preview before applying."
        ),
        epilog=(
            "examples:\n"
            "  fp mcp bulk --folder ~/Work --permission allow\n"
            "  fp mcp bulk --project 3 --visibility visible\n"
            "  fp mcp bulk --folder ~/Work --permission allow --visibility visible\n"
            "  fp mcp bulk --folder ~/Work --permission deny --dry-run"
        ),
        formatter_class=FORMATTER,
    )
    bulk_parser.add_argument("--folder", default=None, help="Folder scope (path)")
    bulk_parser.add_argument("--project", type=int, default=None, help="Project ID scope")
    bulk_parser.add_argument("--permission", default=None, help="Permission setting: allow or deny")
    bulk_parser.add_argument("--visibility", default=None, help="Visibility setting: visible, opaque, or hidden")
    bulk_parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview changes without applying")
    bulk_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    bulk_parser.set_defaults(func=_bulk)
