"""fp api — start the HTTP API server."""

from footprinter.cli._common import FORMATTER


def _start_api(args) -> None:
    from footprinter.api.server import main

    main(host=args.host, port=args.port)


def register(subparsers) -> None:
    """Register the ``api`` subcommand."""
    parser = subparsers.add_parser(
        "api",
        help="Start the HTTP API server",
        description=(
            "Start the Footprinter HTTP API server.\n\n"
            "Provides REST endpoints for programmatic access to indexed data.\n"
            "Auto-generated docs available at /docs (Swagger UI)."
        ),
        epilog=(
            "examples:\n"
            "  fp api                     Start on localhost:8000\n"
            "  fp api --port 9000         Start on custom port\n"
            "  fp api --host 0.0.0.0      Listen on all interfaces"
        ),
        formatter_class=FORMATTER,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.set_defaults(func=_start_api)
