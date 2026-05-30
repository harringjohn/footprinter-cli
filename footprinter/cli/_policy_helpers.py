"""Shared MCP access-policy helpers used by ``setup.py`` and ``mcp_cmd.py``
(``fp mcp check/set/reset/bulk``).
"""

import os
import sqlite3

from rich.table import Table

from footprinter.access_stamper import (
    count_affected_entities,
    recalculate_access,
    recalculate_access_batched,
)
from footprinter.cli._common import connect_db, console, output_json
from footprinter.cli._prompt import SafeConfirm as Confirm
from footprinter.db.policies import (
    seed_access_policies,  # noqa: F401 — re-exported; setup.py imports from here
    )
from footprinter.paths import get_db_path
from footprinter.utils.paths import abbreviate_home

CONFIRM_THRESHOLD = 100
"""Entity count above which policy changes require interactive confirmation."""


# ---------------------------------------------------------------------------
# Confirmation helper
# ---------------------------------------------------------------------------


def confirm_recalculation(conn: sqlite3.Connection, scope: str, *, yes: bool = False) -> bool:
    """Count affected entities; if above threshold, prompt for confirmation.

    Returns True to proceed, False to cancel.
    """
    counts = count_affected_entities(conn, scope)
    total = sum(counts.values())
    if total <= CONFIRM_THRESHOLD:
        return True
    if yes:
        return True
    parts = [f"{c:,} {t}{'s' if c != 1 else ''}" for t, c in counts.items() if c]
    console.print(f"\nThis will recalculate access on [bold]{total:,}[/bold] entities across {len(counts)} tables.")
    for part in parts:
        console.print(f"  {part}")
    return Confirm.ask("Proceed?", default=False)


# ---------------------------------------------------------------------------
# Progress-aware recalculation
# ---------------------------------------------------------------------------


def recalculate_with_progress(conn: sqlite3.Connection, scope: str) -> dict[str, int]:
    """Recalculate access with a Rich progress bar for large scopes.

    If total affected entities <= CONFIRM_THRESHOLD, runs the fast unbatched
    path and prints a one-line summary. Otherwise shows a Rich progress bar
    with per-batch updates.
    """
    counts = count_affected_entities(conn, scope)
    total = sum(counts.values())

    if total <= CONFIRM_THRESHOLD:
        return recalculate_access(conn, scope)

    from rich.progress import Progress

    with Progress(console=console) as progress:
        task = progress.add_task("Recalculating access…", total=total)
        stats = recalculate_access_batched(
            conn,
            scope,
            on_batch=lambda n: progress.advance(task, advance=n),
        )
    return stats


# ---------------------------------------------------------------------------
# DB / path helpers
# ---------------------------------------------------------------------------


def get_policy_db() -> sqlite3.Connection | None:
    """Open a connection to the Footprinter database for policy operations."""
    return connect_db(get_db_path())


def normalize_path(path: str) -> str:
    """Convert absolute paths to ``~/…`` form for consistent config storage.

    Strips trailing slashes and collapses double slashes via ``os.path.normpath``.
    Paths not under ``$HOME`` are returned normalized but unchanged.
    """
    normalized = os.path.normpath(path)
    home = os.path.expanduser("~")
    if normalized.startswith(home + os.sep):
        normalized = "~" + normalized[len(home) :]
    elif normalized == home:
        normalized = "~"
    return normalized


# ---------------------------------------------------------------------------
# Single-path check helpers
# ---------------------------------------------------------------------------


def check_file_path(conn: sqlite3.Connection, path: str, json_output: bool, verbose: bool = False) -> int:
    """Check resolved access for a single file path."""
    from footprinter.permissions import resolve_permission_with_source
    from footprinter.visibility import resolve_visibility_with_source

    expanded = os.path.expanduser(os.path.normpath(path))

    row = conn.execute(
        "SELECT id, name, project_id FROM files WHERE path = ? AND status = 'listed'",
        (expanded,),
    ).fetchone()

    if row:
        file_id = row["id"]
        perm_val, perm_src = resolve_permission_with_source(conn, "file", file_id)
        vis_val, vis_src = resolve_visibility_with_source(conn, "file", file_id)
        perm_str = "allow" if perm_val else "deny"
        found = True
        client_id = None
        if row["project_id"] is not None:
            proj = conn.execute(
                "SELECT client_id FROM projects WHERE id = ?",
                (row["project_id"],),
            ).fetchone()
            if proj:
                client_id = proj["client_id"]
        chain = build_policy_chain(conn, expanded, file_id, row["project_id"], client_id)
    else:
        # Check folders table before falling through to not-found
        folder_row = conn.execute(
            "SELECT id, name, path FROM folders WHERE path = ?",
            (expanded,),
        ).fetchone()
        if folder_row:
            return check_folder(conn, expanded, json_output, verbose=verbose)

        perm_str, perm_src = simulate_path_permission(conn, expanded)
        vis_val, vis_src = simulate_path_visibility(conn, expanded)
        found = False
        file_id = None
        chain = build_policy_chain(conn, expanded, None, None, None)

    display_path = abbreviate_home(expanded)

    if json_output:
        data = {
            "type": "file",
            "path": display_path,
            "file_id": file_id,
            "found_in_db": found,
            "permission": {"resolved": perm_str, "source": perm_src},
            "visibility": {"resolved": vis_val, "source": vis_src},
            "chain": chain,
        }
        output_json(data)
    else:
        console.print(f"\nAccess Check: [bold]{display_path}[/bold]")
        if not found:
            console.print("  [dim]Not found in files or folders — resolving from policy chain[/dim]")
            console.print("  [dim]Tip: Use --folder for directory aggregate, --project for project ID[/dim]")
        console.print()
        console.print(f"  Permission: [bold]{perm_str}[/bold]   (from {perm_src})")
        console.print(f"  Visibility: [bold]{vis_val}[/bold]   (from {vis_src})")
        if chain:
            console.print()
            print_policy_chain(chain)

    return 0


def check_folder(conn: sqlite3.Connection, path: str, json_output: bool, verbose: bool) -> int:
    """Check resolved access for all files under a folder path."""
    from footprinter.permissions import batch_resolve_permissions
    from footprinter.visibility import batch_resolve_visibility

    expanded = os.path.expanduser(os.path.normpath(path))
    if not expanded.endswith(os.sep):
        expanded += os.sep

    rows = conn.execute(
        "SELECT id, name FROM files WHERE path LIKE ? AND status = 'listed'",
        (expanded + "%",),
    ).fetchall()

    display_path = abbreviate_home(expanded)
    file_count = len(rows)

    if file_count == 0:
        if json_output:
            data = {
                "type": "folder",
                "folder": display_path,
                "file_count": 0,
                "permission_counts": {"allow": 0, "deny": 0},
                "visibility_counts": {"full": 0, "opaque": 0, "hidden": 0},
            }
            output_json(data)
        else:
            console.print(f"\nFolder Check: [bold]{display_path}[/bold]  (0 files)")
            console.print("  [dim]No indexed files in this folder.[/dim]")
        return 0

    ids = [r["id"] for r in rows]
    perm_results = batch_resolve_permissions(conn, "file", ids)
    vis_results = batch_resolve_visibility(conn, "file", ids)

    perm_counts = {"allow": 0, "deny": 0}
    vis_counts = {"full": 0, "opaque": 0, "hidden": 0}

    for aid in ids:
        allowed, _ = perm_results.get(aid, (False, "baseline"))
        perm_counts["allow" if allowed else "deny"] += 1

        vis_state, _ = vis_results.get(aid, ("opaque", "baseline"))
        vis_counts[vis_state] = vis_counts.get(vis_state, 0) + 1

    if json_output:
        data = {
            "type": "folder",
            "folder": display_path,
            "file_count": file_count,
            "permission_counts": perm_counts,
            "visibility_counts": vis_counts,
        }
        if verbose:
            file_details = []
            for r in rows:
                aid = r["id"]
                allowed, p_src = perm_results.get(aid, (False, "baseline"))
                vis_state, v_src = vis_results.get(aid, ("opaque", "baseline"))
                file_details.append(
                    {
                        "name": r["name"],
                        "permission": "allow" if allowed else "deny",
                        "permission_source": p_src,
                        "visibility": vis_state,
                        "visibility_source": v_src,
                    }
                )
            data["files"] = file_details
        output_json(data)
    else:
        console.print(f"\nFolder Check: [bold]{display_path}[/bold]  ({file_count} files)")
        console.print()
        console.print(f"  Permission:  allow: {perm_counts['allow']}   deny: {perm_counts['deny']}")
        console.print(
            f"  Visibility:  full: {vis_counts['full']}   "
            f"opaque: {vis_counts['opaque']}   hidden: {vis_counts['hidden']}"
        )

        if verbose:
            console.print()
            table = Table(title="Files")
            table.add_column("Name", style="cyan")
            table.add_column("Permission")
            table.add_column("Source", style="dim")
            table.add_column("Visibility")
            table.add_column("Source", style="dim")
            for r in rows:
                aid = r["id"]
                allowed, p_src = perm_results.get(aid, (False, "baseline"))
                vis_state, v_src = vis_results.get(aid, ("opaque", "baseline"))
                table.add_row(
                    r["name"],
                    "allow" if allowed else "deny",
                    p_src,
                    vis_state,
                    v_src,
                )
            console.print(table)

    return 0


def check_project(
    conn: sqlite3.Connection, project_id: int, json_output: bool, verbose: bool = False
) -> int:
    """Check resolved access for a project by ID."""
    from footprinter.permissions import resolve_permission_with_source
    from footprinter.visibility import resolve_visibility_with_source

    row = conn.execute("SELECT id, name FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        console.print(f"[red]Project not found:[/red] id={project_id}")
        return 1

    perm_val, perm_src = resolve_permission_with_source(conn, "project", project_id)
    vis_val, vis_src = resolve_visibility_with_source(conn, "project", project_id)
    perm_str = "allow" if perm_val else "deny"

    file_rows = []
    perm_results = {}
    vis_results = {}
    perm_counts = {"allow": 0, "deny": 0}
    vis_counts = {"full": 0, "opaque": 0, "hidden": 0}

    if verbose:
        from footprinter.permissions import batch_resolve_permissions
        from footprinter.visibility import batch_resolve_visibility

        file_rows = conn.execute(
            "SELECT id, name FROM files WHERE project_id = ? AND status = 'listed'",
            (project_id,),
        ).fetchall()
        ids = [r["id"] for r in file_rows]

        if ids:
            perm_results = batch_resolve_permissions(conn, "file", ids)
            vis_results = batch_resolve_visibility(conn, "file", ids)
            for fid in ids:
                allowed, _ = perm_results.get(fid, (False, "baseline"))
                perm_counts["allow" if allowed else "deny"] += 1
                v_state, _ = vis_results.get(fid, ("opaque", "baseline"))
                vis_counts[v_state] = vis_counts.get(v_state, 0) + 1

    if json_output:
        data = {
            "project_id": project_id,
            "project_name": row["name"],
            "permission": {"resolved": perm_str, "source": perm_src},
            "visibility": {"resolved": vis_val, "source": vis_src},
        }
        if verbose:
            data["file_count"] = len(file_rows)
            data["permission_counts"] = perm_counts
            data["visibility_counts"] = vis_counts
            file_details = []
            for r in file_rows:
                allowed, p_src = perm_results.get(r["id"], (False, "baseline"))
                v_state, v_src = vis_results.get(r["id"], ("opaque", "baseline"))
                file_details.append({
                    "name": r["name"],
                    "permission": "allow" if allowed else "deny",
                    "permission_source": p_src,
                    "visibility": v_state,
                    "visibility_source": v_src,
                })
            data["files"] = file_details
        output_json(data)
    else:
        console.print(f"\nProject Check: [bold]{row['name']}[/bold]  (id={project_id})")
        console.print()
        console.print(f"  Permission: [bold]{perm_str}[/bold]   (from {perm_src})")
        console.print(f"  Visibility: [bold]{vis_val}[/bold]   (from {vis_src})")

        if verbose:
            file_count = len(file_rows)
            console.print(f"\n  Files: {file_count}")
            console.print(f"  Permission:  allow: {perm_counts['allow']}   deny: {perm_counts['deny']}")
            console.print(
                f"  Visibility:  full: {vis_counts['full']}   "
                f"opaque: {vis_counts['opaque']}   hidden: {vis_counts['hidden']}"
            )
            if file_rows:
                console.print()
                table = Table(title="Files")
                table.add_column("Name", style="cyan")
                table.add_column("Permission")
                table.add_column("Source", style="dim")
                table.add_column("Visibility")
                table.add_column("Source", style="dim")
                for r in file_rows:
                    fid = r["id"]
                    allowed, p_src = perm_results.get(fid, (False, "baseline"))
                    v_state, v_src = vis_results.get(fid, ("opaque", "baseline"))
                    table.add_row(r["name"], "allow" if allowed else "deny", p_src, v_state, v_src)
                console.print(table)

    return 0


def check_client(
    conn: sqlite3.Connection, client_id: int, json_output: bool, verbose: bool = False
) -> int:
    """Check resolved access for a client by ID."""
    from footprinter.permissions import resolve_permission_with_source
    from footprinter.visibility import resolve_visibility_with_source

    row = conn.execute("SELECT id, name FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not row:
        console.print(f"[red]Client not found:[/red] id={client_id}")
        return 1

    perm_val, perm_src = resolve_permission_with_source(conn, "client", client_id)
    vis_val, vis_src = resolve_visibility_with_source(conn, "client", client_id)
    perm_str = "allow" if perm_val else "deny"

    proj_rows = []
    perm_results = {}
    vis_results = {}
    perm_counts = {"allow": 0, "deny": 0}
    vis_counts = {"full": 0, "opaque": 0, "hidden": 0}

    if verbose:
        from footprinter.permissions import batch_resolve_permissions
        from footprinter.visibility import batch_resolve_visibility

        proj_rows = conn.execute(
            "SELECT id, name FROM projects WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        ids = [r["id"] for r in proj_rows]

        if ids:
            perm_results = batch_resolve_permissions(conn, "project", ids)
            vis_results = batch_resolve_visibility(conn, "project", ids)
            for pid in ids:
                allowed, _ = perm_results.get(pid, (False, "baseline"))
                perm_counts["allow" if allowed else "deny"] += 1
                v_state, _ = vis_results.get(pid, ("opaque", "baseline"))
                vis_counts[v_state] = vis_counts.get(v_state, 0) + 1

    if json_output:
        data = {
            "client_id": client_id,
            "client_name": row["name"],
            "permission": {"resolved": perm_str, "source": perm_src},
            "visibility": {"resolved": vis_val, "source": vis_src},
        }
        if verbose:
            data["project_count"] = len(proj_rows)
            data["permission_counts"] = perm_counts
            data["visibility_counts"] = vis_counts
            proj_details = []
            for r in proj_rows:
                allowed, p_src = perm_results.get(r["id"], (False, "baseline"))
                v_state, v_src = vis_results.get(r["id"], ("opaque", "baseline"))
                proj_details.append({
                    "id": r["id"],
                    "name": r["name"],
                    "permission": "allow" if allowed else "deny",
                    "permission_source": p_src,
                    "visibility": v_state,
                    "visibility_source": v_src,
                })
            data["projects"] = proj_details
        output_json(data)
    else:
        console.print(f"\nClient Check: [bold]{row['name']}[/bold]  (id={client_id})")
        console.print()
        console.print(f"  Permission: [bold]{perm_str}[/bold]   (from {perm_src})")
        console.print(f"  Visibility: [bold]{vis_val}[/bold]   (from {vis_src})")

        if verbose:
            project_count = len(proj_rows)
            console.print(f"\n  Projects: {project_count}")
            console.print(f"  Permission:  allow: {perm_counts['allow']}   deny: {perm_counts['deny']}")
            console.print(
                f"  Visibility:  full: {vis_counts['full']}   "
                f"opaque: {vis_counts['opaque']}   hidden: {vis_counts['hidden']}"
            )
            if proj_rows:
                console.print()
                table = Table(title="Projects")
                table.add_column("Name", style="cyan")
                table.add_column("Permission")
                table.add_column("Source", style="dim")
                table.add_column("Visibility")
                table.add_column("Source", style="dim")
                for r in proj_rows:
                    pid = r["id"]
                    allowed, p_src = perm_results.get(pid, (False, "baseline"))
                    v_state, v_src = vis_results.get(pid, ("opaque", "baseline"))
                    table.add_row(r["name"], "allow" if allowed else "deny", p_src, v_state, v_src)
                console.print(table)

    return 0


# ---------------------------------------------------------------------------
# Policy chain / simulation
# ---------------------------------------------------------------------------


def build_policy_chain(
    conn: sqlite3.Connection,
    path: str,
    file_id: int | None,
    project_id: int | None,
    client_id: int | None,
) -> list[dict]:
    """Build diagnostic policy chain showing what policies exist at each scope level."""
    from footprinter.permissions import BASELINE_PERMISSION
    from footprinter.visibility import BASELINE_VISIBILITY

    chain = []

    # 1. File-level
    if file_id is not None:
        perm = conn.execute(
            "SELECT setting FROM permission_policies WHERE scope = ?",
            (f"file:{file_id}",),
        ).fetchone()
        vis = conn.execute(
            "SELECT setting FROM visibility_policies WHERE scope = ?",
            (f"file:{file_id}",),
        ).fetchone()
        chain.append(
            {
                "scope": f"file:{file_id}",
                "permission": perm["setting"] if perm else None,
                "visibility": vis["setting"] if vis else None,
            }
        )

    # 2. Folder prefix policies (longest first)
    if path:
        folder_perms = conn.execute(
            "SELECT scope, setting FROM permission_policies WHERE scope LIKE 'folder:%' ORDER BY LENGTH(scope) DESC"
        ).fetchall()
        folder_vis = conn.execute(
            "SELECT scope, setting FROM visibility_policies WHERE scope LIKE 'folder:%' ORDER BY LENGTH(scope) DESC"
        ).fetchall()

        folder_perm_map = {r["scope"]: r["setting"] for r in folder_perms}
        folder_vis_map = {r["scope"]: r["setting"] for r in folder_vis}
        all_folder_scopes = sorted(
            set(folder_perm_map.keys()) | set(folder_vis_map.keys()),
            key=lambda s: len(s),
            reverse=True,
        )

        for scope in all_folder_scopes:
            prefix = scope[len("folder:") :]
            expanded_prefix = os.path.expanduser(prefix)
            if path.startswith(expanded_prefix):
                chain.append(
                    {
                        "scope": scope,
                        "permission": folder_perm_map.get(scope),
                        "visibility": folder_vis_map.get(scope),
                    }
                )

    # 3. Project-level
    if project_id is not None:
        perm = conn.execute(
            "SELECT setting FROM permission_policies WHERE scope = ?",
            (f"project:{project_id}",),
        ).fetchone()
        vis = conn.execute(
            "SELECT setting FROM visibility_policies WHERE scope = ?",
            (f"project:{project_id}",),
        ).fetchone()
        chain.append(
            {
                "scope": f"project:{project_id}",
                "permission": perm["setting"] if perm else None,
                "visibility": vis["setting"] if vis else None,
            }
        )

    # 4. Client-level
    if client_id is not None:
        perm = conn.execute(
            "SELECT setting FROM permission_policies WHERE scope = ?",
            (f"client:{client_id}",),
        ).fetchone()
        vis = conn.execute(
            "SELECT setting FROM visibility_policies WHERE scope = ?",
            (f"client:{client_id}",),
        ).fetchone()
        chain.append(
            {
                "scope": f"client:{client_id}",
                "permission": perm["setting"] if perm else None,
                "visibility": vis["setting"] if vis else None,
            }
        )

    # 5. Source: files
    src_perm = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'source:files'").fetchone()
    src_vis = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'source:files'").fetchone()
    chain.append(
        {
            "scope": "source:files",
            "permission": src_perm["setting"] if src_perm else None,
            "visibility": src_vis["setting"] if src_vis else None,
        }
    )

    # 6. Global
    global_perm = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
    global_vis = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
    chain.append(
        {
            "scope": "global",
            "permission": global_perm["setting"] if global_perm else None,
            "visibility": global_vis["setting"] if global_vis else None,
        }
    )

    # 7. Baseline
    chain.append(
        {
            "scope": "baseline",
            "permission": "allow" if BASELINE_PERMISSION else "deny",
            "visibility": BASELINE_VISIBILITY,
        }
    )

    return chain


def print_policy_chain(chain: list[dict]) -> None:
    """Print the policy chain as a Rich table."""
    table = Table(title="Policy Chain")
    table.add_column("Scope", style="cyan")
    table.add_column("Permission")
    table.add_column("Visibility")
    for entry in chain:
        table.add_row(
            entry["scope"],
            entry.get("permission") or "-",
            entry.get("visibility") or "-",
        )
    console.print(table)


def simulate_path_permission(conn: sqlite3.Connection, expanded_path: str) -> tuple[str, str]:
    """Simulate permission resolution for a path not in the database.

    Walks folder prefix -> source:files -> global -> baseline.
    """
    from footprinter.permissions import BASELINE_PERMISSION

    rows = conn.execute(
        "SELECT scope, setting FROM permission_policies WHERE scope LIKE 'folder:%' ORDER BY LENGTH(scope) DESC"
    ).fetchall()
    for row in rows:
        prefix = row["scope"][len("folder:") :]
        expanded_prefix = os.path.expanduser(prefix)
        if expanded_path.startswith(expanded_prefix):
            return (row["setting"], row["scope"])

    src = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'source:files'").fetchone()
    if src:
        return (src["setting"], "source:files")

    gl = conn.execute("SELECT setting FROM permission_policies WHERE scope = 'global'").fetchone()
    if gl:
        return (gl["setting"], "global")

    return ("allow" if BASELINE_PERMISSION else "deny", "baseline")


def simulate_path_visibility(conn: sqlite3.Connection, expanded_path: str) -> tuple[str, str]:
    """Simulate visibility resolution for a path not in the database.

    Walks folder prefix -> source:files -> global -> baseline.
    """
    from footprinter.visibility import BASELINE_VISIBILITY

    rows = conn.execute(
        "SELECT scope, setting FROM visibility_policies WHERE scope LIKE 'folder:%' ORDER BY LENGTH(scope) DESC"
    ).fetchall()
    for row in rows:
        prefix = row["scope"][len("folder:") :]
        expanded_prefix = os.path.expanduser(prefix)
        if expanded_path.startswith(expanded_prefix):
            return (row["setting"], row["scope"])

    src = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'source:files'").fetchone()
    if src:
        return (src["setting"], "source:files")

    gl = conn.execute("SELECT setting FROM visibility_policies WHERE scope = 'global'").fetchone()
    if gl:
        return (gl["setting"], "global")

    return (BASELINE_VISIBILITY, "baseline")
