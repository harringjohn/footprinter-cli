"""fp uninstall — reverse what `fp setup` and `pip install` created.

Four phases, each individually skippable via prompt:

1. Remove the ``footprinter`` entry from Claude Desktop's MCP config
2. Remind the user to restart Claude Desktop (only if step 1 changed anything)
3. Remove ``~/.footprinter`` (config, database, vector store, logs)
4. Uninstall the ``footprinter-cli`` pip package (pipx → pip → printed command)

Each phase reports what it did or that it skipped, so re-running is safe.
"""

import shutil
import subprocess

from rich.panel import Panel
from rich.rule import Rule

from footprinter.cli._common import FORMATTER, console
from footprinter.cli._prompt import SafeConfirm
from footprinter.cli.mcp_setup import detect_config_path, unregister_mcp_server
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
    """Run all four phases in order. Returns no value; phases handle their own errors."""
    console.print()
    console.print(Panel.fit("[bold]fp uninstall[/bold]", border_style="dim"))
    console.print(
        "Each step prompts before changing anything. You can decline any step.",
        style="dim",
    )

    mcp_changed = _phase_mcp()
    if mcp_changed:
        _phase_restart_reminder()
    _phase_data_dir()
    _phase_package()

    console.print()
    console.print("[green]✓[/green] Uninstall complete.")


def _phase_mcp() -> bool:
    """Remove the footprinter entry from Claude Desktop's MCP config.

    Returns True if the config was actually modified, False if skipped or no-op.
    """
    console.print()
    console.print(Rule("[bold]Step 1 of 4 — Claude Desktop MCP config[/bold]", style="dim"))

    path = detect_config_path()
    if path is None:
        console.print("[yellow]Unsupported platform — skipping MCP config cleanup.[/yellow]")
        return False
    if not path.exists():
        console.print(f"  [dim]No MCP config at {path} — nothing to remove.[/dim]")
        return False

    if not SafeConfirm.ask(
        f"Remove [bold]footprinter[/bold] from {path}?",
        default=True,
    ):
        console.print("  [yellow]skipped[/yellow]")
        return False

    return unregister_mcp_server(config_path=path)


def _phase_restart_reminder() -> None:
    """Tell the user to restart Claude Desktop so it drops the MCP connection."""
    console.print()
    console.print(
        Panel(
            "Restart [bold]Claude Desktop[/bold] now so it drops the Footprinter MCP connection.",
            title="Step 2 of 4 — Restart Claude Desktop",
            border_style="yellow",
        )
    )


def _phase_data_dir() -> None:
    """Delete ``~/.footprinter`` (config, database, vectors, logs)."""
    console.print()
    console.print(Rule("[bold]Step 3 of 4 — User data[/bold]", style="dim"))

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
    console.print(Rule("[bold]Step 4 of 4 — Package[/bold]", style="dim"))

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
