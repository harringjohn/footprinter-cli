"""
Command-line search interface — keyword, semantic, and hybrid modes.

Thin CLI wrapper around ``footprinter.services.search_service.mode_search``.
"""

import argparse
import sys

from rich.console import Console

from footprinter.cli._common import FORMATTER, add_json_flag, open_db, output_json
from footprinter.services.search_service import (
    _fts_file_to_result,
    ml_available,
    mode_search,
)

console = Console()


def _resolve_mode(mode: str | None, out: Console, *, quiet: bool = False) -> str:
    """Resolve effective search mode based on request and ML availability."""
    has_ml = ml_available()

    if mode == "semantic" and not has_ml:
        if not quiet:
            out.print("Semantic search requires additional dependencies.")
            out.print("  Install with:  pip install footprinter-cli\\[semantic]")
        sys.exit(1)

    if mode == "hybrid" and not has_ml:
        if not quiet:
            out.print(
                "[dim]Semantic search not available — using keyword search. "
                "Run: pip install footprinter-cli\\[semantic] for AI-powered results.[/dim]"
            )
        return "keyword"

    if mode is not None:
        return mode

    # Auto-detect
    if has_ml:
        return "hybrid"

    if not quiet:
        out.print(
            "[dim]Semantic search not available — using keyword search. "
            "Run: pip install footprinter-cli\\[semantic] for AI-powered results.[/dim]"
        )
    return "keyword"


def execute_search(
    query: str,
    limit: int = 10,
    type_filter: str | None = None,
    mode: str | None = None,
    output: Console | None = None,
    db_path: str | None = None,
    json_output: bool = False,
) -> None:
    """Run search and display results.

    Shared implementation used by the ``fp search`` subcommand.
    """
    out = output or console
    effective_mode = _resolve_mode(mode, out, quiet=json_output)

    # Dispatch by mode via service layer
    try:
        if effective_mode == "semantic":
            merged = mode_search(query, mode=effective_mode, limit=limit, type_filter=type_filter)
        else:
            if db_path is None:
                from footprinter.paths import get_db_path

                db_path = str(get_db_path())
            with open_db(db_path) as conn:
                merged = mode_search(query, mode=effective_mode, limit=limit, type_filter=type_filter, conn=conn)
    except Exception as exc:
        if not json_output:
            out.print(f"[red]Search failed:[/red] {exc}")
        else:
            output_json({"query": query, "mode": effective_mode, "error": str(exc), "results": []})
        sys.exit(1)

    if json_output:
        output_json(
            {
                "query": query,
                "mode": effective_mode,
                "results": merged,
            }
        )
        return

    out.print(f"\nSearching for: '{query}' ({effective_mode} mode)")
    out.print("=" * 80)

    if not merged:
        out.print("No results found.")
        return

    # Display results
    for i, item in enumerate(merged, 1):
        if item["source_type"] == "file":
            r = item["data"]
            file_path = r.get("file_path", r.get("name", ""))
            chunk_info = ""
            if r.get("total_chunks", 1) > 1:
                chunk_info = f" (chunk {r['chunk_index'] + 1}/{r['total_chunks']})"

            out.print(f"\n{i}. [File] {file_path}{chunk_info}")
            out.print("-" * 80)
            out.print(r.get("content_snippet", ""))
            out.print()
        else:
            r = item["data"]
            title = r.get("chat_title", "(untitled)")
            source = r.get("source", "")
            source_label = f" ({source})" if source else ""

            out.print(f"\n{i}. [Chat] {title}{source_label}")
            out.print("-" * 80)
            out.print(r.get("snippet", ""))
            out.print()

    out.print("=" * 80)
    out.print(f"Showing {len(merged)} results")


def register(subparsers) -> None:
    """Register ``fp search`` on the CLI router."""
    parser = subparsers.add_parser(
        "search",
        help="Search across indexed content",
        description=(
            "Search across indexed files and chats using\nkeyword (FTS5), semantic (vector), or hybrid (both) matching."
        ),
        epilog=(
            "examples:\n"
            "  fp search 'database migration'                Search (auto-detect mode)\n"
            "  fp search 'auth flow' --mode keyword           FTS5 keyword search\n"
            "  fp search 'auth flow' --mode semantic          Vector search only\n"
            "  fp search 'auth flow' --mode hybrid            Keyword + semantic fusion\n"
            "  fp search 'auth flow' -n 20                    Return 20 results\n"
            "  fp search 'invoice' --type .pdf                Only PDF files\n"
            "  fp search oauth token refresh                  Multi-word query\n"
            "  fp search 'auth flow' --json                   JSON output"
        ),
        formatter_class=FORMATTER,
    )
    parser.add_argument("query", nargs="*", help="Search query (multiple words joined)")
    parser.add_argument(
        "--mode",
        choices=["keyword", "semantic", "hybrid"],
        default=None,
        help=(
            "Search mode: keyword (FTS5), semantic (vectors), hybrid (both)."
            " Default: hybrid if ML available, keyword otherwise."
        ),
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Max results to return (default: 10)",
    )
    parser.add_argument(
        "--type",
        help="Filter by file type (e.g. .pdf, .md); excludes chat results",
    )
    add_json_flag(parser)

    def _handle(args) -> None:
        if not args.query:
            parser.print_help()
            return
        execute_search(
            query=" ".join(args.query),
            limit=args.limit,
            type_filter=args.type,
            mode=args.mode,
            output=console,
            json_output=getattr(args, "json", False),
        )

    parser.set_defaults(func=_handle)


def main():
    """CLI for search."""
    parser = argparse.ArgumentParser(
        prog="fp search",
        description="Search across your files and chats",
    )
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument(
        "--mode",
        choices=["keyword", "semantic", "hybrid"],
        default=None,
        help="Search mode: keyword (FTS5), semantic (vectors), hybrid (both).",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Max results to return (default: 10)",
    )
    parser.add_argument("--type", help="Filter by file type (e.g., .pdf, .md). Excludes chat results.")
    add_json_flag(parser)

    args = parser.parse_args()
    execute_search(
        query=" ".join(args.query),
        limit=args.limit,
        type_filter=args.type,
        mode=args.mode,
        json_output=getattr(args, "json", False),
    )


if __name__ == "__main__":
    main()
