"""Status reporting — terminal status display, stage detail formatting, data counts."""

import sqlite3
from typing import Dict, List

from footprinter.paths import get_db_path


def get_status(db_path: str = None) -> Dict:
    """Get status of all data sources.

    Args:
        db_path: Path to SQLite database. Falls back to get_db_path().
    """
    if db_path is None:
        db_path = str(get_db_path())

    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    status = {}

    # Files - status = 'listed' means default-visible files
    # (excludes 'unlisted' dotfiles and 'removed' tombstones)
    cursor.execute(
        """
        SELECT source, COUNT(*) as count, SUM(size_bytes) as size
        FROM files WHERE status = 'listed'
        GROUP BY source
    """
    )
    status["files"] = {
        row["source"]: {
            "count": row["count"],
            "size_mb": round((row["size"] or 0) / 1024 / 1024, 1),
        }
        for row in cursor.fetchall()
    }

    cursor.execute("SELECT COUNT(*) FROM files WHERE status = 'listed'")
    status["files_total"] = cursor.fetchone()[0]

    # Indexed folders
    cursor.execute(
        """
        SELECT source, COUNT(*) as count
        FROM folders WHERE status = 'listed'
        GROUP BY source
    """
    )
    status["folders"] = {row["source"] or "local": row["count"] for row in cursor.fetchall()}

    # Browser visits
    cursor.execute("SELECT COUNT(*) FROM visits")
    status["visits"] = cursor.fetchone()[0]

    # Emails
    cursor.execute("SELECT COUNT(*) FROM emails")
    status["emails"] = cursor.fetchone()[0]

    # Chats
    cursor.execute("SELECT account, COUNT(*) as count FROM chats GROUP BY account")
    status["chats"] = {row["account"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) FROM messages")
    status["messages"] = cursor.fetchone()[0]

    # Projects
    cursor.execute("SELECT COUNT(*) FROM projects")
    status["projects"] = cursor.fetchone()[0]

    # retention classifications removed — not part of CLI tool

    # Access resolution — count entities with stamped visibility
    access = {}
    for table in ("files", "emails", "chats"):
        try:
            where = "mcp_view IS NOT NULL"
            if table == "files":
                where += " AND status = 'listed'"
            stamped = cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
            total_where = "status = 'listed'" if table == "files" else "1=1"
            total = cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {total_where}").fetchone()[0]
            access[table] = {"stamped": stamped, "total": total}
        except sqlite3.OperationalError:
            pass
    status["access_resolution"] = access

    # FTS health — check existence and integrity inline on the existing
    # connection to avoid opening a second Database() instance
    fts_tables = {
        "files_fts": "files",
        "emails_fts": "emails",
        "chats_fts": "chats",
    }
    fts = {}
    for fts_table, base_table in fts_tables.items():
        base_rows = cursor.execute(f"SELECT COUNT(*) FROM {base_table}").fetchone()[0]
        try:
            fts_rows = cursor.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
        except sqlite3.OperationalError:
            fts[fts_table] = {
                "status": "error",
                "fts_rows": None,
                "base_rows": base_rows,
            }
            continue
        # FTS5 integrity-check detects index drift
        try:
            cursor.execute(f"INSERT INTO {fts_table}({fts_table}, rank) VALUES('integrity-check', 1)")
            fts[fts_table] = {
                "status": "ok",
                "fts_rows": fts_rows,
                "base_rows": base_rows,
            }
        except sqlite3.DatabaseError:
            fts[fts_table] = {
                "status": "drift",
                "fts_rows": fts_rows,
                "base_rows": base_rows,
            }
    status["fts"] = fts

    conn.close()

    return status


def _stage_detail_string(result: Dict) -> str:
    """Extract a short detail string from a stage result dict."""
    reason = result.get("reason")
    if reason:
        return str(reason)

    known_keys = {
        "files_indexed": "files",
        "folders_found": "folders",
        "folders_indexed": "folders",
        "urls_indexed": "urls",
        "emails_indexed": "emails",
        "inserted": "inserted",
        "updated": "updated",
        "unchanged": "unchanged",
        "skipped": "skipped",
        "errors": "errors",
    }
    parts = []
    for key, label in known_keys.items():
        if key in result and isinstance(result[key], (int, float)):
            parts.append(f"{result[key]:,} {label}")

    # Check nested dicts with 'status' key (sub-results like classification, scoring)
    for key, value in result.items():
        if key in (
            "stage",
            "status",
            "elapsed_seconds",
            "error",
            "error_type",
            "recoverable",
            "mode",
            "note",
        ):
            continue
        if isinstance(value, dict) and "status" in value:
            sub_status = value["status"]
            if sub_status == "error":
                parts.append(f"{key}: error")
            else:
                # Pull a useful number from the sub-result
                for sub_key in (
                    "processed",
                    "files_processed",
                    "messages_indexed",
                    "projects_found",
                    "files_updated",
                    "folders_updated",
                ):
                    if sub_key in value and isinstance(value[sub_key], (int, float)):
                        parts.append(f"{value[sub_key]:,} {sub_key.replace('_', ' ')}")
                        break

    return ", ".join(parts[:3])


def print_status(status: Dict, quiet: bool = False, console=None):
    """Pretty print status as a Rich table."""
    if quiet:
        return

    from rich.console import Console
    from rich.table import Table

    if console is None:
        console = Console()

    console.print()
    console.print("[bold]Data Pipeline Status[/bold]")
    console.print()

    # Main data table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Source", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Size", justify="right")

    # File rows by source
    total_count = 0
    total_size = 0.0
    for source, data in status.get("files", {}).items():
        count = data["count"]
        size_mb = data["size_mb"]
        total_count += count
        total_size += size_mb
        table.add_row(f"  {source}", f"{count:,}", f"{size_mb:.1f} MB")

    # Non-file sources
    browser = status.get("visits", 0)
    emails = status.get("emails", 0)
    messages_count = status.get("messages", 0)
    projects = status.get("projects", 0)

    table.add_row("Browser history", f"{browser:,}", "")
    table.add_row("Emails", f"{emails:,}", "")
    table.add_row("Chat messages", f"{messages_count:,}", "")
    table.add_row("Projects", f"{projects:,}", "")

    # Chats and folders as table rows
    chat_total = sum(status.get("chats", {}).values())
    if chat_total:
        table.add_row("Chats", f"{chat_total:,}", "")
    folder_total = sum(status.get("folders", {}).values())
    if folder_total:
        table.add_row("Indexed folders", f"{folder_total:,}", "")

    # Total row
    table.add_section()
    table.add_row(
        "[bold]Total files[/bold]",
        f"[bold]{total_count:,}[/bold]",
        f"[bold]{total_size:.1f} MB[/bold]",
    )

    console.print(table)

    # FTS health section
    fts_data = status.get("fts", {})
    if fts_data:
        console.print()
        console.print("[bold]FTS Search Indexes[/bold]")
        fts_table = Table(show_header=True, header_style="bold")
        fts_table.add_column("Index", style="cyan")
        fts_table.add_column("Rows", justify="right")
        fts_table.add_column("Status")

        for idx_name, info in fts_data.items():
            idx_status = info.get("status", "unknown")
            if idx_status == "ok":
                status_text = "[green]ok[/green]"
                rows_text = f"{info['base_rows']:,}"
            elif idx_status == "error":
                status_text = "[red]missing[/red]"
                rows_text = "—"
            else:
                status_text = f"[yellow]{idx_status}[/yellow]"
                rows_text = f"{info.get('fts_rows', '?')}"
            fts_table.add_row(idx_name, rows_text, status_text)

        console.print(fts_table)

    console.print()


def _print_completion_summary(console, results: List[Dict], *, show_next_steps: bool = True):
    """Print a completion summary after pipeline run."""
    total_time = sum(r.get("elapsed_seconds", 0) for r in results)
    error_count = sum(1 for r in results if r.get("status") == "error")
    warn_count = sum(1 for r in results if r.get("status") == "completed_with_errors")
    completed_count = sum(1 for r in results if r.get("status") in ("completed", "completed_with_errors", "info"))

    console.print()
    if error_count == 0 and warn_count == 0:
        console.print(f"[bold green]Pipeline complete[/bold green]  {completed_count} stages in {total_time:.1f}s")
    elif error_count == 0:
        console.print(
            f"[bold yellow]Pipeline complete with {warn_count} warning(s)[/bold yellow]  "
            f"{completed_count} stages in {total_time:.1f}s"
        )
    else:
        console.print(
            f"[bold yellow]Pipeline finished with {error_count} error(s)[/bold yellow]  "
            f"{completed_count} OK, {error_count} failed in {total_time:.1f}s"
        )

    if show_next_steps:
        console.print()
        console.print("[dim]Next steps:[/dim]")
        console.print("[dim]  fp mcp                  Configure Claude Desktop[/dim]")
        console.print("[dim]  fp status              Show data counts[/dim]")
        console.print()


def print_results(results: List[Dict], quiet: bool = False, console=None, *, show_next_steps: bool = True):
    """Pretty print pipeline results as a Rich table."""
    if quiet:
        return

    from rich.console import Console
    from rich.table import Table

    if console is None:
        console = Console()

    console.print()
    table = Table(show_header=True, header_style="bold", title="Pipeline Results")
    table.add_column("Stage", style="cyan")
    table.add_column("Status")
    table.add_column("Time", justify="right")
    table.add_column("Details", style="dim")

    status_styles = {
        "completed": "[green]OK[/green]",
        "completed_with_errors": "[yellow]WARN[/yellow]",
        "info": "[blue]info[/blue]",
        "skipped": "[yellow]skip[/yellow]",
        "error": "[red]FAIL[/red]",
    }

    for result in results:
        stage = result.get("stage", "unknown")
        status = result.get("status", "unknown")
        elapsed = result.get("elapsed_seconds", 0)
        status_text = status_styles.get(status, f"[dim]{status}[/dim]")
        details = _stage_detail_string(result)

        if status == "error":
            error_msg = result.get("error", "")
            if error_msg:
                details = str(error_msg)[:200]

        table.add_row(stage, status_text, f"{elapsed:.1f}s", details)

    console.print(table)
    _print_completion_summary(console, results, show_next_steps=show_next_steps)
