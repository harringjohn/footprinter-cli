"""fp permission — access policy commands.

Subcommands:
    fp permission list                   Show all configured policies
    fp permission set <scope> [csv]      Set visibility and/or access for a scope
    fp permission reset <scope>          Remove policy (fall back to inheritance)
    fp permission check <scope>           Resolve access for a scope
    fp permission recalculate [scope]    Re-resolve access stamps from the policy chain
"""

import csv
import os
import time

from rich.table import Table

from footprinter.access_stamper import ENTITY_META, count_affected_entities
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
    SCOPE_PREFIXES,
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


_CSV_SCOPE_PREFIX: dict[str, str] = {
    meta["table"]: etype
    for etype, meta in ENTITY_META.items()
    if etype in SCOPE_PREFIXES
}


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
# Handler: recalculate
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
            merged[scope]["visibility"] = row["setting"]
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
    csv_file = getattr(args, "csv_file", None)
    if csv_file is not None:
        return _set_csv(args)

    visibility = getattr(args, "visibility", None)
    access = getattr(args, "access", None)

    if not visibility and not access:
        console.print(
            "[red]Specify at least one setting:[/red] --visibility or --access"
        )
        raise SystemExit(1)

    if visibility and visibility not in VISIBILITY_SETTINGS:
        console.print(
            f"[red]Invalid visibility setting:[/red] {visibility}\n"
            f"  Valid: {', '.join(sorted(VISIBILITY_SETTINGS))}"
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

        if visibility:
            set_visibility_policy(conn, args.scope, visibility)
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
# Handler: set (CSV bulk path)
# ---------------------------------------------------------------------------


def _set_csv(args) -> None:
    visibility_flag = getattr(args, "visibility", None)
    access_flag = getattr(args, "access", None)
    csv_file = args.csv_file

    if visibility_flag or access_flag:
        console.print(
            "[red]Cannot combine CSV file with --visibility/--access flags.[/red]\n"
            "  Settings come from the CSV columns."
        )
        raise SystemExit(1)

    scope = args.scope
    if ":" not in scope or not scope.startswith("source:"):
        console.print(
            "[red]CSV bulk requires a source:<type> scope.[/red]\n"
            f"  Got: {scope}"
        )
        raise SystemExit(1)

    source_type = scope.split(":", 1)[1]
    scope_prefix = _CSV_SCOPE_PREFIX.get(source_type)
    if scope_prefix is None:
        console.print(
            f"[red]CSV bulk is not supported for {scope}.[/red]\n"
            f"  Supported: {', '.join(sorted(_CSV_SCOPE_PREFIX))}"
        )
        raise SystemExit(1)

    if not os.path.isfile(csv_file):
        console.print(f"[red]File not found:[/red] {csv_file}")
        raise SystemExit(1)

    with open(csv_file, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        if "id" not in fieldnames:
            console.print("[red]CSV must contain an 'id' column.[/red]")
            raise SystemExit(1)

        has_visibility = "visibility" in fieldnames
        has_access = "access" in fieldnames
        if not has_visibility and not has_access:
            console.print(
                "[red]CSV must contain at least one of: visibility, access.[/red]"
            )
            raise SystemExit(1)

        rows = list(reader)

    if not rows:
        console.print("[dim]No rows in CSV — nothing to apply.[/dim]")
        return

    conn = get_policy_db()
    if conn is None:
        console.print("[yellow]No database found.[/yellow]")
        raise SystemExit(1)

    try:
        entity_type = scope_prefix
        table = ENTITY_META[entity_type]["table"]

        validated: list[tuple[int, str | None, str | None]] = []
        for i, row in enumerate(rows, start=2):
            raw_id = row.get("id", "").strip()
            try:
                record_id = int(raw_id)
            except (ValueError, TypeError):
                console.print(
                    f"[red]Row {i}: Invalid id '{raw_id}' — must be an integer.[/red]"
                )
                raise SystemExit(1)

            vis = row.get("visibility", "").strip() if has_visibility else ""
            acc = row.get("access", "").strip() if has_access else ""

            if not vis and not acc:
                console.print(
                    f"[red]Row {i}: At least one of visibility or access must be set.[/red]"
                )
                raise SystemExit(1)

            if vis and vis not in VISIBILITY_SETTINGS:
                console.print(
                    f"[red]Row {i}: Invalid visibility '{vis}'.[/red]\n"
                    f"  Valid: {', '.join(sorted(VISIBILITY_SETTINGS))}"
                )
                raise SystemExit(1)

            if acc and acc not in PERMISSION_SETTINGS:
                console.print(
                    f"[red]Row {i}: Invalid access '{acc}'.[/red]\n"
                    f"  Valid: {', '.join(sorted(PERMISSION_SETTINGS))}"
                )
                raise SystemExit(1)

            exists = conn.execute(
                f"SELECT 1 FROM {table} WHERE id = ?", (record_id,)
            ).fetchone()
            if not exists:
                console.print(
                    f"[red]Row {i}: {entity_type} {record_id} not found in database.[/red]"
                )
                raise SystemExit(1)

            validated.append((record_id, vis or None, acc or None))

        for record_id, vis, acc in validated:
            record_scope = f"{scope_prefix}:{record_id}"
            if vis:
                set_visibility_policy(conn, record_scope, vis)
            if acc:
                set_permission_policy(conn, record_scope, acc)

        stats = recalculate_with_progress(conn, scope)

        console.print(
            f"\nApplied [bold]{len(validated)}[/bold] record "
            f"{'policy' if len(validated) == 1 else 'policies'} "
            f"for [cyan]{scope}[/cyan]."
        )
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


def _parse_check_scope(scope: str) -> tuple[str, str]:
    """Parse a check scope string into (type, value)."""
    if scope == "global":
        console.print(
            "[red]check is not supported for scope[/red] [cyan]global[/cyan]\n"
            "  Supported: file path, folder:<path>, project:<id>, client:<id>"
        )
        raise SystemExit(1)

    if ":" not in scope:
        return ("path", scope)

    prefix, value = scope.split(":", 1)

    if prefix == "file":
        if value.isdigit():
            console.print(
                "[red]check does not support numeric file IDs.[/red] Use the file path instead.\n"
                "  Example: fp permission check ~/Work/file.py"
            )
            raise SystemExit(1)
        return ("path", value)

    if prefix == "folder":
        from footprinter.db.policies import is_folder_path_scope

        if not is_folder_path_scope(scope):
            console.print(
                "[red]check does not support numeric folder IDs.[/red] Use a folder path instead.\n"
                "  Example: fp permission check folder:~/Work"
            )
            raise SystemExit(1)
        return ("folder", value)

    if prefix in ("project", "client"):
        try:
            int(value)
        except ValueError:
            console.print(
                f"[red]Invalid {prefix} ID:[/red] {value!r}. Must be a number."
            )
            raise SystemExit(1)
        return (prefix, value)

    console.print(
        f"[red]check is not supported for scope[/red] [cyan]{scope}[/cyan]\n"
        "  Supported: file path, folder:<path>, project:<id>, client:<id>"
    )
    raise SystemExit(1)


def _check(args) -> None:
    from footprinter.permissions import BASELINE_PERMISSION
    from footprinter.visibility import BASELINE_VISIBILITY

    scope = getattr(args, "scope", None)
    json_output = getattr(args, "json", False)
    verbose = getattr(args, "verbose", False)

    if not scope:
        console.print("[red]Specify a scope to check.[/red]")
        console.print()
        console.print("Usage:")
        console.print("  fp permission check ~/Work/file.py")
        console.print("  fp permission check folder:~/Work")
        console.print("  fp permission check project:3")
        console.print("  fp permission check client:7")
        raise SystemExit(1)

    scope_type, scope_value = _parse_check_scope(scope)

    conn = get_policy_db()
    if conn is None:
        perm_str = "allow" if BASELINE_PERMISSION else "deny"
        vis_str = BASELINE_VISIBILITY
        if json_output:
            output_json(
                {
                    "path": scope,
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
        if scope_type == "path":
            check_file_path(conn, scope_value, json_output, verbose)
        elif scope_type == "folder":
            check_folder(conn, scope_value, json_output, verbose)
        elif scope_type == "project":
            check_project(conn, int(scope_value), json_output, verbose)
        elif scope_type == "client":
            check_client(conn, int(scope_value), json_output, verbose)
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
            "  fp permission set source:emails records.csv     Bulk per-record policies\n"
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
            "Single-scope mode: at least one of --visibility or --access is required.\n"
            "CSV bulk mode: pass a CSV file with id,visibility,access columns after\n"
            "a source:<type> scope to set per-record policies in bulk.\n\n"
            "Scopes: global, folder:~/path, project:<id>, client:<id>, source:<type>."
        ),
        epilog=(
            "examples:\n"
            "  fp permission set global --visibility full --access allow\n"
            "  fp permission set folder:~/Work --visibility hidden\n"
            "  fp permission set project:3 --access deny\n"
            "  fp permission set source:emails --visibility opaque --access deny\n"
            "  fp permission set source:emails records.csv   # bulk per-record policies\n"
            "  fp permission set source:files  records.csv   # bulk per-record policies"
        ),
        formatter_class=FORMATTER,
    )
    set_parser.add_argument(
        "scope",
        help="Policy scope (e.g. global, folder:~/Work, source:emails)",
    )
    set_parser.add_argument(
        "csv_file",
        nargs="?",
        default=None,
        help="CSV file with id,visibility,access columns (requires source:<type> scope)",
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
        help="Check access resolution for a scope",
        description=(
            "Check both access and visibility resolution for a scope.\n\n"
            "Accepts the same scope strings as set/reset/recalculate.\n"
            "A bare path (no prefix) is treated as a file path."
        ),
        epilog=(
            "examples:\n"
            "  fp permission check ~/Work/file.py           Check a file path\n"
            "  fp permission check file:~/Work/file.py       Same, with explicit prefix\n"
            "  fp permission check folder:~/Work             Check a folder\n"
            "  fp permission check project:3                 Check a project\n"
            "  fp permission check client:7                  Check a client\n"
            "  fp permission check folder:~/Work --verbose   Show per-file details"
        ),
        formatter_class=FORMATTER,
    )
    check_parser.add_argument(
        "scope",
        nargs="?",
        default=None,
        help=(
            "Scope to check. Examples: ~/Work/file.py, folder:~/Work, project:3, client:7. "
            "A bare path (no prefix) is treated as a file path."
        ),
    )
    check_parser.add_argument(
        "--verbose", action="store_true", help="Show per-file details"
    )
    add_json_flag(check_parser)
    check_parser.set_defaults(func=_check)

    # -- recalculate --
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
