"""fp search — keyword, semantic, and hybrid search across indexed content.

Thin wrapper that delegates to :func:`footprinter.cli.search.execute_search`
so both ``fp search`` and direct invocations share one code path.
"""

from footprinter.cli._common import FORMATTER, add_json_flag, console


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
        from footprinter.cli.search import execute_search

        execute_search(
            query=" ".join(args.query),
            limit=args.limit,
            type_filter=args.type,
            mode=args.mode,
            output=console,
            json_output=getattr(args, "json", False),
        )

    parser.set_defaults(func=_handle)
