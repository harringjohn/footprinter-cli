"""Shared post-ingest vectorization stage helper.

Phased ingest: ``fp setup`` and ``fp ingest`` print "index is ready"
once the main pipeline returns, then run this follow-up stage with its
own progress UI. Centralized here so both call sites stay consistent.
"""

from __future__ import annotations

from typing import Optional

from footprinter.cli._common import console


def _file_vectorization_in_config() -> bool:
    """Check config without touching the vector store."""
    try:
        from footprinter.source_registry import get_config

        return bool(get_config().get("semantic", {}).get("file_vectorization", False))
    except Exception:  # ConfigError, ImportError, etc. — treat as disabled
        return False


def run_vectorization_stage(*, quiet: bool = False, file_ids: "Optional[list[int]]" = None) -> None:
    """Run vectorization as a follow-up stage after the main pipeline.

    No-op when ``semantic.file_vectorization`` is disabled. On failure
    the wizard/ingest run continues — vectorization is best-effort.

    Args:
        quiet: Suppress Rich output.
        file_ids: When provided, scope vectorization to these file IDs only.
            None means broad (all unvectorized files).
    """
    if not _file_vectorization_in_config():
        return

    from footprinter.ingest.orchestrator import DataPipelineOrchestrator

    if not quiet:
        console.print()
        console.print("[green]✓ Your Footprinter index is ready to use.[/green]")
        console.print()
        console.print("[bold]Deep Read (semantic search)[/bold] is running in the background.")
        console.print(
            "A message will appear here when complete. [yellow]Do not close this window.[/yellow]"
        )

    orchestrator = DataPipelineOrchestrator()
    progress = None
    task_id = None
    try:
        if not quiet:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
            )

            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                console=console,
                transient=True,
            )
            progress.start()
            task_id = progress.add_task("[cyan]Vectorizing files[/cyan]", total=None)

        def on_progress(count: int) -> None:
            if progress is not None and task_id is not None:
                progress.update(task_id, completed=count)

        result = orchestrator.run_vectorization(on_progress=on_progress, file_ids=file_ids)

        if progress is not None:
            progress.stop()

        if quiet:
            return

        status = result.status.value
        data = result.data or {}
        if status == "skipped":
            return
        new = data.get("vectorized_new", 0)
        failed = data.get("vectorized_failed", 0)
        skipped_missing = data.get("vectorized_skipped_missing", 0)
        skipped_large = data.get("vectorized_skipped_large", 0)
        skipped_large_files = data.get("skipped_large_files") or []
        if status == "completed_with_errors" or failed:
            console.print(
                f"  [yellow]⚠[/yellow] Deep Read: {new} embedded, {failed} failed, "
                f"{skipped_missing} skipped"
                + (f", {skipped_large} too large" if skipped_large else "")
            )
        else:
            trail = ""
            if skipped_missing:
                trail += f", {skipped_missing} skipped"
            if skipped_large:
                trail += f", {skipped_large} too large"
            console.print(f"  [green]✓[/green] Deep Read complete: {new} embedded" + trail)
        if skipped_large_files:
            from footprinter.utils.paths import abbreviate_home

            console.print(
                f"  [yellow]⚠[/yellow] Skipped {len(skipped_large_files)}"
                f" file(s) over vectorize cap:"
            )
            for entry in skipped_large_files:
                size_mb = entry.get("size_bytes", 0) / (1024 * 1024)
                console.print(f"      {size_mb:>7.1f} MB  {abbreviate_home(entry.get('path', ''))}")
    except KeyboardInterrupt:
        if progress is not None:
            progress.stop()
        raise
    except Exception as e:  # Intentional broad catch: follow-up stage must not crash setup/ingest
        if progress is not None:
            progress.stop()
        if not quiet:
            console.print(f"  [yellow]Vectorization warning:[/yellow] {e}")
    finally:
        orchestrator.close()
