"""
MCP Configuration Helper for AI clients.

Detects config paths for MCP clients, generates the correct
MCP server snippet for this Footprinter installation, and optionally writes it.

Usage:
    fp setup mcp             # Print MCP snippet to paste
    fp setup mcp --claude    # Write/merge snippet into Claude Desktop config (with backup)
"""

import json
import os
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# Known MCP-compatible clients and their config locations.
MCP_CLIENT_CONFIGS = [
    {"name": "Claude Desktop", "path": "~/Library/Application Support/Claude/claude_desktop_config.json"},
    {"name": "Claude Code", "command": "claude mcp add footprinter -- fp-mcp"},
    {"name": "Cursor", "path": "~/.cursor/mcp.json"},
    {"name": "VS Code", "path": ".vscode/mcp.json (per-project)"},
    {"name": "Gemini CLI", "path": "~/.gemini/settings.json"},
]


def is_mcp_available() -> bool:
    """Check if the mcp package is installed.

    Returns:
        True if ``import mcp`` succeeds, False otherwise.
    """
    try:
        __import__("mcp")
        return True
    except ImportError:
        return False


def _repo_root() -> Path:
    """Repo checkout root (dev-only: MCP cwd, run_mcp.sh discovery)."""
    return Path(__file__).resolve().parent.parent.parent


def _is_dev_checkout(root: Optional[Path] = None) -> bool:
    """True when running from a source checkout (not a pip install)."""
    return ((root or _repo_root()) / "pyproject.toml").exists()


def detect_config_path() -> Optional[Path]:
    """Detect Claude Desktop config path for the current platform.

    Returns:
        Path to claude_desktop_config.json, or None if unsupported platform.
    """
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            appdata = str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return None


def get_mcp_command(project_root: Path = None) -> tuple[str, list[str]]:
    """Get the command and args to launch the MCP server.

    Priority: fp-mcp binary → run_mcp.sh → sys.executable -m footprinter.mcp.

    Args:
        project_root: Override project root (default: auto-detected).

    Returns:
        Tuple of (command, args_list).
    """
    root = project_root or _repo_root()

    # 1. Prefer fp-mcp entry point (most portable for pip installs)
    fp_mcp_cmd = shutil.which("fp-mcp")
    if fp_mcp_cmd:
        return fp_mcp_cmd, []

    # 2. Fall back to run_mcp.sh (dev environments — script launches MCP directly, no args)
    run_script = root / "run_mcp.sh"
    if run_script.exists():
        return str(run_script), []

    # 3. Fall back to current Python + module
    return sys.executable, ["-m", "footprinter.mcp"]


def generate_snippet(project_root: Path = None) -> dict:
    """Generate the MCP server config snippet as a dict.

    Args:
        project_root: Override project root (default: auto-detected).

    Returns:
        Dict suitable for merging into claude_desktop_config.json.
    """
    root = project_root or _repo_root()
    command, args = get_mcp_command(root)

    # Warn if the command doesn't exist on disk or PATH
    if not Path(command).is_file() and not shutil.which(command):
        console.print(f"[yellow]Warning: command not found: {command}[/yellow]")

    server_config = {"command": command}
    if args:
        server_config["args"] = args
    # Only set cwd when it's meaningful (explicit root or dev checkout)
    if project_root is not None or _is_dev_checkout(root):
        server_config["cwd"] = str(root)

    return {"mcpServers": {"footprinter": server_config}}


def write_config(snippet: dict, config_path: Path = None) -> bool:
    """Write or merge the MCP snippet into Claude Desktop config.

    Creates a backup before modifying an existing file.

    Args:
        snippet: The snippet dict from generate_snippet().
        config_path: Override config path (default: auto-detected).

    Returns:
        True if write succeeded.
    """
    path = config_path or detect_config_path()

    if path is None:
        console.print("[red]Unsupported platform — cannot detect config path.[/red]")
        return False

    # Load existing config or start empty
    existing = {}
    if path.exists():
        try:
            with open(path, "r") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[red]Cannot read existing config:[/red] {e}")
            return False

    # Merge: add/update mcpServers.footprinter
    if "mcpServers" not in existing:
        existing["mcpServers"] = {}
    existing["mcpServers"]["footprinter"] = snippet["mcpServers"]["footprinter"]

    # Backup existing file
    if path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = path.with_suffix(f".backup_{timestamp}.json")
        shutil.copy2(path, backup)
        console.print(f"  Backed up to [dim]{backup}[/dim]")

    # Write
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")

    console.print(f"  Wrote [bold]{path}[/bold]")
    return True


def read_mcp_config(path: Path) -> Optional[dict]:
    """Read and parse the MCP config at ``path``.

    Returns the parsed dict, or None if the file does not exist.
    Raises ``json.JSONDecodeError`` or ``OSError`` if the file exists
    but cannot be read or parsed — callers decide how to surface that.
    """
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)


def has_footprinter_entry(config: dict) -> bool:
    """True iff the parsed MCP config has a ``footprinter`` entry under mcpServers.

    Coerces hand-edited ``"mcpServers": null`` to ``{}``.
    """
    return "footprinter" in (config.get("mcpServers") or {})


def unregister_mcp_server(config_path: Path = None, dry_run: bool = False) -> bool:
    """Remove the footprinter entry from the Claude Desktop MCP config.

    Backs up the existing file before mutating.
    Idempotent: missing file or missing entry both return True without error.

    Args:
        config_path: Override config path (default: auto-detected).
        dry_run: If True, report intent without writing.

    Returns:
        True on success (including no-op cases — missing file, missing
        ``mcpServers`` key, or no footprinter entry). False when the
        platform is unsupported (no config path) or the config file
        exists but cannot be parsed as JSON.
    """
    path = config_path or detect_config_path()

    if path is None:
        console.print("[yellow]Unsupported platform — cannot detect MCP config path.[/yellow]")
        return False

    try:
        existing = read_mcp_config(path)
    except (json.JSONDecodeError, OSError) as e:
        console.print(f"[red]Cannot read existing config:[/red] {e}")
        return False

    if existing is None:
        console.print(f"  [dim]No MCP config at {path} — nothing to remove.[/dim]")
        return True

    if not has_footprinter_entry(existing):
        console.print(f"  [dim]No footprinter entry in {path}.[/dim]")
        return True

    servers = existing.get("mcpServers") or {}

    if dry_run:
        console.print(f"[dim]Would remove footprinter from:[/dim] {path}")
        return True

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_suffix(f".backup_{timestamp}.json")
    shutil.copy2(path, backup)
    console.print(f"  Backed up to [dim]{backup}[/dim]")

    del servers["footprinter"]
    existing["mcpServers"] = servers

    with open(path, "w") as f:
        json.dump(existing, f, indent=2)
        f.write("\n")

    console.print(f"  Removed footprinter from [bold]{path}[/bold]")
    return True


def print_client_paths():
    """Render a table of known MCP clients and their config locations."""
    table = Table(title="MCP Client Config Paths", show_header=True)
    table.add_column("Client", style="bold")
    table.add_column("Config Location / Command")

    for client in MCP_CLIENT_CONFIGS:
        if "command" in client:
            table.add_row(client["name"], f"[cyan]{client['command']}[/cyan]")
        else:
            table.add_row(client["name"], f"[dim]{client['path']}[/dim]")

    console.print()
    console.print(table)


def print_snippet(snippet: dict):
    """Display the MCP snippet for manual pasting.

    Args:
        snippet: The snippet dict from generate_snippet().
    """
    json_str = json.dumps(snippet, indent=2)
    console.print()
    console.print("Add this to your MCP client config:")
    console.print(Panel(json_str, title="MCP Config"))
    print_client_paths()
    console.print()
    console.print("[dim]Or run [bold]fp setup mcp --claude[/bold] to write it to Claude Desktop automatically.[/dim]")
