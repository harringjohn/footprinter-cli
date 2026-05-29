"""fp doctor — installation health checks and repair subcommands.

Bare ``fp doctor`` runs diagnostic checks (like ``brew doctor``).
Naming a specific system — ``fp doctor search`` or ``fp doctor semantic``
— diagnoses and repairs that system.
"""

import importlib.util
import json
import platform
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from footprinter.cli._common import FORMATTER, add_json_flag, console, output_json


@dataclass
class Check:
    """Result of a single diagnostic check."""

    name: str
    status: str  # OK, WARN, FAIL
    message: str = ""


def _get_python_version() -> tuple:
    return sys.version_info[:3]


def _find_spec(name: str):
    try:
        return importlib.util.find_spec(name)
    except (ModuleNotFoundError, ValueError):
        return None


def _probe_fda() -> bool:
    """Return True if Safari History.db is readable (macOS FDA check)."""
    safari_db = Path.home() / "Library" / "Safari" / "History.db"
    if not safari_db.exists():
        return True  # no Safari DB means nothing to check
    try:
        with sqlite3.connect(f"file:{safari_db}?mode=ro", uri=True) as conn:
            conn.execute("SELECT count(*) FROM sqlite_master")
        return True
    except Exception:
        return False


def _check_python_version() -> Check:
    major, minor, micro = _get_python_version()
    if (major, minor) >= (3, 11):
        return Check("python_version", "OK", f"Python {major}.{minor}.{micro}")
    return Check(
        "python_version",
        "FAIL",
        f"Python {major}.{minor}.{micro} — requires 3.11 or later",
    )


def _check_platform() -> Check:
    system = platform.system()
    machine = platform.machine()
    return Check("platform", "OK", f"{system} ({machine})")


def _check_config() -> Check:
    from footprinter.paths import get_config_path

    config_path = get_config_path()
    if not config_path.exists():
        return Check(
            "config",
            "WARN",
            f"No config at {config_path} — run 'fp setup' to create one",
        )
    try:
        import yaml

        with open(config_path) as f:
            yaml.safe_load(f)
        return Check("config", "OK", str(config_path))
    except Exception as e:
        return Check("config", "FAIL", f"Config at {config_path} is not valid YAML: {e}")


def _check_database() -> Check:
    from footprinter.paths import get_db_path

    db_path = get_db_path()
    if not db_path.exists():
        return Check(
            "database",
            "WARN",
            f"No database at {db_path} — run 'fp ingest' to create one",
        )
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.execute("SELECT count(*) FROM sqlite_master")
        return Check("database", "OK", str(db_path))
    except Exception as e:
        return Check("database", "FAIL", f"Database at {db_path} is not readable: {e}")


def _check_fda() -> Check:
    if platform.system() != "Darwin":
        return Check("fda", "OK", "Not macOS — Full Disk Access not applicable")
    if _probe_fda():
        return Check("fda", "OK", "Safari History.db is readable")
    return Check(
        "fda",
        "WARN",
        "Cannot read Safari History.db — grant Full Disk Access to your terminal in"
        " System Settings > Privacy & Security",
    )


def _check_semantic_deps() -> Check:
    missing = []
    for mod in ("chromadb", "onnxruntime"):
        if _find_spec(mod) is None:
            missing.append(mod)
    if not missing:
        return Check("semantic_deps", "OK", "chromadb and onnxruntime available")
    return Check(
        "semantic_deps",
        "WARN",
        f"Optional semantic search dependencies not installed: {', '.join(missing)}"
        " — install with: pipx install --force 'footprinter-cli[full]'",
    )


def _check_parse_deps() -> Check:
    missing = []
    for mod in ("docx", "pypdf", "openpyxl", "pptx"):
        if _find_spec(mod) is None:
            missing.append(mod)
    if not missing:
        return Check("parse_deps", "OK", "Document parsing dependencies available")
    return Check(
        "parse_deps",
        "WARN",
        f"Optional parsing dependencies not installed: {', '.join(missing)}"
        " — install with: pipx install --force 'footprinter-cli[full]'",
    )


def _check_mcp_config() -> Check:
    if platform.system() == "Darwin":
        config_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    else:
        config_dir = Path.home() / ".config" / "Claude"
        config_path = config_dir / "claude_desktop_config.json"

    if not config_path.exists():
        return Check("mcp_config", "OK", "No Claude Desktop config found (optional)")

    try:
        with open(config_path) as f:
            data = json.load(f)
        servers = data.get("mcpServers", {})
        if "footprinter" not in servers:
            return Check("mcp_config", "OK", "Claude Desktop config exists but no footprinter entry (optional)")

        entry = servers["footprinter"]
        command = entry.get("command", "")
        if command:
            resolved = Path(command).exists() if Path(command).is_absolute() else shutil.which(command)
            if not resolved:
                return Check(
                    "mcp_config",
                    "WARN",
                    f"MCP command not found: {command}",
                )
        return Check("mcp_config", "OK", "footprinter MCP server configured in Claude Desktop")
    except Exception as e:
        return Check("mcp_config", "WARN", f"Could not read Claude Desktop config: {e}")


def _check_fts_health() -> Check:
    from footprinter.paths import get_db_path

    db_path = get_db_path()
    if not db_path.exists():
        return Check("fts_health", "OK", "No database — FTS check skipped")
    try:
        from footprinter.ingest.database import Database

        db = Database(str(db_path))
        health = db.check_fts_health()
        db.close()
        errors = [t for t, info in health.items() if info["status"] == "error"]
        if errors:
            return Check(
                "fts_health",
                "WARN",
                f"FTS indexes need repair: {', '.join(errors)}"
                " — run 'fp doctor search'",
            )
        return Check("fts_health", "OK", "FTS indexes healthy")
    except Exception as e:
        return Check("fts_health", "WARN", f"FTS health check failed: {e}")


def run_checks() -> list[Check]:
    """Run all diagnostic checks and return the results."""
    return [
        _check_python_version(),
        _check_platform(),
        _check_config(),
        _check_database(),
        _check_fts_health(),
        _check_fda(),
        _check_semantic_deps(),
        _check_parse_deps(),
        _check_mcp_config(),
    ]


def register(subparsers) -> None:
    """Register ``fp doctor`` on the CLI router."""
    parser = subparsers.add_parser(
        "doctor",
        help="Check installation health",
        description=(
            "Run diagnostic checks on your Footprinter installation.\n"
            "Reports environment, configuration, and dependency status.\n\n"
            "Name a specific system to diagnose and repair it."
        ),
        epilog=(
            "examples:\n"
            "  fp doctor                  Run all health checks\n"
            "  fp doctor --json           Machine-readable output\n"
            "  fp doctor search           Rebuild FTS search indexes\n"
            "  fp doctor semantic         Rebuild vector store (incremental)\n"
            "  fp doctor semantic full    Rebuild vector store (full reset)"
        ),
        formatter_class=FORMATTER,
    )
    add_json_flag(parser)
    parser.set_defaults(func=_handle)

    sub = parser.add_subparsers(dest="doctor_command", metavar="COMMAND")

    # fp doctor search
    search_p = sub.add_parser(
        "search",
        help="Rebuild FTS search indexes",
        description="Drop and rebuild all FTS search indexes from base table data.",
        formatter_class=FORMATTER,
    )
    search_p.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress Rich output",
    )
    search_p.set_defaults(func=_handle_search)

    # fp doctor semantic
    semantic_p = sub.add_parser(
        "semantic",
        help="Rebuild the vector store",
        description="Rebuild the ChromaDB vector store from database contents.",
        formatter_class=FORMATTER,
    )
    semantic_p.add_argument(
        "mode",
        nargs="?",
        const="incremental",
        default="incremental",
        choices=["incremental", "sync", "full"],
        metavar="MODE",
        help=(
            "Rebuild mode: incremental (default, new/modified/removed only), "
            "sync (incremental + verify counts), full (delete and rebuild everything)"
        ),
    )
    semantic_p.add_argument(
        "--vector-source",
        choices=["files", "chats", "all"],
        default="all",
        help="Which vectors to rebuild (default: all)",
    )
    semantic_p.add_argument(
        "--phase",
        choices=["files", "messages", "chat_info"],
        default=None,
        help="Run a single rebuild phase (default: all)",
    )
    semantic_p.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress Rich output",
    )
    semantic_p.set_defaults(func=_handle_semantic)


def _handle_search(args) -> None:
    from footprinter.ingest.vector_ops import _repair_fts

    _repair_fts(quiet=getattr(args, "quiet", False))


def _handle_semantic(args) -> None:
    from footprinter.ingest.vector_ops import _rebuild_vectors

    _rebuild_vectors(
        quiet=getattr(args, "quiet", False),
        source=getattr(args, "vector_source", "all"),
        phase=getattr(args, "phase", None),
        mode=getattr(args, "mode", "incremental"),
    )


def _handle(args) -> None:
    checks = run_checks()

    if getattr(args, "json", False):
        output_json([asdict(c) for c in checks])
    else:
        from rich.markup import escape
        from rich.table import Table

        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("check", style="bold")
        table.add_column("status", width=6)
        table.add_column("message")

        for c in checks:
            if c.status == "OK":
                status_cell = f"[green]{c.status}[/green]"
            elif c.status == "WARN":
                status_cell = f"[yellow]{c.status}[/yellow]"
            else:
                status_cell = f"[red]{c.status}[/red]"
            table.add_row(c.name, status_cell, escape(c.message))

        console.print(table)

    has_fail = any(c.status == "FAIL" for c in checks)
    if has_fail:
        sys.exit(1)
