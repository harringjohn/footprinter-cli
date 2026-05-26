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
            "  fp ingest --rebuild-vectors            Rebuild vectors (incremental)\n"
            "  fp ingest --rebuild-vectors full       Rebuild vectors (full reset)\n"
            "  fp ingest --preview                    Pre-scan summary (no ingest)\n"
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
        "--rebuild-vectors",
        nargs="?",
        const="incremental",
        default=None,
        choices=["incremental", "sync", "full"],
        metavar="MODE",
        help=(
            "Rebuild the vector store. Modes: incremental (default, "
            "process new/modified/removed only), sync (incremental + "
            "verify counts), full (delete and rebuild everything)"
        ),
    )
    parser.add_argument(
        "--vector-source",
        choices=["files", "chats", "all"],
        default="all",
        help="Which vectors to rebuild (default: all). Only used with --rebuild-vectors",
    )
    parser.add_argument(
        "--phase",
        choices=["files", "messages", "chat_info"],
        default=None,
        help="Run a single rebuild phase (default: all). Only used with --rebuild-vectors",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help=(
            "Pre-scan configured directories and print a summary "
            "(file counts by extension, top-N largest files/directories, "
            "outliers above size threshold) without ingesting or vectorizing. "
            "In a TTY, prompts to proceed with the real ingest."
        ),
    )
    parser.add_argument(
        "--repair-fts",
        action="store_true",
        help="Drop and rebuild FTS search indexes",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging to file",
    )

    # Sub-subparsers for ingest actions
    subs = parser.add_subparsers(dest="ingest_action", metavar="COMMAND", title="commands (one required)")

    # status
    status_p = subs.add_parser(
        "status",
        help="Show pipeline diagnostics",
        description="Show data counts and pipeline health diagnostics.",
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
    # --repair-fts and --rebuild-vectors take precedence over everything
    if getattr(args, "repair_fts", False):
        from footprinter.ingest.vector_ops import _repair_fts

        _repair_fts(quiet=getattr(args, "quiet", False))
        return

    rebuild_mode = getattr(args, "rebuild_vectors", None)
    if rebuild_mode:
        from footprinter.ingest.vector_ops import _rebuild_vectors

        _rebuild_vectors(
            quiet=getattr(args, "quiet", False),
            source=getattr(args, "vector_source", "all"),
            phase=getattr(args, "phase", None),
            mode=rebuild_mode,
        )
        return

    if getattr(args, "preview", False):
        _ingest_preview(args)
        return

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


# Defaults for the preview render. Configurable via the indexing.preview_*
# config keys; tests pass plain configs without those keys.
_PREVIEW_TOP_N_DEFAULT = 10
_PREVIEW_OUTLIER_THRESHOLD_MB_DEFAULT = 50


def _stdout_is_tty() -> bool:
    """Patch point for the preview prompt: True iff the user is at a terminal.

    Wraps ``sys.stdout.isatty()`` so tests can override the result without
    having to dodge ``run_fp``'s in-test stdout swap (which would otherwise
    re-route the patch to the wrong StringIO).
    """
    return sys.stdout.isatty()


def _format_bytes(n: int) -> str:
    """Human-readable byte size (binary units)."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _render_preview_plain(summary, *, threshold_bytes: int) -> str:
    """Render a one-line, machine-friendly preview (used in --quiet mode)."""
    by_ext = summary.by_extension()
    ext_part = ", ".join(f"{ext}={n}" for ext, n in sorted(by_ext.items(), key=lambda kv: kv[1], reverse=True))
    return (
        f"preview: files={summary.total_files} bytes={summary.total_bytes} "
        f"outliers={len(summary.outliers())} threshold={threshold_bytes} {ext_part}"
    )


def _render_preview(summary, *, threshold_bytes: int, console_):
    """Render a ScanSummary to the Rich console."""
    from rich.table import Table

    console_.print()
    console_.print(
        f"[bold]Preview[/bold]  [dim]({summary.total_files} files, "
        f"{_format_bytes(summary.total_bytes)} total)[/dim]"
    )
    console_.print()

    by_ext = summary.by_extension()
    if by_ext:
        ext_table = Table(title="Files by extension", show_edge=False)
        ext_table.add_column("Extension")
        ext_table.add_column("Count", justify="right")
        for ext, count in sorted(by_ext.items(), key=lambda kv: kv[1], reverse=True):
            ext_table.add_row(ext, str(count))
        console_.print(ext_table)
        console_.print()

    top_files = summary.top_files()
    if top_files:
        files_table = Table(title=f"Top {len(top_files)} largest files", show_edge=False)
        files_table.add_column("Size", justify="right")
        files_table.add_column("Path")
        for entry in top_files:
            files_table.add_row(_format_bytes(int(entry.get("file_size") or 0)), entry["file_path"])
        console_.print(files_table)
        console_.print()

    top_dirs = summary.top_directories()
    if top_dirs:
        dirs_table = Table(title=f"Top {len(top_dirs)} largest directories", show_edge=False)
        dirs_table.add_column("Size", justify="right")
        dirs_table.add_column("Directory")
        for path, total in top_dirs:
            dirs_table.add_row(_format_bytes(total), path)
        console_.print(dirs_table)
        console_.print()

    outliers = summary.outliers()
    if outliers:
        out_table = Table(
            title=f"Outliers ≥ {_format_bytes(threshold_bytes)}",
            show_edge=False,
        )
        out_table.add_column("Size", justify="right")
        out_table.add_column("Path")
        for entry in outliers:
            out_table.add_row(_format_bytes(int(entry.get("file_size") or 0)), entry["file_path"])
        console_.print(out_table)
        console_.print()


def _ingest_preview(args) -> None:
    """Pre-scan configured directories and print a summary.

    No DB writes, no vectorization. Always prints a summary so ``--preview``
    is meaningful even in scripts: ``--quiet`` switches to a single-line
    plain-text summary, and the interactive prompt is shown only when
    ``stdout`` is a TTY and ``--quiet`` is not set.

    Acquires the same exclusive run lock as ``fp ingest`` so a preview cannot
    race a real ingest scan over the same directories.
    """
    import fcntl

    from footprinter.ingest.file_scanner import FileScanner
    from footprinter.ingest.scan_summary import ScanSummary
    from footprinter.paths import get_run_lock_path
    from footprinter.source_registry import ConfigError, get_config

    quiet = getattr(args, "quiet", False)

    try:
        config = get_config()
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    indexing = config.get("indexing", {}) or {}
    top_n = int(indexing.get("preview_top_n") or _PREVIEW_TOP_N_DEFAULT)
    threshold_mb = indexing.get("preview_size_threshold_mb")
    if threshold_mb is None:
        threshold_mb = _PREVIEW_OUTLIER_THRESHOLD_MB_DEFAULT
    threshold_bytes = int(threshold_mb) * 1024 * 1024

    lock_path = get_run_lock_path()
    lock_fd = open(lock_path, "w")
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            console.print("[red]Error:[/red] Another fp ingest is already in progress.")
            sys.exit(1)

        scanner = FileScanner(config)
        summary = ScanSummary(top_n=top_n, outlier_threshold_bytes=threshold_bytes)
        for entry in scanner.scan_all_directories(skip_hashing=True):
            summary.add(entry)
    finally:
        lock_fd.close()

    if quiet:
        print(_render_preview_plain(summary, threshold_bytes=threshold_bytes))
    else:
        _render_preview(summary, threshold_bytes=threshold_bytes, console_=console)

    if quiet or not _stdout_is_tty():
        return

    answer = input("Proceed with ingest? [y/N] ").strip().lower()
    if answer == "y":
        _ingest_pipeline(args)


def _ingest_status(args) -> None:
    """Show pipeline diagnostics (data counts)."""
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
