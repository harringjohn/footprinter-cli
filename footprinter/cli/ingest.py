"""fp ingest — pipeline execution, import, and refresh.

Thin routing layer that delegates to existing orchestrator/analysis classes.
All heavy imports are deferred inside handler functions to keep ``fp --help`` fast.
"""

import sys

from footprinter.cli._common import FORMATTER, add_json_flag, console, output_json

# ---------------------------------------------------------------------------
# argparse registration
# ---------------------------------------------------------------------------


def _build_parser(subparsers, name):
    """Build and return the ingest parser."""
    parser = subparsers.add_parser(
        name,
        help="Run the data ingest pipeline",
        description=(
            "Execute the data pipeline or manage pipeline operations.\n\n"
            "By default, runs all sources incrementally (new/updated only).\n"
            "Use --full to re-process everything. Use 'refresh <source>'\n"
            "to run a single source. The --pipe flag is available for\n"
            "power users who need to target specific internal pipes."
        ),
        epilog=(
            "examples:\n"
            "  fp ingest                              All sources (incremental)\n"
            "  fp ingest --full                       All sources (full re-process)\n"
            "  fp ingest refresh local                Re-scan local files (incremental)\n"
            "  fp ingest refresh all --full            Re-scan all sources (full)\n"
            "  fp ingest --pipe local_files,browser   Specific internal pipes\n"
            "  fp ingest                              Run file + metadata ingest\n"
            "  fp ingest status                       Show pipeline diagnostics\n"
            "  fp ingest import export.zip            Import a chat export"
        ),
        formatter_class=FORMATTER,
    )

    # Pipeline flags (on the parent parser, not sub-subparsers)
    parser.add_argument(
        "--pipe",
        "-s",
        type=str,
        metavar="PIPE",
        help="Comma-separated pipes to run (e.g. local_files,browser)",
    )
    parser.add_argument(
        "--full",
        "-f",
        action="store_true",
        help="Full mode: re-process everything (default: incremental)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress Rich output (for scripts and cron)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging to file",
    )

    # Sub-subparsers for ingest actions
    subs = parser.add_subparsers(dest="ingest_action", metavar="COMMAND", title="commands (one required)")

    # status (deprecated — use fp status)
    status_p = subs.add_parser(
        "status",
        help="[deprecated] Use 'fp status' instead",
        description="[DEPRECATED] Use 'fp status' instead.\n\nShow data counts and pipeline health diagnostics.",
        formatter_class=FORMATTER,
    )
    add_json_flag(status_p)

    # import
    import_p = subs.add_parser(
        "import",
        help="Import a chat export",
        description=(
            "Import a Claude or ChatGPT chat export.\n\n"
            "Accepts .zip files or extracted directories. Duplicate\n"
            "imports are detected and skipped."
        ),
        epilog=("examples:\n  fp ingest import ~/Downloads/claude-export.zip\n  fp ingest import ./extracted-chats/"),
        formatter_class=FORMATTER,
    )
    import_p.add_argument("path", help="Path to .zip file or extracted directory")
    import_p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress UI and summary output",
    )

    # refresh
    refresh_p = subs.add_parser(
        "refresh",
        help="Re-scan a data source (default: incremental)",
        description=(
            "Re-scan a data source, processing new and updated entries.\n\n"
            "Valid sources: local, browser, chat, and connector sources.\n"
            "Some sources require connectors. See fp connect list.\n"
            "Use --full to re-process everything."
        ),
        epilog=(
            "examples:\n"
            "  fp ingest refresh local         Re-scan local files (incremental)\n"
            "  fp ingest refresh local --full  Re-scan local files (full)\n"
            "  fp ingest refresh browser       Re-scan browser history\n"
            "  fp ingest refresh all           Re-scan everything"
        ),
        formatter_class=FORMATTER,
    )
    refresh_p.add_argument("source", help="Source to refresh (e.g. local, browser, chat, all)")
    refresh_p.add_argument(
        "--full", "-f", action="store_true", help="Full mode: re-process everything (default: incremental)"
    )

    return parser


def register(subparsers) -> None:
    """Register the ``ingest`` command."""
    ingest_parser = _build_parser(subparsers, "ingest")
    ingest_parser.set_defaults(func=_handle_ingest)


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------


def _handle_ingest(args) -> None:
    """Route to the correct handler based on args."""
    action = getattr(args, "ingest_action", None)

    if action is None:
        # Bare `fp ingest` or `fp ingest --pipe/--full`
        _ingest_pipeline(args)
        return

    handlers = {
        "status": _ingest_status,
        "import": _ingest_import,
        "refresh": _ingest_refresh,
    }
    handler = handlers.get(action)
    if handler:
        handler(args)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _print_source_banner(config: dict, *, pipes=None, console=None):
    """Print a startup banner listing active and inactive data sources.

    When ``pipes`` is a list (from ``--pipe``), sources whose pipe names are
    not in the list are omitted entirely. When ``pipes`` is None, every
    configured source is shown as today.
    """
    if console is None:
        from footprinter.cli._common import console as _console

        console = _console

    from footprinter.connectors import discover_connectors, is_configured, is_installed

    pipe_set = set(pipes) if pipes is not None else None
    show_local = pipe_set is None or bool(pipe_set & {"local_files", "local_folders"})
    show_browser = pipe_set is None or "browser" in pipe_set

    console.print("[bold]Sources:[/bold]")
    if show_local:
        if config.get("directories"):
            console.print("  [green]\u2713[/green] Local files")
        else:
            console.print("  [dim]\u2022 Local files  (no directories configured)[/dim]")
    if show_browser:
        if config.get("browsers"):
            console.print("  [green]\u2713[/green] Browser history")
        else:
            console.print("  [dim]\u2022 Browser history  (no browsers configured)[/dim]")

    for name, spec in discover_connectors().items():
        if pipe_set is not None and not (pipe_set & set(spec.pipes)):
            continue
        if is_installed(spec) and is_configured(spec, config):
            console.print(f"  [green]\u2713[/green] {spec.description}")
        else:
            console.print(f"  [dim]\u2022 {spec.description}  (fp connect install {name})[/dim]")

    console.print()


def _run_with_logging(
    orchestrator,
    *,
    pipes=None,
    refresh_source=None,
    mode,
    quiet=False,
    verbose=False,
    header="Footprinter Data Pipeline",
    show_banner=False,
    show_next_steps=True,
    scan_roots=None,
):
    """Shared run helper: Rich Progress, file logging, run record, cleanup.

    Dispatch target:
      - ``refresh_source`` set → orchestrator.run_refresh(refresh_source)
      - ``pipes`` set → orchestrator.run_pipes(pipes)
      - neither → orchestrator.run_pipeline("all")

    Shows a stage counter ("Stage 2/5: local_files") and intra-stage
    progress counts for adapters that report them via on_progress.
    """
    import fcntl
    import logging
    from datetime import datetime, timezone

    from footprinter.ingest.run_record import save_run_record
    from footprinter.ingest.status import print_results
    from footprinter.paths import get_run_lock_path, get_run_logs_dir, prune_run_logs
    from footprinter.utils.logging_config import add_file_handler

    # Acquire run lock (prevents concurrent fp ingest)
    lock_path = get_run_lock_path()
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fd.close()
        console.print("[red]Error:[/red] Another fp ingest is already in progress.")
        sys.exit(1)

    started_at = datetime.now(timezone.utc)
    results = []
    progress = None
    file_handler = None

    try:
        # Prune old run logs before creating a new one
        prune_run_logs()

        # Set up file logging
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = get_run_logs_dir() / f"run_{timestamp}.log"
        file_handler = add_file_handler(
            log_path,
            level=logging.DEBUG if verbose else logging.INFO,
        )
        logging.getLogger("footprinter").info(
            "Pipeline started: mode=%s, header=%s",
            mode,
            header,
        )

        # Resolve stage list for counter display
        if refresh_source is not None:
            stage_list = orchestrator.refresh_pipes.get(refresh_source, [])
        elif pipes is not None:
            stage_list = pipes
        else:
            stage_list = orchestrator.runner.pipelines.get("all", [])
        total_stages = len(stage_list)
        stage_index = [0]  # mutable counter for closures
        current_task = [None]  # track active progress task

        # Rich Progress (unless quiet)
        if not quiet:
            from rich.progress import (
                BarColumn,
                MofNCompleteColumn,
                Progress,
                SpinnerColumn,
                TextColumn,
            )

            console.print()
            console.print(f"[bold]{header}[/bold]  [dim]({mode})[/dim]")
            console.print()

            if show_banner:
                _print_source_banner(orchestrator.config, pipes=pipes, console=console)

            progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                console=console,
                transient=True,
            )
            progress.start()

        def on_start(stage):
            """Log and show progress task when a pipeline stage begins."""
            stage_index[0] += 1
            logging.getLogger("footprinter").info("Stage started: %s", stage)
            if progress is not None:
                label = f"Stage {stage_index[0]}/{total_stages}: [cyan]{stage}[/cyan]"
                current_task[0] = progress.add_task(label, total=None)

        def on_progress(count):
            """Update intra-stage progress count."""
            if progress is not None and current_task[0] is not None:
                progress.update(current_task[0], completed=count)

        def on_end(stage, result):
            """Log result, remove progress task, and print result line."""
            from footprinter.ingest.status import _stage_detail_string

            results.append(result)
            status = result.get("status", "unknown")
            elapsed = result.get("elapsed_seconds", 0)
            logging.getLogger("footprinter").info(
                "Stage ended: %s status=%s elapsed=%.1fs",
                stage,
                status,
                elapsed,
            )

            # Remove progress task before printing result line
            if progress is not None and current_task[0] is not None:
                progress.remove_task(current_task[0])
                current_task[0] = None

            if status in ("completed", "info"):
                icon = "[green]\u2713[/green]"
            elif status == "completed_with_errors":
                icon = "[yellow]\u26a0[/yellow]"
            elif status == "error":
                icon = "[red]\u2717[/red]"
            elif status == "skipped":
                icon = "[dim]\u25cb[/dim]"
            else:
                icon = "[dim]?[/dim]"

            if not quiet:
                details = _stage_detail_string(result)
                detail_part = f"  {details}" if details else ""
                console.print(f"  {icon} {stage}{detail_part}  [dim]({elapsed:.1f}s)[/dim]")

        if refresh_source is not None:
            orchestrator.run_refresh(
                refresh_source,
                on_pipe_start=on_start,
                on_pipe_end=on_end,
                on_progress=on_progress,
            )
        elif pipes:
            orchestrator.run_pipes(
                pipes,
                on_pipe_start=on_start,
                on_pipe_end=on_end,
                on_progress=on_progress,
                scan_roots=scan_roots,
            )
        else:
            orchestrator.run_pipeline(
                "all",
                on_pipe_start=on_start,
                on_pipe_end=on_end,
                on_progress=on_progress,
            )

        if progress is not None:
            progress.stop()

        # Save run record
        record_path = save_run_record(results, mode=mode, started_at=started_at)
        logging.getLogger("footprinter").info("Run record saved to %s", record_path)

        print_results(results, quiet=quiet, show_next_steps=show_next_steps)

        if not quiet:
            console.print(f"[dim]Log: {log_path}[/dim]")

        return results

    except ValueError:
        if progress is not None:
            progress.stop()
        raise
    except KeyboardInterrupt:
        if progress is not None:
            progress.stop()
        record_path = save_run_record(results, mode=mode, started_at=started_at, interrupted=True)
        logging.getLogger("footprinter").info("Run record saved to %s", record_path)
        raise
    finally:
        lock_fd.close()
        if file_handler:
            logging.root.removeHandler(file_handler)
            file_handler.close()
        orchestrator.close()


def _extract_touched_file_ids(results) -> list:
    """Extract touched_file_ids from the local_files stage result."""
    if not results:
        return []
    for r in results:
        if r.get("stage") == "local_files":
            return r.get("touched_file_ids") or []
    return []


def _ingest_pipeline(args) -> None:
    """Execute the data pipeline: bare ingest, --pipe, --full."""
    from footprinter.ingest.orchestrator import DataPipelineOrchestrator

    pipe_str = getattr(args, "pipe", None)
    pipes = [s.strip() for s in pipe_str.split(",")] if pipe_str else None

    orchestrator = DataPipelineOrchestrator()
    orchestrator.full_mode = getattr(args, "full", False)
    quiet = getattr(args, "quiet", False)
    verbose = getattr(args, "verbose", False)
    mode_str = "full" if orchestrator.full_mode else "incremental"

    if pipes is not None:
        try:
            orchestrator.runner.validate_pipes(pipes)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    try:
        results = _run_with_logging(
            orchestrator,
            pipes=pipes,
            mode=mode_str,
            quiet=quiet,
            verbose=verbose,
            show_banner=True,
        )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("[dim]Interrupted.[/dim]")
        sys.exit(130)

    if pipes is None or "local_files" in pipes:
        from footprinter.cli._vectorize_stage import run_vectorization_stage

        file_ids = _extract_touched_file_ids(results) if pipes is not None else None
        run_vectorization_stage(quiet=quiet, file_ids=file_ids)


def _ingest_status(args) -> None:
    """Show pipeline diagnostics (data counts). DEPRECATED: use fp status."""
    from rich.console import Console as _C

    _C(stderr=True).print(
        "[yellow]Warning:[/yellow] 'fp ingest status' is deprecated. "
        "Use [bold]fp status[/bold] instead.",
    )

    from footprinter.paths import get_db_path

    db_path = get_db_path()
    if not db_path.exists():
        if getattr(args, "json", False):
            output_json({})
        else:
            console.print("[dim]No database found. Run [bold]fp ingest[/bold] to start indexing.[/dim]")
        return

    from footprinter.ingest.status import get_status, print_status

    status = get_status(str(db_path))

    if getattr(args, "json", False):
        output_json(status)
    else:
        print_status(status, quiet=getattr(args, "quiet", False))


def _ingest_import(args) -> None:
    """Import a chat export file."""
    from pathlib import Path

    from footprinter.ingest.chat_indexer import ChatIndexer
    from footprinter.ingest.database import Database
    from footprinter.paths import get_db_path

    quiet = getattr(args, "quiet", False)

    try:
        db = Database(str(get_db_path()))
        manager = ChatIndexer(db)
        result = manager.upload(Path(args.path), console=None if quiet else console)

        status = result.get("status", "unknown")
        if not quiet:
            if status == "duplicate":
                prev = result.get("previous_upload", {})
                console.print(
                    f"[yellow]Already imported[/yellow] (uploaded {prev.get('uploaded_at', 'unknown')})"
                )
            else:
                added = result.get("chats_added", 0)
                updated = result.get("chats_updated", 0)
                messages = result.get("messages_imported", 0)
                errors = result.get("errors", 0)
                console.print(
                    f"[green]Imported[/green] {added + updated} chats"
                    f" ({added} new, {updated} updated), {messages} messages"
                )
                if errors:
                    console.print(f"[yellow]Warning:[/yellow] {errors} chats failed to import")
    except Exception as e:
        if not quiet:
            console.print(f"[red]Import failed:[/red] {e}")
        sys.exit(1)


def _ingest_refresh(args) -> None:
    """Re-scan a data source."""
    from footprinter.ingest.orchestrator import DataPipelineOrchestrator

    orchestrator = DataPipelineOrchestrator()
    refresh_pipes = orchestrator.refresh_pipes

    source = args.source
    valid_sources = list(refresh_pipes.keys())

    # Early source validation (before lock/log setup) for a clean exit on bad input.
    if source not in refresh_pipes:
        console.print(f"[red]Error:[/red] Unknown refresh source: {source}")
        console.print(f"Valid sources: {', '.join(valid_sources)}")
        sys.exit(1)

    stages = refresh_pipes[source]
    orchestrator.full_mode = getattr(args, "full", False)
    quiet = getattr(args, "quiet", False)
    verbose = getattr(args, "verbose", False)
    mode_str = "full" if orchestrator.full_mode else "incremental"

    try:
        results = _run_with_logging(
            orchestrator,
            refresh_source=source,
            mode=mode_str,
            quiet=quiet,
            verbose=verbose,
            header=f"Footprinter Refresh  source={source}, {len(stages)} stages",
        )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("[dim]Interrupted.[/dim]")
        sys.exit(130)

    if "local_files" in stages:
        from footprinter.cli._vectorize_stage import run_vectorization_stage

        file_ids = _extract_touched_file_ids(results)
        run_vectorization_stage(quiet=quiet, file_ids=file_ids)
