"""
Interactive setup wizard for Footprinter.

Guides new users through configuration in ~3 minutes.
Usage:
    fp setup                  # Run interactive wizard
    fp setup mcp --claude     # Write MCP config to Claude Desktop
"""

import argparse
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from footprinter.cli import mcp_setup
from footprinter.cli._policy_helpers import (
    get_policy_db as _get_db_connection,
)
from footprinter.cli._policy_helpers import (
    normalize_path as _normalize_path,
)
from footprinter.cli._policy_helpers import (
    seed_access_policies as _seed_access_policies,
)
from footprinter.cli._prompt import (
    PromptCancelled,
)
from footprinter.cli._prompt import (
    SafeConfirm as Confirm,
)
from footprinter.cli._prompt import (
    SafePrompt as Prompt,
)

# In-process pipeline — imported here so tests can patch them
from footprinter.cli.ingest import _run_with_logging
from footprinter.ingest.orchestrator import DataPipelineOrchestrator
from footprinter.paths import (
    get_bundled_path,
    get_chroma_path,
    get_config_path,
    get_db_path,
    get_log_path,
)
from footprinter.source_registry import ConfigError, get_config

logger = logging.getLogger(__name__)


def _load_existing_config() -> dict | None:
    """Load existing config, returning None if missing or invalid."""
    try:
        return get_config()
    except ConfigError:
        return None


console = Console()




# Common directories checked during quick start — only those that exist are included
QUICK_START_CANDIDATES = ["~/Documents", "~/Desktop", "~/Work", "~/Projects"]

# Directories offered as optional extras (not defaults)
OPTIONAL_DIRECTORIES = ["~/.claude"]

# Vectorization defaults — file types that benefit from semantic embedding
DEFAULT_FILE_TYPES = [".md", ".txt", ".pdf", ".docx"]

# Known junk patterns — (fnmatch_pattern, description) tuples
# Files matching these exist as text but contain no meaningful prose content.
# Patterns use ** glob syntax; fnmatch matches / on Unix.
KNOWN_JUNK_PATTERNS = [
    ("**/Photos Library.photoslibrary/**", "macOS Spotlight index cache"),
    ("**/.claude/debug/**", "Claude Code debug logs"),
    ("**/.claude/paste-cache/**", "Claude Code paste cache"),
    ("**/.claude/cache/**", "Claude Code cache"),
    ("**/.claude/projects/**", "Claude Code session data"),
    ("**/.claude/plans/**", "Claude Code auto-generated plans"),
    ("**/.claude/plugins/**", "Claude Code plugin cache"),
    ("**/.cci/**", "CumulusCI cache"),
    ("**/.context/**", "IDE context directories"),
    ("**/.github/**", "GitHub config and workflows"),
    ("**/.ai-dev/**", "AI dev tool directories"),
]

_SCAN_FILE_LIMIT = 50_000


def _scan_directories_for_vectorization(directories: list[str], file_types: list[str]) -> dict:
    """Scan directories for files matching file_types, detecting junk patterns.

    Returns dict with total, by_extension, junk_hits, total_after_exclusions,
    and truncated flag.
    """
    from fnmatch import fnmatch

    by_extension: dict[str, int] = {}
    junk_hits: dict[str, int] = {}
    total = 0
    truncated = False

    for directory in directories:
        expanded = os.path.expanduser(directory)
        if not os.path.isdir(expanded) or os.path.islink(expanded):
            continue
        for dirpath, _dirnames, filenames in os.walk(expanded, followlinks=False):
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in file_types:
                    continue
                total += 1
                by_extension[ext] = by_extension.get(ext, 0) + 1

                # Check junk patterns
                full_path = os.path.join(dirpath, filename)
                for pattern, _desc in KNOWN_JUNK_PATTERNS:
                    if fnmatch(full_path, pattern):
                        junk_hits[pattern] = junk_hits.get(pattern, 0) + 1
                        break  # one pattern match per file is enough

                if total >= _SCAN_FILE_LIMIT:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break

    junk_total = sum(junk_hits.values())
    return {
        "total": total,
        "by_extension": by_extension,
        "junk_hits": junk_hits,
        "total_after_exclusions": total - junk_total,
        "truncated": truncated,
    }


def get_available_browsers() -> list[str]:
    """Browsers available on the current platform (Safari is macOS-only)."""
    browsers = ["chrome"]
    if sys.platform == "darwin":
        browsers.insert(0, "safari")
    return browsers


# ---------------------------------------------------------------------------
# argparse registration (for fp CLI router)
# ---------------------------------------------------------------------------


def register(subparsers) -> None:
    """Register ``fp setup`` with its subcommands."""
    from footprinter.cli._common import FORMATTER

    parser = subparsers.add_parser(
        "setup",
        help="Configuration wizard and system setup",
        description=(
            "Interactive setup wizard and system configuration.\n\n"
            "Run with no arguments for the guided wizard (~3 minutes).\n"
            "Use flags to run specific setup tasks."
        ),
        epilog=(
            "examples:\n"
            "  fp setup                   Run the interactive wizard\n"
            "  fp setup mcp --claude      Configure MCP for Claude Desktop\n"
            "  fp setup folders add ~/Work/newdir\n"
            "\n"
            "tip: use 'fp setup <command> --help' for details on subcommands."
        ),
        formatter_class=FORMATTER,
    )
    parser.set_defaults(func=_handle_setup)

    subs = parser.add_subparsers(dest="setup_action", metavar="COMMAND", title="commands (one required)")

    # mcp
    _add_mcp_parser(subs, formatter_class=FORMATTER)

    # folders (add/remove only — list is now fp view folders)
    folders_p = subs.add_parser(
        "folders",
        help="Manage indexed folders",
        description=(
            "Add or remove directories from the indexing configuration.\n\n"
            "Use 'fp view folders' to view indexed folders."
        ),
        epilog=("examples:\n  fp setup folders add ~/Work/newproject\n  fp setup folders remove ~/Work/old"),
        formatter_class=FORMATTER,
    )
    folders_sub = folders_p.add_subparsers(dest="folders_command", metavar="COMMAND", title="commands (one required)")
    add_p = folders_sub.add_parser(
        "add",
        help="Add a directory to index",
        description="Add a directory path to the indexing configuration.",
        formatter_class=FORMATTER,
    )
    add_p.add_argument("path", help="Directory path to add")
    add_p.add_argument(
        "--no-index",
        action="store_true",
        help="Skip running the indexer after adding",
    )
    remove_p = folders_sub.add_parser(
        "remove",
        help="Remove a directory from config",
        description="Remove a directory from the indexing configuration.",
        formatter_class=FORMATTER,
    )
    remove_p.add_argument("path", help="Directory path to remove")


def _handle_setup(args) -> None:
    """Dispatch ``fp setup`` subcommands."""
    try:
        _handle_setup_inner(args)
    except (PromptCancelled, KeyboardInterrupt):
        console.print("\n[dim]Setup cancelled.[/dim]")
        sys.exit(130)


def _add_mcp_parser(subparsers, *, formatter_class=None):
    """Add the MCP subparser with --claude flag."""
    kwargs = {"help": "Configure MCP integration"}
    if formatter_class:
        kwargs.update(
            description=(
                "Configure the MCP server snippet for AI clients.\n\n"
                "Bare command prints the snippet. Use --claude to write it."
            ),
            epilog=(
                "examples:\n"
                "  fp setup mcp               Print MCP snippet for manual config\n"
                "  fp setup mcp --claude      Write to Claude Desktop config (creates backup)"
            ),
            formatter_class=formatter_class,
        )
    parser = subparsers.add_parser("mcp", **kwargs)
    parser.add_argument(
        "--claude",
        action="store_true",
        help="Write/merge snippet into Claude Desktop config (creates backup)",
    )
    return parser


def _dispatch_mcp(args) -> None:
    """Shared MCP subcommand dispatch — used by both router and main()."""
    if not mcp_setup.is_mcp_available():
        console.print("[red]MCP package not installed.[/red] Install with: pip install mcp")
        sys.exit(1)

    snippet = mcp_setup.generate_snippet()

    if getattr(args, "claude", False):
        ok = mcp_setup.write_config(snippet)
        sys.exit(0 if ok else 1)

    # Default: print snippet
    mcp_setup.print_snippet(snippet)


def _handle_setup_inner(args) -> None:
    """Inner dispatch for ``fp setup`` — separated so cancellation is caught."""
    action = getattr(args, "setup_action", None)

    if action == "mcp":
        _dispatch_mcp(args)
        return

    if action == "folders":
        cmd = getattr(args, "folders_command", None)
        if cmd == "add":
            sys.exit(folders_add(args.path, index=not args.no_index))
        elif cmd == "remove":
            sys.exit(folders_remove(args.path))
        else:
            console.print("[yellow]Usage: fp setup folders add|remove[/yellow]")
        return

    run_interactive_wizard()


# ---------------------------------------------------------------------------
# Standalone entry point (fp setup)
# ---------------------------------------------------------------------------


def main():
    """CLI entry point for fp setup."""
    parser = argparse.ArgumentParser(
        prog="fp setup",
        description="Interactive setup wizard for Footprinter",
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    _add_mcp_parser(subparsers)

    folders_parser = subparsers.add_parser(
        "folders",
        help="Manage indexed folders",
    )
    folders_sub = folders_parser.add_subparsers(dest="folders_command")
    add_parser = folders_sub.add_parser("add", help="Add a directory to index")
    add_parser.add_argument("path", help="Directory path to add")
    add_parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip running the indexer after adding",
    )
    remove_parser = folders_sub.add_parser("remove", help="Remove a directory from config")
    remove_parser.add_argument("path", help="Directory path to remove")

    args = parser.parse_args()

    if args.subcommand == "mcp":
        _dispatch_mcp(args)
        return

    if args.subcommand == "folders":
        cmd = getattr(args, "folders_command", None)
        if cmd == "add":
            sys.exit(folders_add(args.path, index=not args.no_index))
        elif cmd == "remove":
            sys.exit(folders_remove(args.path))
        else:
            folders_parser.print_help()
            return

    run_interactive_wizard()


def _print_phase(step: int, total: int, name: str):
    """Print phase progression indicator as a visual Rule."""
    console.print()
    console.print(Rule(f"[bold]Step {step} of {total} — {name}[/bold]", style="dim"))


def _choose_preset() -> dict | None:
    """Offer preset profiles. Returns preset dict or None for full/custom."""
    console.print(
        "  [bold]Quick start[/bold] — common directories "
        f"({', '.join(QUICK_START_CANDIDATES)}), no email, browser or chat history (add more later)"
    )
    console.print("  [bold]Full setup[/bold]  — choose everything yourself")
    choice = Prompt.ask("  Profile", choices=["quick", "full"], default="full")
    if choice == "quick":
        dirs = [d for d in QUICK_START_CANDIDATES if os.path.isdir(os.path.expanduser(d))]
        if not dirs:
            console.print("  [yellow]No common directories found — switching to full setup[/yellow]")
            return None
        # Surface what quick mode skips so the user isn't surprised later when
        # browser/chat/CSV results are empty.
        console.print(
            "\n  [dim]Quick start skips: browser history, chat history import, "
            "CSV import. Default content settings apply (snippets ON, semantic OFF). "
            "You can add any of these later with fp setup or fp ingest.[/dim]"
        )
        return {"directories": dirs, "browsers": []}
    return None


def run_interactive_wizard():
    """Run the full interactive setup flow.

    An unnumbered Welcome panel precedes 6 numbered steps: Data Sources,
    Content & Search, Confirm & Write, Claude Desktop, Populate, Summary.

    Access policies are seeded after the Populate if/else block so they
    run regardless of whether the user accepts indexing. The function
    handles a missing DB gracefully (returns {}). Claude Desktop runs
    before Populate so the user can restart Claude Desktop while
    indexing finishes.

    PromptCancelled and KeyboardInterrupt propagate to the caller
    (``_handle_setup``) which prints the cancellation message and
    exits with code 130.
    """
    existing = _load_existing_config()

    # Welcome is intentionally unnumbered — the panel itself is the welcome,
    # so a "Step 1 of N" rule above it would double-announce the same screen.
    welcome_extra = ""
    if existing is not None:
        welcome_extra = (
            "\n\n[bold yellow]Existing configuration detected.[/bold yellow]\n"
            "  Current settings will be shown as defaults. Only sections\n"
            "  you explicitly change will be updated."
        )
    fda_prereq = (
        "  - Full Disk Access for Safari history (System Settings > Privacy & Security)\n"
        if sys.platform == "darwin"
        else ""
    )
    console.print(
        Panel(
            "[bold]Footprinter Setup Wizard[/bold]\n\n"
            "Footprinter indexes your files, browser history, emails, "
            "and chat exports for AI-powered search and analysis.\n\n"
            "[bold]Steps:[/bold]\n"
            "  1. Data Sources — directories, browsers, chat exports, CSV import\n"
            "  2. Content & Search — snippets and semantic search\n"
            "  3. Confirm & Write — preview and save configuration\n"
            "  4. Claude Desktop — MCP integration\n"
            "  5. Populate — index your data\n"
            "  6. Summary — results and next steps"
            "\n\n[dim]Prerequisites (optional, can add later):[/dim]\n"
            + fda_prereq
            + "  - Chat exports from Claude or ChatGPT (see reference/chat-export.md)\n"
            "  - CSV import for clients/projects "
            "(templates: reference/clients-template.csv, reference/projects-template.csv)"
            + welcome_extra,
            title="fp setup",
        )
    )

    # Phase 1: Data Sources
    _print_phase(1, 6, "Data Sources")
    if existing is not None:
        preset = None  # Skip preset choice in reconfigure mode
    else:
        preset = _choose_preset()
    if preset:
        answers = {"directories": preset["directories"], "browsers": preset["browsers"]}
        connector_results = {}
        chat_export_path = None
    else:
        answers = collect_answers(existing=existing)
        connector_results = {}
        chat_export_path = collect_chat_export_path()

    # Phase 2: Content & Search
    _print_phase(2, 6, "Content & Search")
    if preset:
        semantic_answers = collect_vectorization_answers(directories=preset["directories"], quick=True)
    else:
        semantic_answers = collect_vectorization_answers(directories=answers["directories"], existing=existing)

    # Phase 3: Confirm & Write
    _print_phase(3, 6, "Confirm & Write")
    preview_config(
        answers,
        connectors=connector_results,
        chat_export_path=chat_export_path,
        semantic=semantic_answers,
    )

    if not Confirm.ask("Write this configuration?", default=True):
        console.print("[dim]Setup cancelled.[/dim]")
        return

    config = generate_config(answers, connector_results=connector_results, semantic=semantic_answers, existing=existing)
    write_config(config)

    # Phase 4: Claude Desktop
    _print_phase(4, 6, "Claude Desktop")
    mcp_configured = offer_setup_claude()

    # Phase 5: Populate
    _print_phase(5, 6, "Populate")

    # Truncate setup log before first orchestrator call
    setup_log = get_log_path()
    setup_log.parent.mkdir(parents=True, exist_ok=True)
    setup_log.write_text("")

    # Build dynamic description of what will run
    stages_desc = ["local file indexing"]
    if answers.get("browsers"):
        stages_desc.append("browser history")
    if chat_export_path:
        stages_desc.append("chat import")
    console.print(f"  This will run: {', '.join(stages_desc)}.")
    if mcp_configured:
        console.print(
            "  [dim]Tip: restart Claude Desktop while indexing runs to load the MCP server.[/dim]"
        )

    chat_result = {}
    if Confirm.ask("Index and analyze your data now?", default=True):
        try:
            run_orchestrator(answers, connector_results=connector_results)
        except Exception as e:  # Intentional broad catch: setup wizard step must not crash the wizard
            console.print(f"  [yellow]Indexing error: {e}[/yellow]")
        if chat_export_path:
            try:
                chat_result = import_chat_export(chat_export_path)
            except Exception as e:  # Intentional broad catch: setup wizard step must not crash the wizard
                console.print(f"  [yellow]Chat import error: {e}[/yellow]")
        # CSV import runs here — the orchestrator above creates the DB, so
        # _offer_csv_import_wizard can open it and insert rows. Asking earlier
        # (in Data Sources) would silently skip on fresh installs.
        _offer_csv_import_wizard()
        # Phased ingest — main pipeline returned, so the index is usable now.
        # Run vectorization with its own progress UI as a follow-up.
        from footprinter.cli._vectorize_stage import run_vectorization_stage

        run_vectorization_stage()
    else:
        console.print("  [dim]Skipped. Run later: fp ingest[/dim]")

    seed_access_policies()

    # Phase 6: Summary
    _print_phase(6, 6, "Summary")
    print_summary(
        chat_result=chat_result,
        mcp_configured=mcp_configured,
        connector_results=connector_results,
    )


def _offer_csv_import_wizard() -> None:
    """Wizard wrapper that opens the DB and calls _offer_csv_import."""
    from footprinter.cli._common import open_db

    try:
        with open_db() as conn:
            _offer_csv_import(conn)
    except SystemExit:
        # open_db exits if DB not found — not an error during setup
        console.print("  [dim]Database not ready — skipping CSV import.[/dim]")


def _offer_csv_import(conn) -> None:
    """Prompt user to import clients/projects from CSV files.

    Loops until the user enters an empty path to finish.
    Prompts the user to choose clients vs projects (both share a ``name``
    column, so headers can't reliably distinguish them). Shows a summary
    and confirms before inserting.
    """
    import csv as csv_mod

    console.print("\n[bold]Import clients/projects from CSV[/bold]")
    console.print(
        "  If you have a spreadsheet of clients or projects, paste the file path.\n"
        "  [dim]Leave blank to skip. You can import later with: fp upsert clients data.csv[/dim]"
    )

    while True:
        path_str = Prompt.ask("  CSV file path (blank to skip)", default="")
        if not path_str:
            return

        csv_path = Path(path_str).expanduser()
        if not csv_path.exists():
            console.print(f"  [red]File not found: {csv_path}[/red]")
            continue

        # Read headers to detect entity type
        try:
            with open(csv_path, encoding="utf-8", newline="") as f:
                reader = csv_mod.DictReader(f)
                headers = reader.fieldnames or []
                rows = list(reader)
        except Exception as e:  # Intentional broad catch: setup wizard step must not crash the wizard
            console.print(f"  [red]Could not read CSV: {e}[/red]")
            continue

        if not rows:
            console.print("  [dim]Empty CSV — nothing to import.[/dim]")
            continue

        # Ask which entity type this CSV holds — clients and projects both
        # carry a `name` column, so we can't infer the type from headers.
        kind = Prompt.ask(
            "  Is this a clients or projects CSV?",
            choices=["clients", "projects", "skip"],
            default="skip",
        )
        if kind == "skip":
            continue
        entity_type = "client" if kind == "clients" else "project"
        svc_name = "client_service" if entity_type == "client" else "project_service"

        from footprinter.cli.upsert import CSV_COLUMNS, _process_csv_rows

        required_cols, optional_cols, int_cols = CSV_COLUMNS[entity_type]

        # Check required columns
        missing = set(required_cols) - set(headers)
        if missing:
            console.print(f"  [red]Missing required columns: {', '.join(sorted(missing))}[/red]")
            continue

        import footprinter.services as svc

        service = getattr(svc, svc_name)

        created, updated, errors, error_details = _process_csv_rows(
            conn,
            rows,
            service,
            entity_type,
            required_cols,
            optional_cols,
            int_cols,
        )

        # Show summary
        table = Table(title=f"CSV Import — {entity_type}s")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Created", str(created))
        table.add_row("Updated", str(updated))
        table.add_row("Errors", str(errors))
        console.print(table)

        if error_details:
            for err in error_details[:5]:
                console.print(f"  [yellow]Row {err['row']}: {err['error']}[/yellow]")
            if len(error_details) > 5:
                console.print(f"  [dim]... and {len(error_details) - 5} more errors[/dim]")

        console.print(f"  [green]Imported {created} new, updated {updated} existing {entity_type}(s).[/green]")


def collect_answers(existing: dict | None = None) -> dict:
    """Gather user input via rich prompts.

    Args:
        existing: Optional existing config dict. When provided, current
                  directories and browsers are shown as defaults.

    Returns:
        Dict with keys: directories, browsers.
    """
    answers = {}

    # --- Directories ---
    console.print("\n[bold]1. Directories to scan[/bold]")
    console.print(
        "  Footprinter will scan these directories for files to index —\n"
        "  metadata, content types, and project structure.\n"
        "  [dim]Common choices: ~/Work, ~/Personal, ~/Documents[/dim]\n"
        "  [dim]Use ~ for your home directory.[/dim]"
    )

    existing_dirs = (existing or {}).get("directories", [])
    if existing_dirs:
        console.print(f"  Current directories: {', '.join(existing_dirs)}")
        if Confirm.ask("  Keep current directories?", default=True):
            directories = list(existing_dirs)
            # Still offer to add more
            console.print("  [dim]You can add more directories below (leave blank to continue).[/dim]")
            while True:
                path = Prompt.ask("  Add another directory (leave blank to finish)", default="")
                if not path:
                    break
                if Path(path).expanduser().is_dir():
                    directories.append(path)
                    console.print(f"  [green]✓[/green] Added {path}")
                else:
                    console.print(f"  [red]Directory not found: {path}[/red]")
            answers["directories"] = directories
        else:
            # User wants to re-enter directories — fall through to standard collection
            answers["directories"] = _collect_directories_from_scratch()
    else:
        answers["directories"] = _collect_directories_from_scratch()

    # --- Browsers ---
    console.print("\n[bold]2. Browser history[/bold]")
    console.print(
        "  Optionally index your browsing history for search and context.\n"
        "  [dim]You can enable this later in config.yaml.[/dim]"
    )

    existing_browsers = (existing or {}).get("browsers", [])
    if existing_browsers:
        console.print(f"  Currently enabled: {', '.join(existing_browsers)}")
        if Confirm.ask("  Keep current browser settings?", default=True):
            browsers = list(existing_browsers)
        else:
            browsers = _collect_browsers_from_scratch()
    else:
        browsers = _collect_browsers_from_scratch()
    answers["browsers"] = browsers

    return answers


def _collect_directories_from_scratch() -> list[str]:
    """Collect directories interactively from scratch."""
    while True:
        directories = []

        # Prompt for directories one at a time
        while True:
            prompt_text = (
                "  Enter directory path" if not directories else "  Add another directory (leave blank to finish)"
            )
            path = Prompt.ask(prompt_text, default="" if directories else ...)
            if not path:
                break
            expanded = os.path.expanduser(path)
            if os.path.isdir(expanded):
                directories.append(path)
                console.print(f"  [green]✓[/green] Added {path}")
            else:
                console.print(f"  [red]Directory not found: {path}[/red]")

        # Offer optional directories if they exist
        for d in OPTIONAL_DIRECTORIES:
            expanded = os.path.expanduser(d)
            if os.path.isdir(expanded):
                if d == "~/.claude":
                    console.print("  [dim]~/.claude contains Claude Code settings and chat history[/dim]")
                if Confirm.ask(f"  Include {d}?", default=False):
                    directories.append(d)

        if directories:
            return directories
        console.print("  [red]At least one directory is required.[/red]")


SAFARI_FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def _guide_safari_full_disk_access() -> None:
    """Walk the user through granting Full Disk Access for Safari history.

    Safari's History.db is protected by macOS's Full Disk Access permission.
    Selecting Safari in the wizard does nothing on its own — the user must
    add their terminal/app to Privacy & Security → Full Disk Access. The
    caller fires this helper once after all browser selections are
    collected, so the user finishes naming browsers before being walked
    through the OS permission grant.
    """
    if sys.platform != "darwin":
        return

    console.print()
    console.print(
        "  [yellow]Safari history requires Full Disk Access.[/yellow] This is a one-time\n"
        "  macOS permission grant. Without it, Safari history will return 0 rows."
    )
    console.print(
        "  Open [bold]System Settings → Privacy & Security → Full Disk Access[/bold]\n"
        "  and add the terminal or app you're running [bold]fp[/bold] from."
    )

    if Confirm.ask("  Open System Settings now?", default=True):
        subprocess.run(["open", SAFARI_FDA_URL], check=False)
        console.print(
            "  [dim]Toggle Full Disk Access on for Terminal (or iTerm, VS Code, etc.).[/dim]"
        )

    if not Confirm.ask(
        "  Press y once you've granted access (n to skip and continue without Safari history)",
        default=False,
    ):
        console.print("  [dim]Skipping — Safari history will be empty until you grant access and re-run.[/dim]")
        return

    history_db = Path(os.path.expanduser("~/Library/Safari/History.db"))
    try:
        with history_db.open("rb") as f:
            f.read(16)
        console.print("  [green]✓[/green] Safari history is readable.")
    except (PermissionError, FileNotFoundError, OSError, RuntimeError):
        console.print(
            "  [yellow]⚠[/yellow] Safari history still appears unreadable. Re-run [bold]fp setup[/bold]\n"
            "  after granting Full Disk Access if browser results are empty."
        )


def _collect_browsers_from_scratch() -> list[str]:
    """Collect browser selection, then fire FDA guidance once if Safari was chosen.

    Firing FDA after the full per-browser loop keeps cause-and-effect grouped:
    the user finishes naming all browsers they want, then deals with the
    macOS permission grant once (rather than being interrupted between
    browsers).
    """
    browsers = []
    for b in get_available_browsers():
        if Confirm.ask(f"  Include {b}?", default=True):
            browsers.append(b)
    if "safari" in browsers:
        _guide_safari_full_disk_access()
    return browsers


def _check_semantic_deps() -> bool:
    """Check semantic deps and offer pip install if missing. Return True if available."""
    from footprinter.cli.diagnostics import is_importable as _is_importable

    if _is_importable("chromadb") and _is_importable("onnxruntime"):
        return True

    console.print("\n  [yellow]Semantic search requires chromadb and onnxruntime.[/yellow]")
    if Confirm.ask("  Install now? (pip install footprinter-cli[full])", default=True):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "footprinter-cli[full]"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("  [green]✓[/green] Semantic dependencies installed.")
            return True
        else:
            console.print(f"  [red]Install failed:[/red] {result.stderr.strip()}")

    console.print("  [dim]You can enable semantic search later with fp setup.[/dim]")
    return False


def collect_vectorization_answers(
    directories: list[str],
    existing: dict | None = None,
    quick: bool = False,
) -> dict:
    """Ask about content indexing: snippets and vectorization.

    Groups all content extraction decisions into one section:
    - Content snippets: FTS keyword search previews (per entity)
    - Semantic search: vector embeddings for meaning-based search (per entity)

    Args:
        directories: Directories to scan for file type preview.
        existing: Optional existing config dict for defaults.
        quick: If True, show compact summary with auto-selected exclusions.

    Returns:
        Dict with content_snippets (bool),
        file_vectorization, chat_vectorization (bool),
        file_types (list), exclude_patterns (list).
    """
    existing_vec = (existing or {}).get("vectorization", {})
    existing_semantic = (existing or {}).get("semantic", {})
    # Fresh installs default to ON so `fp search` returns content matches, not
    # just filenames; reconfigure runs preserve the user's prior choice.
    if existing is not None and "indexing" in existing and "content_snippets" in existing["indexing"]:
        snippets_default = existing["indexing"]["content_snippets"]
    else:
        snippets_default = True
    file_types = existing_vec.get("file_types", list(DEFAULT_FILE_TYPES))
    existing_excludes = existing_vec.get("exclude_patterns", [])

    console.print("\n[bold]Content Indexing[/bold]")
    console.print("  [bold]1. Metadata only (default)[/bold]")
    console.print(
        "  Footprinter indexes filenames, paths, timestamps, and structure.\n"
        "  Nothing is read from inside your files. The option below opts in\n"
        "  to reading and storing file content for richer search.\n"
    )

    console.print("  [bold]2. Content snippets[/bold]")
    console.print(
        "  Reads each file during indexing and stores a short preview\n"
        "  (~1000 chars) in Footprinter's local database, so keyword\n"
        "  search matches file contents — not just filenames. Connector\n"
        "  plugins (e.g. Gmail) use the same flag to store body previews.\n"
        "  [bold]Local only[/bold]: previews are written to a local SQLite database\n"
        "  on your machine. Nothing is uploaded or shared, and the MCP client only sees\n"
        "  content when you grant explicit permission via fp mcp.\n"
        "  [dim]Trade-off: Footprinter keeps a stored copy of file (and connector) previews on disk.[/dim]"
    )
    content_snippets = Confirm.ask("  Enable file content snippets?", default=snippets_default)

    console.print("\n  [bold]Semantic search[/bold]")
    console.print(
        "  Stores content as embeddings in a local ChromaDB database.\n"
        "  This lets you find files and chats by meaning, not just keywords.\n"
        "  [dim]Trade-off: additional disk space (~500 MB) and longer indexing time.[/dim]"
    )

    if quick:
        result = _collect_vectorization_quick(directories, file_types, existing_excludes, existing_semantic)
    else:
        result = _collect_vectorization_full(directories, file_types, existing_excludes, existing_semantic)
    result["content_snippets"] = content_snippets
    return result


def _collect_vectorization_quick(
    directories: list[str],
    file_types: list[str],
    existing_excludes: list[str],
    existing_semantic: dict,
) -> dict:
    """Quick-mode vectorization: compact summary with auto-selected exclusions."""
    scan = _scan_directories_for_vectorization(directories, file_types)

    if scan["total"] > 0:
        junk_count = sum(scan["junk_hits"].values())
        console.print(f"\n  Found [bold]{scan['total']}[/bold] files matching {', '.join(file_types)}")
        if junk_count > 0:
            console.print(
                f"  [yellow]{junk_count} likely junk files detected[/yellow] "
                f"→ {scan['total_after_exclusions']} after exclusions"
            )

    file_default = existing_semantic.get("file_vectorization", False)
    chat_default = existing_semantic.get("chat_vectorization", False)

    file_vec = Confirm.ask("  Enable semantic search for files?", default=file_default)
    chat_vec = Confirm.ask("  Enable semantic search for chats?", default=chat_default)

    if not file_vec and not chat_vec:
        return {
            "file_vectorization": False,
            "chat_vectorization": False,
            "file_types": file_types,
            "exclude_patterns": existing_excludes,
        }

    # Auto-include detected junk exclusions
    exclude_patterns = list(existing_excludes)
    for pattern in scan["junk_hits"]:
        if pattern not in exclude_patterns:
            exclude_patterns.append(pattern)

    if not _check_semantic_deps():
        return {
            "file_vectorization": False,
            "chat_vectorization": False,
            "file_types": file_types,
            "exclude_patterns": exclude_patterns,
        }

    return {
        "file_vectorization": file_vec,
        "chat_vectorization": chat_vec,
        "file_types": file_types,
        "exclude_patterns": exclude_patterns,
    }


def _collect_vectorization_full(
    directories: list[str],
    file_types: list[str],
    existing_excludes: list[str],
    existing_semantic: dict,
) -> dict:
    """Full-mode vectorization: enable-files, file types, enable-chats, then scan.

    Question order: files → file types (only if files enabled) → chats. Asking
    the file-enable Confirm before file-type configuration avoids the
    cost-up-front-then-decline anti-pattern where the user configures an
    allowlist before being asked whether they want file embedding at all.
    File types belong with the file question, so they sit between files and
    chats rather than after both Confirms.
    """
    file_default = existing_semantic.get("file_vectorization", False)
    chat_default = existing_semantic.get("chat_vectorization", False)
    file_vec = Confirm.ask("  Enable semantic search for files?", default=file_default)

    if file_vec:
        # File type allowlist — only asked when file vectorization is on
        console.print(f"\n  File types to embed: [bold]{', '.join(file_types)}[/bold]")
        keep_types = Confirm.ask("  Keep these file types?", default=True)
        if not keep_types:
            raw = Prompt.ask("  Enter file types (comma-separated, e.g. .md, .txt, .py)")
            file_types = [t.strip() for t in raw.split(",") if t.strip()]

    chat_vec = Confirm.ask("  Enable semantic search for chats?", default=chat_default)

    if not file_vec and not chat_vec:
        return {
            "file_vectorization": False,
            "chat_vectorization": False,
            "file_types": file_types,
            "exclude_patterns": list(existing_excludes),
        }

    if not _check_semantic_deps():
        return {
            "file_vectorization": False,
            "chat_vectorization": False,
            "file_types": file_types,
            "exclude_patterns": list(existing_excludes),
        }

    # Scan and show results
    scan = _scan_directories_for_vectorization(directories, file_types)

    if scan["total"] > 0:
        console.print(f"\n  Scanned: [bold]{scan['total']}[/bold] files found")
        for ext, count in sorted(scan["by_extension"].items()):
            console.print(f"    {ext}: {count}")

    # Junk exclusions
    exclude_patterns = list(existing_excludes)
    if scan["junk_hits"]:
        console.print("\n  [yellow]Recommended exclusions:[/yellow]")
        detected_patterns = []
        for pattern, count in scan["junk_hits"].items():
            desc = next((d for p, d in KNOWN_JUNK_PATTERNS if p == pattern), pattern)
            console.print(f"    {pattern} ({count} files) — {desc}")
            detected_patterns.append(pattern)

        accept_all = Confirm.ask("  Accept recommended exclusions?", default=True)
        if accept_all:
            for p in detected_patterns:
                if p not in exclude_patterns:
                    exclude_patterns.append(p)
        else:
            for pattern in detected_patterns:
                desc = next((d for p, d in KNOWN_JUNK_PATTERNS if p == pattern), pattern)
                include = Confirm.ask(f"  Exclude {pattern}?", default=True)
                if include and pattern not in exclude_patterns:
                    exclude_patterns.append(pattern)

    if scan["total"] > 0:
        after = scan["total"] - sum(scan["junk_hits"].get(p, 0) for p in exclude_patterns)
        console.print(f"\n  Files to embed: [bold]{after}[/bold] (of {scan['total']} total)")

    return {
        "file_vectorization": file_vec,
        "chat_vectorization": chat_vec,
        "file_types": file_types,
        "exclude_patterns": exclude_patterns,
    }


def preview_config(
    answers: dict,
    console=None,
    connectors: dict = None,
    chat_export_path: str = None,
    semantic: dict = None,
):
    """Display a summary of the configuration before writing.

    Args:
        answers: Dict from collect_answers().
        console: Optional Rich Console (for testing).
        connectors: Optional connector results dict.
        chat_export_path: Optional path to a chat export file/directory.
        semantic: Optional dict from collect_vectorization_answers().
    """
    if console is None:
        console = Console()

    lines = []
    lines.append(f"Directories: {', '.join(answers.get('directories', []))}")
    browsers = answers.get("browsers", [])
    if browsers:
        lines.append(f"Browsers: {', '.join(browsers)}")
    else:
        lines.append("Browsers: [dim]none (can add later)[/dim]")
    if chat_export_path:
        lines.append(f"Chat export: {chat_export_path}")
    else:
        lines.append("Chat export: [dim]none (can add later)[/dim]")
    if semantic and (semantic.get("file_vectorization") or semantic.get("chat_vectorization")):
        parts = []
        if semantic.get("file_vectorization"):
            parts.append("files")
        if semantic.get("chat_vectorization"):
            parts.append("chats")
        lines.append(f"Semantic search: {', '.join(parts)}")
        if semantic.get("file_types"):
            lines.append(f"  File types: {', '.join(semantic['file_types'])}")
        if semantic.get("exclude_patterns"):
            lines.append(f"  Exclusion patterns: {len(semantic['exclude_patterns'])}")
    else:
        lines.append("Semantic search: [dim]disabled (can enable later)[/dim]")

    if semantic and semantic.get("content_snippets"):
        lines.append("Content snippets: files")
    else:
        lines.append("Content snippets: [dim]disabled (can enable later)[/dim]")

    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="Configuration Preview",
            border_style="dim",
            expand=False,
        )
    )
    console.print()


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base. Returns a new dict."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def generate_config(
    answers: dict,
    connector_results: dict = None,
    semantic: dict = None,
    existing: dict | None = None,
) -> dict:
    """Load config.example.yaml and apply user answers.

    Args:
        answers: Dict from collect_answers().
        connector_results: Optional dict from connector setup hooks mapping
                account names to verified service lists
                (e.g. {"personal": ["drive"]}).
        semantic: Optional dict from collect_vectorization_answers() with
                  file_vectorization and chat_vectorization bools.
        existing: Optional existing config dict. When provided, its values
                  are deep-merged on top of the template before wizard answers
                  are applied, preserving sections the user didn't change.
                  Note: source_seeds are reconciled by name (template seeds
                  kept, existing seeds overlaid) rather than replaced wholesale.

    Returns:
        Config dict ready to write as YAML.
    """
    import copy

    if connector_results is None:
        connector_results = {}

    with open(get_bundled_path("config.example.yaml"), "r") as f:
        config = yaml.safe_load(f)

    if existing is not None:
        # Save template seeds before merge (_deep_merge replaces lists wholesale)
        template_seeds = list(config.get("source_seeds", []))
        config = _deep_merge(config, copy.deepcopy(existing))
        # Reconcile source_seeds: keep all template seeds, overlay existing by name
        existing_seeds = config.get("source_seeds", [])
        by_name = {s["name"]: s for s in template_seeds}
        for s in existing_seeds:
            by_name[s["name"]] = s
        config["source_seeds"] = list(by_name.values())

    # Apply answers — these always come from explicit user input
    config["directories"] = answers.get("directories") or []
    config["browsers"] = answers.get("browsers", [])

    # Strip the placeholder API key — real key goes in .env
    if "claude" in config and "api_key" in config["claude"]:
        config["claude"]["api_key"] = "YOUR_API_KEY_HERE"

    # Apply connector config via hooks (enable flags, source_seeds, accounts)
    if connector_results:
        from footprinter.connectors import discover_connectors, resolve_hook

        for _name, spec in discover_connectors().items():
            if spec.config_apply:
                fn = resolve_hook(spec.config_apply)
                if fn:
                    fn(config, connector_results)

    # Apply semantic search settings — always ensure section exists with safe defaults
    config.setdefault("semantic", {})
    if semantic:
        config["semantic"]["file_vectorization"] = semantic.get("file_vectorization", False)
        config["semantic"]["chat_vectorization"] = semantic.get("chat_vectorization", False)
    else:
        config["semantic"].setdefault("file_vectorization", False)
        config["semantic"].setdefault("chat_vectorization", False)

    # Apply vectorization settings from the wizard (file_types, exclude_patterns)
    if semantic and "file_types" in semantic:
        config.setdefault("vectorization", {})
        config["vectorization"]["file_types"] = semantic["file_types"]
    if semantic and "exclude_patterns" in semantic:
        config.setdefault("vectorization", {})
        config["vectorization"]["exclude_patterns"] = semantic["exclude_patterns"]

    # Apply content snippets setting
    config.setdefault("indexing", {})
    if semantic and "content_snippets" in semantic:
        config["indexing"]["content_snippets"] = semantic["content_snippets"]

    return config


def write_config(config: dict, path: Path = None):
    """Write config dict to YAML file.

    Args:
        config: Config dict to write.
        path: Override output path (default: config/config.yaml).
    """
    target = path or get_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    with open(target, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    console.print(f"  Wrote [bold]{target}[/bold]")


def _run_orchestrator_stages(stages: list[str], scan_roots: list[str] | None = None):
    """Run pipeline stages in-process via the same code path as ``fp ingest``.

    Uses DataPipelineOrchestrator + ``_run_with_logging()`` directly.

    Args:
        stages: List of stage names (e.g. ["local_folders", "local_files"]).
        scan_roots: Optional override for filesystem-scanning pipes.
            When provided, local_folders/local_files scan only these paths.
            When None, all configured directories are scanned.
    """
    orchestrator = DataPipelineOrchestrator()
    try:
        _run_with_logging(
            orchestrator,
            pipes=stages,
            mode="incremental",
            quiet=False,
            header="Setup Indexing",
            show_next_steps=False,
            scan_roots=scan_roots,
        )
    except ValueError as e:
        console.print(f"[yellow]Pipeline error:[/yellow] {e}")
    except KeyboardInterrupt:
        console.print("[dim]Interrupted.[/dim]")


def run_orchestrator(answers: dict = None, connector_results: dict = None):
    """Run initial indexing stages via the in-process pipeline.

    Builds stages dynamically: always includes local_folders,local_files.
    Adds browser stage if answers contains non-empty browsers list.
    Adds connector pipes if connector_results has verified accounts.

    Args:
        answers: Dict from collect_answers(). None defaults to {}.
        connector_results: Optional dict of connector results.
    """
    if answers is None:
        answers = {}
    if connector_results is None:
        connector_results = {}

    console.print("\n[bold]Running initial indexing...[/bold]")
    stages = ["local_folders", "local_files"]
    if answers.get("browsers"):
        stages.append("browser")
    if connector_results:
        from footprinter.connectors import discover_connectors, is_installed

        for name, spec in discover_connectors().items():
            if is_installed(spec):
                stages.extend(spec.pipes)
    _run_orchestrator_stages(stages)


def collect_chat_export_path() -> str | None:
    """Prompt user for a chat export path (Phase 2 — Data Sources).

    Returns:
        Expanded path string if user provides a valid path, None otherwise.
    """
    console.print("\n[bold]3. Chat history[/bold]")
    console.print(
        "  Optionally import Claude or ChatGPT chat exports.\n"
        "  [dim]You can also import later with: fp ingest import <file>[/dim]"
    )
    if not Confirm.ask("  Do you have Claude or ChatGPT exports to import?", default=False):
        return None

    console.print("  [dim]Supported: Claude .zip export or unzipped directory[/dim]")
    path = Prompt.ask("  Path to export file (.zip or directory)")
    if not path:
        return None

    path = os.path.expanduser(path)
    resolved = Path(path)
    if not resolved.exists():
        console.print(f"  [red]File not found: {path}[/red]")
        return None

    return str(resolved)


def import_chat_export(path: str) -> dict:
    """Import a chat export from a previously collected path (Phase 5 — Populate).

    Args:
        path: Expanded path to the export file or directory.

    Returns:
        Result dict from ChatIndexer.upload(), or {} on failure.
    """
    resolved = Path(path)
    try:
        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(str(get_db_path()))
        manager = ChatIndexer(db)
        result = manager.upload(resolved)
        console.print("  [green]Chat import complete.[/green]")
        if isinstance(result, dict):
            added = result.get("chats_added", 0)
            updated = result.get("chats_updated", 0)
            msgs = result.get("messages_imported", 0)
            console.print(
                f"  Imported: [cyan]{added + updated}[/cyan] chats "
                f"({added} new, {updated} updated), "
                f"[cyan]{msgs}[/cyan] messages"
            )
        return result if isinstance(result, dict) else {}
    except Exception as e:  # Intentional broad catch: user-facing CLI; errors shown to console, not re-raised
        console.print(f"  [yellow]Chat import failed: {e}[/yellow]")
        console.print(f"  [dim]Run manually: fp ingest import {path}[/dim]")
        return {}


def offer_setup_claude() -> bool:
    """Offer to configure Claude Desktop MCP integration.

    Returns:
        True if MCP was successfully configured, False otherwise.
    """
    if not mcp_setup.is_mcp_available():
        console.print("\n[dim]MCP package not installed — skipping Claude Desktop configuration.[/dim]")
        console.print("  [dim]Install with: pip install mcp[/dim]")
        return False

    try:
        snippet = mcp_setup.generate_snippet()
    except Exception as e:  # Intentional broad catch: user-facing CLI; errors shown to console, not re-raised
        console.print(f"  [yellow]MCP setup failed: {e}[/yellow]")
        console.print("  [dim]Run manually: fp setup mcp --claude[/dim]")
        return False

    # Offer snippet for manual copy/paste (Cursor, Windsurf, etc.)
    if Confirm.ask(
        "\nView MCP config snippet (for Claude Code, Cursor, VS Code, and other clients)?",
        default=True,
    ):
        mcp_setup.print_snippet(snippet)

    # Offer Claude Desktop auto-config
    if not Confirm.ask("\nConfigure Claude Desktop automatically?", default=False):
        return False

    try:
        mcp_setup.write_config(snippet)
        console.print("  [green]Claude Desktop MCP configured.[/green]")
        return True
    except Exception as e:  # Intentional broad catch: user-facing CLI; errors shown to console, not re-raised
        console.print(f"  [yellow]MCP setup failed: {e}[/yellow]")
        console.print("  [dim]Run manually: fp setup mcp --claude[/dim]")
        return False


# _get_db_connection and _normalize_path imported from _policy_helpers


def _require_config() -> tuple[dict, Path]:
    """Load config via get_config(), exit on missing or invalid config.

    Returns:
        Tuple of (config_dict, config_path).

    Exits:
        sys.exit(1) with helpful message if config is missing or corrupt.
    """
    try:
        config = get_config()
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        sys.exit(1)

    return config, get_config_path()


def folders_add(path: str, index: bool = True) -> int:
    """Add a directory to the config and optionally trigger indexing.

    Args:
        path: Directory path to add.
        index: If True, prompt to run indexing after adding.

    Returns:
        0 on success, 1 on error.
    """
    normalized = _normalize_path(path)
    expanded = os.path.expanduser(normalized)

    config, config_path = _require_config()
    directories = config.get("directories", [])

    # Duplicate-check before existence-check: a configured path is a duplicate
    # regardless of whether the directory is currently reachable, and "already
    # configured" is more actionable than "not a directory" when both are true.
    existing_expanded = {os.path.expanduser(d) for d in directories}
    if expanded in existing_expanded:
        console.print(f"[yellow]Already configured:[/yellow] {normalized}")
        return 1

    if not os.path.isdir(expanded):
        console.print(f"[red]Not a directory or not found:[/red] {path}")
        return 1

    directories.append(normalized)
    config["directories"] = directories
    write_config(config, config_path)
    console.print(f"[green]Added:[/green] {normalized}")

    if index:
        if Confirm.ask("Run indexing for the new folder now?", default=True):
            # Scope the scan to the newly added directory so we don't
            # rewalk every configured root for a single new folder.
            _run_orchestrator_stages(
                ["local_folders", "local_files"], scan_roots=[normalized]
            )

    return 0


def folders_remove(path: str) -> int:
    """Remove a directory from the config.

    Does NOT delete files from the database — they remain as audit trail.

    Args:
        path: Directory path to remove.

    Returns:
        0 on success, 1 if path wasn't configured.
    """
    normalized = _normalize_path(path)
    expanded = os.path.expanduser(normalized)

    config, config_path = _require_config()
    directories = config.get("directories", [])

    # Filter out entries that match when expanded
    remaining = [d for d in directories if os.path.expanduser(d) != expanded]

    if len(remaining) == len(directories):
        console.print(f"[yellow]Not configured:[/yellow] {normalized}")
        return 1

    config["directories"] = remaining
    write_config(config, config_path)
    console.print(f"[green]Removed:[/green] {normalized}")
    console.print("[dim]  Note: indexed files remain in the database.[/dim]")
    return 0


def _get_indexing_counts() -> dict:
    """Query DB for folder and file counts. Returns empty dict if DB doesn't exist."""
    conn = _get_db_connection()
    if conn is None:
        return {}

    try:
        cur = conn.cursor()
        counts = {}
        for table, query in [
            ("folders", "SELECT COUNT(*) FROM folders"),
            ("files", "SELECT COUNT(*) FROM files WHERE status = 'listed'"),
            ("visits", "SELECT COUNT(*) FROM visits"),
            ("projects", "SELECT COUNT(*) FROM projects"),
            ("chats", "SELECT COUNT(*) FROM chats WHERE status = 'listed'"),
            ("messages", "SELECT COUNT(*) FROM messages WHERE status = 'listed'"),
        ]:
            try:
                cur.execute(query)
                counts[table] = cur.fetchone()[0]
            except sqlite3.OperationalError:
                counts[table] = 0
        return counts
    except Exception:  # Intentional broad catch: setup wizard display must not crash
        return {}
    finally:
        conn.close()


def seed_access_policies() -> dict:
    """Seed default access policies. Idempotent via INSERT OR IGNORE.

    Returns:
        Dict with visibility_seeded and permission_seeded bools, or {} if no DB.
    """
    conn = _get_db_connection()
    if conn is None:
        return {}

    try:
        result = _seed_access_policies(conn)

        if result.get("visibility_seeded") or result.get("permission_seeded"):
            console.print(
                "\n[bold]Access policies[/bold]: seeded defaults (visibility: visible, permission: allow)"
            )
        else:
            console.print("\n[bold]Access policies[/bold]: already configured")
        console.print("  [dim]Manage with: fp permission list[/dim]")

        console.print("\n  [dim]Visible[/dim] = AI clients can see file names, sizes, and paths")
        console.print("  [dim]Permission: allow[/dim] = AI clients can read file contents when asked")
        console.print(
            "  [dim]Security posture: fail-open (all reads allowed). "
            "See reference/mcp-access-control.md § Security Posture.[/dim]"
        )

        if Confirm.ask(
            "\n  Restrict to metadata only? (no content reading)",
            default=False,
        ):
            from footprinter.db.policies import set_permission_policy

            set_permission_policy(conn, "global", "deny")
            console.print("  [green]Switched to metadata-only access (permission: deny)[/green]")
        else:
            console.print("  [dim]Keeping full access (permission: allow)[/dim]")

        return result
    except Exception as e:  # Intentional broad catch: policy seeding is best-effort during setup
        logger.error(f"Failed to seed access policies: {e}")
        console.print(f"  [yellow]Warning: failed to seed access policies: {e}[/yellow]")
        console.print("  [dim]Run 'fp setup' later to retry[/dim]")
        return {}
    finally:
        conn.close()


def print_summary(
    chat_result: dict = None,
    mcp_configured: bool = False,
    connector_results: dict = None,
):
    """Display results table and next steps.

    Args:
        chat_result: Result dict from import_chat_export(), or None.
        mcp_configured: Whether MCP was configured during the wizard.
        connector_results: Result dict from connector setup hooks, or None.
    """
    console.print()

    table = Table(title="Setup Complete")
    table.add_column("File", style="bold")
    table.add_column("Status")

    # Config
    config_path = get_config_path()
    if config_path.exists():
        table.add_row(str(config_path), "[green]Created[/green]")
    else:
        table.add_row(str(config_path), "[red]Missing[/red]")

    # Database
    db_path = get_db_path()
    if db_path.exists():
        table.add_row(str(db_path), "[green]Ready[/green]")
    else:
        table.add_row(str(db_path), "[yellow]Not yet created[/yellow]")

    console.print(table)

    # Indexing counts
    counts = _get_indexing_counts()
    if counts:
        console.print()
        console.print(
            f"  Indexed: [cyan]{counts.get('folders', 0)}[/cyan] folders, [cyan]{counts.get('files', 0)}[/cyan] files"
        )
        browser_count = counts.get("visits", 0)
        if browser_count > 0:
            console.print(f"  Browser history: [cyan]{browser_count}[/cyan] URLs")
        chat_count = counts.get("chats", 0)
        chat_msg_count = counts.get("messages", 0)
        if chat_count > 0:
            console.print(f"  Chat: [cyan]{chat_count}[/cyan] chats, [cyan]{chat_msg_count}[/cyan] messages")
        project_count = counts.get("projects", 0)
        if project_count > 0:
            console.print(f"  Projects detected: [cyan]{project_count}[/cyan]")
            console.print("  Use [bold]fp project[/bold] and [bold]fp client[/bold] to organize your data.")

    # Getting started section
    console.print()
    console.print("[bold]Ready to explore your data:[/bold]")
    console.print('  [cyan]fp search[/cyan] [dim]"query"[/dim]          Search your files')
    console.print("  [cyan]fp ingest status[/cyan]           Show data counts")
    console.print("  [cyan]fp ingest[/cyan]                  Re-index (incremental)")
    console.print()
    console.print("[dim]Run fp -h or fp <command> --help for more.[/dim]")

    # Optional hints for things not yet configured
    extras = []
    connectors_configured = bool(connector_results)
    if not connectors_configured:
        extras.append("fp connect")
    chat_count = counts.get("chats", 0) if counts else 0
    if (chat_result is None or not chat_result) and chat_count == 0:
        extras.append("fp ingest import <file>")
    if extras:
        console.print()
        console.print(f"[dim]Not yet set up: {', '.join(extras)}[/dim]")


if __name__ == "__main__":
    main()
