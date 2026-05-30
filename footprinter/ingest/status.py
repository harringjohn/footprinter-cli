"""Status reporting — terminal status display and stage detail formatting."""

from typing import Dict, List


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
        console.print("[dim]  fp setup mcp --claude    Configure Claude Desktop[/dim]")
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
