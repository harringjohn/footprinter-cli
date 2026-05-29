"""Footprinter CLI router."""

import argparse
import sys


def _check_python_version() -> None:
    """Exit with a clear message if Python is too old."""
    if sys.version_info < (3, 11):
        print(
            f"Error: Footprinter requires Python 3.11 or later (found {sys.version_info[0]}.{sys.version_info[1]}).",
            file=sys.stderr,
        )
        sys.exit(1)


def _is_first_run() -> bool:
    """Return True if neither config file nor database exists."""
    from footprinter.paths import get_config_path, get_db_path

    return not get_config_path().exists() and not get_db_path().exists()


def main(argv=None) -> None:
    """Entry point for the ``fp`` command."""
    _check_python_version()

    import sys as _sys

    if argv is None:
        argv = _sys.argv[1:]
    from footprinter import __version__
    from footprinter.cli._common import FORMATTER
    from footprinter.source_registry import ConfigError as _ConfigError

    parser = argparse.ArgumentParser(
        prog="fp",
        description=f"Footprinter v{__version__} — file archival and AI context CLI",
        epilog=(
            "getting started:\n"
            "  fp setup                   Run the configuration wizard\n"
            "  fp connect list            Show available data source connectors\n"
            "\n"
            "data commands:\n"
            "  fp ingest                  Run the data ingest pipeline (incremental)\n"
            "  fp ingest --full           Re-process all data sources\n"
            "  fp status                  Show data counts and system health\n"
            "  fp search 'my query'       Semantic search across indexed content\n"
            "\n"
            "browse indexed data:\n"
            "  fp view files               List indexed files\n"
            "  fp view folders             List indexed folders\n"
            "  fp view projects            List projects\n"
            "  fp view clients             List clients\n"
            "  fp view chats               List chats\n"
            "  fp view emails              List indexed emails\n"
            "  fp view visits              List browser history\n"
            "\n"
            "access control:\n"
            "  fp permission list         Show all configured access policies\n"
            "  fp permission set          Set visibility and/or access for a scope\n"
            "  fp permission check        Check access resolution for a target\n"
            "  fp permission reset        Remove policy (fall back to inheritance)\n"
            "  fp permission recalculate  Re-resolve access stamps from the policy chain\n"
            "\n"
            "servers:\n"
            "  fp mcp                     Start the MCP server\n"
            "  fp api                     Start the HTTP API server\n"
            "\n"
            "diagnostics:\n"
            "  fp doctor                  Check installation health\n"
            "  fp doctor search           Rebuild FTS search indexes\n"
            "  fp doctor semantic         Rebuild vector store\n"
            "\n"
            "cleanup:\n"
            "  fp uninstall               Remove Footprinter (MCP entry, data, package)\n"
            "\n"
            "tip: use 'fp <command> --help' for details on any command."
        ),
        formatter_class=FORMATTER,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="COMMAND")
    subparsers.required = False

    from footprinter.cli import (
        api_cmd,
        connect,
        data,
        delete,
        doctor,
        ingest,
        mcp_cmd,
        permission_cmd,
        search,
        setup,
        status,
        uninstall,
        upsert,
        vectorize,
        view,
    )

    for mod in [
        ingest,
        mcp_cmd,
        permission_cmd,
        api_cmd,
        status,
        search,
        setup,
        connect,
        view,
        upsert,
        data,
        delete,
        vectorize,
        uninstall,
        doctor,
    ]:
        mod.register(subparsers)

    args = parser.parse_args(argv)
    if args.subcommand is None:
        if _is_first_run():
            print(
                "\033[33;1m\U0001f4a1 Looks like this is your first time running "
                "Footprinter.\n   Run 'fp setup' to get started.\033[0m\n",
                file=_sys.stderr,
            )
        parser.print_help()
        return
    from footprinter.cli._prompt import PromptCancelled

    try:
        args.func(args)
    except _ConfigError as e:
        print(str(e), file=_sys.stderr)
        _sys.exit(1)
    except (PromptCancelled, KeyboardInterrupt):
        print("\nCancelled.", file=_sys.stderr)
        _sys.exit(130)
