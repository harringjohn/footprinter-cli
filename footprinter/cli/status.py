"""
Lightweight terminal status command for Footprinter.

Shows data counts, source health, and last run info using rich tables.
No web/FastAPI dependencies required.

Usage:
    fp status                                  # Rich formatted output
    fp status --json                           # Machine-readable JSON
    fp status --last-run                       # Last pipeline run details
    python -m footprinter.cli.status
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from footprinter.connectors import discover_connectors, is_installed, resolve_hook
from footprinter.db.status import get_entity_status_breakdown
from footprinter.paths import get_chroma_path, get_config_path, get_db_path
from footprinter.source_registry import get_config

console = Console()

# Column ordering for the Entity Counts table: current statuses on the left,
# legacy values pushed right so migration drift reads as a visual outlier.
_CURRENT_STATUS_ORDER = ("listed", "unlisted", "removed")
_LEGACY_STATUS_ORDER = ("active", "hidden")


def get_data_counts(db_path: Path) -> dict:
    """Query database for all data counts. Each query wrapped in try/except."""
    counts: dict = {}

    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    try:
        return _query_all_counts(cursor, counts)
    finally:
        conn.close()


def _query_all_counts(cursor, counts: dict) -> dict:
    """Run all count queries. Separated for try/finally in caller."""
    # Files by source
    try:
        cursor.execute(
            """
            SELECT source, COUNT(*) as count, SUM(size_bytes) as size
            FROM files WHERE status = 'listed'
            GROUP BY source
            """
        )
        counts["files"] = {
            row["source"]: {
                "count": row["count"],
                "size_mb": round((row["size"] or 0) / 1024 / 1024, 1),
            }
            for row in cursor.fetchall()
        }
    except sqlite3.OperationalError:
        counts["files"] = {}

    # Total files
    try:
        cursor.execute("SELECT COUNT(*) FROM files WHERE status = 'listed'")
        counts["files_total"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["files_total"] = 0

    # Folders by source
    try:
        cursor.execute(
            """
            SELECT source, COUNT(*) as count
            FROM folders WHERE status = 'listed'
            GROUP BY source
            """
        )
        counts["folders"] = {row["source"] or "local": row["count"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        counts["folders"] = {}

    # Browser visits
    try:
        cursor.execute("SELECT COUNT(*) FROM visits")
        counts["visits"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["visits"] = 0

    # Emails
    try:
        cursor.execute("SELECT COUNT(*) FROM emails")
        counts["emails"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["emails"] = 0

    # Chats by account
    try:
        cursor.execute("SELECT account, COUNT(*) as count FROM chats GROUP BY account")
        counts["chats"] = {row["account"]: row["count"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        counts["chats"] = {}

    # Chat messages
    try:
        cursor.execute("SELECT COUNT(*) FROM messages")
        counts["messages"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["messages"] = 0

    # Top chats by message count
    try:
        cursor.execute(
            """
            SELECT title, message_count, created_at
            FROM chats
            ORDER BY message_count DESC
            LIMIT 5
            """
        )
        counts["top_chats"] = [
            {
                "title": row["title"],
                "message_count": row["message_count"],
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        counts["top_chats"] = []

    # Chat date range
    try:
        cursor.execute("SELECT MIN(created_at) as earliest, MAX(created_at) as latest FROM chats")
        row = cursor.fetchone()
        counts["chat_date_range"] = {
            "earliest": row["earliest"] if row else None,
            "latest": row["latest"] if row else None,
        }
    except sqlite3.OperationalError:
        counts["chat_date_range"] = {"earliest": None, "latest": None}

    # Remote source accounts (for display labels in print_status)
    try:
        cursor.execute("SELECT name, account FROM sources WHERE source_type = 'remote'")
        counts["remote_source_accounts"] = {row["name"]: row["account"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        counts["remote_source_accounts"] = {}

    # Recently modified files
    try:
        cursor.execute(
            """
            SELECT name, source, modified_at
            FROM files WHERE status = 'listed'
            ORDER BY modified_at DESC
            LIMIT 10
            """
        )
        counts["recent_files"] = [
            {
                "name": row["name"],
                "source": row["source"],
                "modified_at": row["modified_at"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        counts["recent_files"] = []

    # Recent uploads
    try:
        cursor.execute(
            """
            SELECT filename, type, status, items_added, uploaded_at
            FROM uploads
            ORDER BY uploaded_at DESC
            LIMIT 5
            """
        )
        counts["recent_uploads"] = [
            {
                "filename": row["filename"],
                "type": row["type"],
                "status": row["status"],
                "items_added": row["items_added"],
                "uploaded_at": row["uploaded_at"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        counts["recent_uploads"] = []

    # Last ingest run — prefer the aggregate (pipe='all') row written by
    # IngestService.run_pipes; fall back to the most recent per-pipe row for
    # databases ingested before the aggregate was introduced.
    try:
        cursor.execute(
            """
            SELECT pipe, started_at, completed_at, mode,
                   items_processed, errors, status, elapsed_seconds
            FROM ingests
            WHERE pipe = 'all' AND status != 'running' AND mode IS NOT NULL
            ORDER BY completed_at DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                """
                SELECT pipe, started_at, completed_at, mode,
                       items_processed, errors, status, elapsed_seconds
                FROM ingests
                WHERE status != 'running' AND mode IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
        if row:
            elapsed = row["elapsed_seconds"]
            if elapsed is None and row["started_at"] and row["completed_at"]:
                try:
                    start = datetime.fromisoformat(row["started_at"])
                    end = datetime.fromisoformat(row["completed_at"])
                    elapsed = round((end - start).total_seconds(), 1)
                except (ValueError, TypeError):
                    pass
            counts["last_run"] = {
                "mode": row["mode"] or "unknown",
                "pipe": row["pipe"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "items_processed": row["items_processed"] or 0,
                "errors": row["errors"] or 0,
                "status": row["status"],
                "elapsed_seconds": elapsed,
            }
        else:
            counts["last_run"] = None
    except sqlite3.OperationalError:
        counts["last_run"] = None

    # Per-entity status breakdown — JSON keeps only non-zero by_status entries
    # to match the documented JSON contract (Rich rendering fills zeros itself).
    counts["entity_breakdown"] = get_entity_status_breakdown(cursor.connection)

    return counts


def get_source_health(config: Optional[dict]) -> dict:
    """Check source health via connector hooks and built-in checks."""
    health: dict = {}

    # Dynamic connector health via ConnectorSpec.health_check hooks
    connector_rows: list[dict] = []
    for name, spec in discover_connectors().items():
        if is_installed(spec) and spec.health_check:
            try:
                fn = resolve_hook(spec.health_check)
                if fn and config:
                    connector_rows.extend(fn(config))
            except Exception:
                pass
    health["connector_rows"] = connector_rows
    health["remote_enabled"] = len(connector_rows) > 0

    # Semantic search — config-aware health check
    config_enabled = config.get("semantic", {}).get("file_vectorization", False) if config else False
    try:
        from footprinter.semantic.vector_store import (
            VectorStore,
            _semantic_available,
        )

        installed = _semantic_available()
    except ImportError:
        installed = False
        VectorStore = None  # type: ignore[assignment]

    if not config_enabled:
        health["semantic"] = {"enabled": False, "installed": installed, "available": False}
    elif not installed:
        health["semantic"] = {"enabled": True, "installed": False, "available": False}
    elif not get_chroma_path().exists():
        health["semantic"] = {"enabled": True, "installed": True, "available": False}
    else:
        try:
            vs = VectorStore.get_instance()
            file_stats = vs.get_file_stats()
            conv_stats = vs.get_chat_stats()
            health["semantic"] = {
                "enabled": True,
                "installed": True,
                "available": True,
                "file_chunks": file_stats.get("total_chunks", 0),
                "chat_docs": conv_stats.get("total_documents", 0),
            }
        except Exception:
            health["semantic"] = {
                "enabled": True,
                "installed": True,
                "available": False,
            }

    return health


def format_relative_time(dt_str: Optional[str]) -> str:
    """Convert ISO datetime string to relative time like '2 hours ago'."""
    if not dt_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())

        if seconds < 0:
            return "just now"
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 30:
            return f"{days}d ago"
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "unknown"


def visible_totals(counts: dict, health: dict) -> dict:
    """Compute file/folder totals from visible sources only.

    When no remote connector is enabled, remote sources are excluded so
    totals match the displayed breakdown.
    Returns ``{"files": int, "folders": int, "size_mb": float}``.
    """
    files = counts.get("files", {})
    folders = counts.get("folders", {})
    remote_accounts = counts.get("remote_source_accounts", {})
    remote_enabled = health.get("remote_enabled", False)

    if remote_enabled:
        vis_files = files
        vis_folders = folders
    else:
        vis_files = {k: v for k, v in files.items() if k not in remote_accounts}
        vis_folders = {k: v for k, v in folders.items() if k not in remote_accounts}

    return {
        "files": sum(info["count"] for info in vis_files.values()),
        "folders": sum(vis_folders.values()),
        "size_mb": sum(info["size_mb"] for info in vis_files.values()),
    }


def _ordered_status_columns(breakdown: dict) -> list[str]:
    """Union of status keys across entities in current → legacy → other order."""
    present = set()
    for info in breakdown.values():
        present.update(info.get("by_status", {}).keys())

    ordered: list[str] = []
    for status in _CURRENT_STATUS_ORDER:
        if status in present:
            ordered.append(status)
    for status in _LEGACY_STATUS_ORDER:
        if status in present:
            ordered.append(status)
    extras = sorted(present - set(_CURRENT_STATUS_ORDER) - set(_LEGACY_STATUS_ORDER))
    ordered.extend(extras)
    return ordered


def _print_entity_counts(counts: dict) -> None:
    """Render the per-entity status breakdown as a Rich table."""
    breakdown = counts.get("entity_breakdown") or {}
    if not breakdown:
        return

    columns = _ordered_status_columns(breakdown)
    table = Table(show_header=True, header_style="bold", title="Entity Counts")
    table.add_column("Entity", style="cyan")
    table.add_column("Total", justify="right")
    for status in columns:
        table.add_column(status.title(), justify="right")

    for entity, info in breakdown.items():
        by_status = info.get("by_status", {})
        row = [entity, f"{info.get('total', 0):,}"]
        row.extend(f"{by_status.get(status, 0):,}" for status in columns)
        table.add_row(*row)

    console.print(table)


def _print_source_health(health: dict) -> None:
    """Render the Source Health table. Skip entirely if no rows would appear."""
    connector_rows = health.get("connector_rows", [])
    semantic = health.get("semantic", {})

    # Early return if nothing to show
    if not (connector_rows or semantic.get("enabled")):
        return

    health_table = Table(show_header=True, header_style="bold", title="Source Health")
    health_table.add_column("Source", style="cyan")
    health_table.add_column("Status")

    # Connector rows — provided dynamically by connector health_check hooks
    for row in connector_rows:
        health_table.add_row(row["source"], row["status"])

    # Semantic Search
    if semantic.get("enabled"):
        if not semantic.get("installed"):
            health_table.add_row(
                "Semantic Search",
                "[yellow]missing deps[/yellow] — pip install footprinter-cli[semantic]",
            )
        elif not semantic.get("available"):
            health_table.add_row(
                "Semantic Search",
                "[yellow]enabled[/yellow] — run fp ingest to build index",
            )
        else:
            chunks = semantic.get("file_chunks", 0)
            docs = semantic.get("chat_docs", 0)
            health_table.add_row(
                "Semantic Search (files)",
                f"[green]active[/green]  {chunks:,} chunks",
            )
            health_table.add_row(
                "Semantic Search (chats)",
                f"[green]active[/green]  {docs:,} docs",
            )

    console.print(health_table)


def print_status(data: dict, health: dict) -> None:
    """Render status with rich panels and tables."""
    db_path = data["database"]["path"]
    db_size = data["database"]["size_mb"]
    config_path = data["config"]["path"]
    config_exists = data["config"]["exists"]

    # Section 1: Header panel
    header_lines = [f"[bold]Database:[/bold]  {db_path} ({db_size:.1f} MB)"]
    config_status = config_path if config_exists else f"{config_path} [dim](not found)[/dim]"
    header_lines.append(f"[bold]Config:[/bold]    {config_status}")
    console.print(Panel("\n".join(header_lines), title="Footprinter Status", expand=False))

    counts = data["counts"]

    # Section 2: Source health (skip if no connectors configured)
    _print_source_health(health)

    # Section 2.5: Per-entity status breakdown (FPR-1720)
    _print_entity_counts(counts)

    # Section 3: Data counts table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Source", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Size", justify="right")

    files = counts.get("files", {})
    folders = counts.get("folders", {})
    remote_accounts = counts.get("remote_source_accounts", {})
    remote_enabled = health.get("remote_enabled", False)

    totals = visible_totals(counts, health)
    total_folder_count = totals["folders"]
    total_file_count = totals["files"]
    total_file_size = totals["size_mb"]

    # Local section
    local_folders = folders.get("local", 0)
    if local_folders:
        table.add_row("Local folders", f"{local_folders:,}", "")

    local_files = files.get("local")
    if local_files:
        table.add_row(
            "Local files",
            f"{local_files['count']:,}",
            f"{local_files['size_mb']:.1f} MB",
        )

    # Remote section (per account, rows shown with 0 counts when remote enabled)
    if remote_enabled and remote_accounts:
        # Build account → display label from connector health rows
        account_labels = {
            row["account"]: row["label"]
            for row in health.get("connector_rows", [])
            if "account" in row and "label" in row
        }
        table.add_section()
        for source_name, account in remote_accounts.items():
            display = account_labels.get(account, account)
            remote_folders = folders.get(source_name, 0)
            remote_files = files.get(source_name)
            table.add_row(
                f"Remote folders ({display})",
                f"{remote_folders:,}",
                "",
            )
            table.add_row(
                f"Remote files ({display})",
                f"{remote_files['count']:,}" if remote_files else "0",
                f"{remote_files['size_mb']:.1f} MB" if remote_files else "0.0 MB",
            )

    # Totals section
    table.add_section()
    if total_folder_count:
        table.add_row(
            "[bold]Total folders[/bold]",
            f"[bold]{total_folder_count:,}[/bold]",
            "",
        )
    table.add_row(
        "[bold]Total files[/bold]",
        f"[bold]{total_file_count:,}[/bold]",
        f"[bold]{total_file_size:.1f} MB[/bold]",
    )

    # Other data sources
    table.add_section()
    table.add_row("Browser history", f"{counts.get('visits', 0):,}", "")
    table.add_row("Emails", f"{counts.get('emails', 0):,}", "")
    table.add_row("Chat messages", f"{counts.get('messages', 0):,}", "")

    chat_total = sum(counts.get("chats", {}).values())
    if chat_total:
        table.add_row("Chats", f"{chat_total:,}", "")

    console.print(table)

    # Section 4: Recently modified files
    recent_files = counts.get("recent_files", [])
    if recent_files:
        console.print()
        files_table = Table(show_header=True, header_style="bold", title="Recently Modified Files")
        files_table.add_column("Filename", style="cyan", max_width=40)
        files_table.add_column("Source")
        files_table.add_column("Date", style="dim")
        for f in recent_files:
            files_table.add_row(
                f["name"],
                f["source"],
                format_relative_time(f["modified_at"]),
            )
        console.print(files_table)

    # Section 5: Recent uploads
    recent_uploads = counts.get("recent_uploads", [])
    if recent_uploads:
        console.print()
        upload_table = Table(show_header=True, header_style="bold", title="Recent Uploads")
        upload_table.add_column("Filename", style="cyan")
        upload_table.add_column("Type")
        upload_table.add_column("Status")
        upload_table.add_column("Items", justify="right")
        upload_table.add_column("Date", style="dim")
        for u in recent_uploads:
            status_style = "[green]" if u["status"] == "completed" else "[red]"
            upload_table.add_row(
                u["filename"],
                u["type"],
                f"{status_style}{u['status']}[/]",
                str(u["items_added"] or 0),
                format_relative_time(u["uploaded_at"]),
            )
        console.print(upload_table)

    # Section 6: Top chats (only when messages exist — metadata-only imports
    # may have chat titles but 0 actual messages)
    top_convos = counts.get("top_chats", [])
    if top_convos and counts.get("messages", 0) > 0:
        console.print()
        chat_table = Table(show_header=True, header_style="bold", title="Top Chats")
        chat_table.add_column("Title", style="cyan", max_width=50)
        chat_table.add_column("Messages", justify="right")
        chat_table.add_column("Date", style="dim")
        for conv in top_convos:
            chat_table.add_row(
                conv["title"] or "(untitled)",
                str(conv["message_count"] or 0),
                format_relative_time(conv["created_at"]),
            )
        console.print(chat_table)

    console.print()

    # Section 7: Last run footer
    last_run = data.get("last_run")
    if last_run:
        time_ago = format_relative_time(last_run.get("started_at"))
        mode = last_run.get("mode", "unknown")
        items = last_run.get("items_processed", 0)
        errors = last_run.get("errors", 0)
        elapsed = last_run.get("elapsed_seconds")
        elapsed_str = f", {elapsed}s" if elapsed is not None else ""
        console.print()
        console.print(
            f"[dim]Last ingest:[/dim] {time_ago} [dim]({mode}, {items:,} items, {errors} errors{elapsed_str})[/dim]"
        )
    else:
        console.print()
        console.print("[dim]No ingest runs recorded.[/dim]")

    console.print()


# ---------------------------------------------------------------------------
# Zero-result heuristic: stages where 0 results likely indicate a problem
# ---------------------------------------------------------------------------
_CORE_ZERO_RESULT_CHECKS: dict[str, str] = {
    "browser": "urls_indexed",
}


def _build_zero_result_checks() -> dict[str, str]:
    """Merge core checks with checks from installed connectors."""
    from footprinter.connectors import discover_connectors, is_installed

    checks = dict(_CORE_ZERO_RESULT_CHECKS)
    for spec in discover_connectors().values():
        if is_installed(spec):
            for pipe_name, count_key in spec.zero_result_checks:
                checks[pipe_name] = count_key
    return checks


def print_last_run(record: Optional[dict]) -> None:
    """Render the last pipeline run as a Rich table with zero-result warnings."""
    if record is None:
        console.print("No pipeline runs recorded.")
        return

    from footprinter.ingest.status import _stage_detail_string

    interrupted = record.get("interrupted", False)
    title = "Last Pipeline Run (interrupted)" if interrupted else "Last Pipeline Run"
    table = Table(show_header=True, header_style="bold", title=title)
    table.add_column("Stage", style="cyan")
    table.add_column("Status")
    table.add_column("Time", justify="right")
    table.add_column("Details", style="dim")

    status_icons = {
        "completed": "[green]OK[/green]",
        "completed_with_errors": "[yellow]WARN[/yellow]",
        "info": "[blue]info[/blue]",
        "skipped": "[yellow]skip[/yellow]",
        "error": "[red]FAIL[/red]",
    }

    zero_checks = _build_zero_result_checks()

    for stage_result in record.get("stages", []):
        stage = stage_result.get("stage", "unknown")
        status = stage_result.get("status", "unknown")
        elapsed = stage_result.get("elapsed_seconds", 0)
        icon = status_icons.get(status, f"[dim]{status}[/dim]")
        details = _stage_detail_string(stage_result)

        if status == "error":
            error_msg = stage_result.get("error", "")
            if error_msg:
                details = str(error_msg)[:200]

        # Zero-result warning
        count_key = zero_checks.get(stage)
        if count_key and status == "completed" and stage_result.get(count_key, -1) == 0:
            icon = "[yellow]⚠ WARNING[/yellow]"
            details = "0 results — check configuration"

        table.add_row(stage, icon, f"{elapsed:.1f}s", details)

    console.print(table)

    # Footer
    mode = record.get("mode", "unknown")
    mode_display = f"{mode} (interrupted)" if interrupted else mode
    total = record.get("total_elapsed_seconds", 0)
    started_at = record.get("started_at")
    time_ago = format_relative_time(started_at)
    console.print(f"[dim]Mode: {mode_display}  |  Total: {total:.1f}s  |  {time_ago}[/dim]")
    console.print()


def main() -> None:
    """Entry point for fp status command."""
    parser = argparse.ArgumentParser(
        description="Show Footprinter system status",
        prog="fp status",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON instead of rich tables",
    )
    parser.add_argument(
        "--last-run",
        action="store_true",
        help="Show details from the last pipeline run",
    )
    args = parser.parse_args()

    # --last-run: per-stage breakdown from run_record.py (session-level JSON cache).
    # Different from the footer's "Last ingest" which reads the ingests DB table —
    # preferring the aggregate row (pipe='all') and falling back to the most
    # recent per-pipe record for pre-aggregate databases.
    if getattr(args, "last_run", False):
        from footprinter.ingest.run_record import load_run_record

        print_last_run(load_run_record())
        return

    db_path = get_db_path()
    config_path = get_config_path()

    # Build structured data
    data: dict = {
        "database": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "size_mb": round(db_path.stat().st_size / 1024 / 1024, 1) if db_path.exists() else 0,
        },
        "config": {
            "path": str(config_path),
            "exists": config_path.exists(),
        },
    }

    if not db_path.exists():
        if args.json:
            data["counts"] = {}
            data["health"] = {}
            data["last_run"] = None
            print(json.dumps(data, indent=2, default=str))
        else:
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

    # Align files_total with visibility-filtered totals
    totals = visible_totals(counts, health)
    counts["files_total"] = totals["files"]

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print_status(data, health)


if __name__ == "__main__":
    main()
