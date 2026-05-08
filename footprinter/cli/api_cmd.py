"""fp api — start the HTTP API server."""

import sys

from footprinter.cli._common import FORMATTER

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _start_api(args) -> None:
    if args.host not in _LOOPBACK_HOSTS:
        if not args.allow_insecure_bind:
            print(
                f"error: refusing to bind to non-loopback host {args.host!r}.\n"
                "The Footprinter HTTP API has no authentication; binding outside "
                "loopback exposes indexed data to anyone on the network.\n"
                "Pass --allow-insecure-bind to override.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(
            f"WARNING: binding to {args.host} — the HTTP API has no authentication. "
            "Anyone reachable on this network can read indexed files, emails, "
            "chats, and browser history.",
            file=sys.stderr,
        )

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
            "  fp api                                       Start on localhost:8000\n"
            "  fp api --port 9000                           Start on custom port\n"
            "  fp api --host 0.0.0.0 --allow-insecure-bind  Listen on all interfaces (no auth!)"
        ),
        formatter_class=FORMATTER,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument(
        "--allow-insecure-bind",
        action="store_true",
        help=(
            "Allow binding to non-loopback interfaces. The HTTP API has no "
            "authentication; anyone on the network can read indexed data."
        ),
    )
    parser.set_defaults(func=_start_api)
