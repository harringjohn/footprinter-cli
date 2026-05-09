"""fp uninstall — reverse what `fp setup` and `pip install` created.

Three user-action phases, each individually skippable via prompt:

1. Remove the ``footprinter`` entry from Claude Desktop's MCP config
   (followed inline by a restart reminder, only when an entry was removed)
2. Remove ``~/.footprinter`` (config, database, vector store, logs)
3. Uninstall the ``footprinter-cli`` pip package (pipx → pip → printed command)

Each phase reports what it did or that it skipped, so re-running is safe.
"""

import json
import shutil
import subprocess

from rich.panel import Panel
from rich.rule import Rule

from footprinter.cli._common import FORMATTER, console
from footprinter.cli._prompt import SafeConfirm
from footprinter.cli.mcp_setup import (
    detect_config_path,
    has_footprinter_entry,
    read_mcp_config,
    unregister_mcp_server,
)
from footprinter.paths import get_home

PACKAGE_NAME = "footprinter-cli"


def register(subparsers) -> None:
    """Register ``fp uninstall`` on the CLI router."""
    parser = subparsers.add_parser(
        "uninstall",
        help="Remove Footprinter (MCP entry, user data, package)",
        description=(
            "Reverse what fp setup and pip install footprinter-cli created.\n"
            "Each step is individually confirmed; declining one does not skip the rest."
        ),
        epilog=(
            "what gets removed:\n"
            "  1. footprinter entry in Claude Desktop's MCP config\n"
            "  2. ~/.footprinter (config, database, vector store, logs)\n"
            "  3. the footprinter-cli pip package"
        ),
        formatter_class=FORMATTER,
    )
    parser.set_defaults(func=_handle_uninstall)


def _handle_uninstall(args) -> None:
    """Run all three user-action phases in order; phases handle their own errors."""
    console.print()
    console.print(Panel.fit("[bold]fp uninstall[/bold]", border_style="dim"))
    console.print(
        "Each step prompts before changing anything. You can decline any step.",
        style="dim",
    )

    if _phase_mcp():
        _show_restart_reminder()
    _phase_data_dir()
    _phase_package()

    console.print()
    console.print("[green]✓[/green] Uninstall complete.")


def _phase_mcp() -> bool:
    """Remove the footprinter entry from Claude Desktop's MCP config.

    Returns True only when an entry was actually removed, so the caller
    can use the return value to decide whether to show the restart reminder.
    Reads the config to check for the entry before prompting, so users
    aren't asked to confirm a no-op.
    """
    console.print()
    console.print(Rule("[bold]Step 1 of 3 — Claude Desktop MCP config[/bold]", style="dim"))

    path = detect_config_path()
    if path is None:
        console.print("[yellow]Unsupported platform — skipping MCP config cleanup.[/yellow]")
        return False

    try:
        config = read_mcp_config(path)
    except (json.JSONDecodeError, OSError) as e:
        console.print(f"  [red]Cannot read existing config:[/red] {e}")
        return False

    if config is None:
        console.print(f"  [dim]No MCP config at {path} — nothing to remove.[/dim]")
        return False

    if not has_footprinter_entry(config):
        console.print(f"  [dim]No footprinter entry in {path} — nothing to remove.[/dim]")
        return False

    if not SafeConfirm.ask(
        f"Remove [bold]footprinter[/bold] from {path}?",
        default=True,
    ):
        console.print("  [yellow]skipped[/yellow]")
        return False

    return unregister_mcp_server(config_path=path)


def _show_restart_reminder() -> None:
    """Inline restart note shown only after a real MCP removal (no step number)."""
    console.print()
    console.print(
        Panel(
            "Restart [bold]Claude Desktop[/bold] now so it drops the Footprinter MCP connection.",
            title="Restart Claude Desktop",
            border_style="yellow",
        )
    )


def _phase_data_dir() -> None:
    """Delete ``~/.footprinter`` (config, database, vectors, logs)."""
    console.print()
    console.print(Rule("[bold]Step 2 of 3 — User data[/bold]", style="dim"))

    # ``get_home()`` creates the directory if missing, so ``home.exists()``
    # is always True here — only check for empty.
    home = get_home()
    if not any(home.iterdir()):
        console.print(f"  [dim]{home} is empty — nothing to remove.[/dim]")
        return

    if not SafeConfirm.ask(
        f"Delete [bold]{home}[/bold] (config, database, vectors, logs)?",
        default=False,
    ):
        console.print("  [yellow]skipped[/yellow] — your data is intact.")
        return

    shutil.rmtree(home)
    console.print(f"  [green]✓[/green] Removed {home}")


def _phase_package() -> None:
    """Uninstall ``footprinter-cli`` via pipx or pip; fall through to printed command."""
    console.print()
    console.print(Rule("[bold]Step 3 of 3 — Package[/bold]", style="dim"))

    cmd = _detect_uninstall_command()
    if cmd is None:
        console.print(
            "  [dim]No pip or pipx found on PATH — run this command yourself:[/dim]"
        )
        _print_manual_command()
        return

    if not SafeConfirm.ask(
        f"Run [bold]{' '.join(cmd)}[/bold]?",
        default=True,
    ):
        console.print("  [yellow]skipped[/yellow] — run this command yourself when ready:")
        _print_manual_command()
        return

    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        console.print(f"  [red]Uninstall command failed:[/red] {exc}")
        console.print("  Run this command yourself:")
        _print_manual_command()
        return

    console.print(f"  [green]✓[/green] Uninstalled {PACKAGE_NAME}")
    console.print(
        "  [dim]Note: pip-installed dependencies (~90 packages, ~250MB) were not removed.[/dim]"
    )
    console.print(
        "  [dim]To review them: [cyan]pip list --not-required[/cyan][/dim]"
    )


def _detect_uninstall_command() -> list[str] | None:
    """Return the argv for uninstalling the package, or None if no installer is found.

    Prefers pipx (handles PEP 668 externally-managed Python). Falls back to
    pip / pip3.
    """
    pipx = shutil.which("pipx")
    if pipx:
        return [pipx, "uninstall", PACKAGE_NAME]

    for name in ("pip", "pip3"):
        path = shutil.which(name)
        if path:
            return [path, "uninstall", "-y", PACKAGE_NAME]
    return None


def _print_manual_command() -> None:
    """Print both pipx and pip commands so the user can pick the right one."""
    console.print(f"    [cyan]pipx uninstall {PACKAGE_NAME}[/cyan]   (PEP 668 systems)")
    console.print(f"    [cyan]pip uninstall {PACKAGE_NAME}[/cyan]    (otherwise)")
