"""Footprinter HTTP API — FastAPI app factory and server entry point."""

from __future__ import annotations

import argparse
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from footprinter.utils.exceptions import DatabaseNotInitializedError

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app() -> "FastAPI":
    """Create and configure the FastAPI application.

    Mounts all API routers under ``/api`` and registers error handlers.
    Semantic router is conditionally mounted if chromadb is available.
    """
    from footprinter import __version__
    from footprinter.api.entities import router as entities_router
    from footprinter.api.search import router as search_router
    from footprinter.api.status import router as status_router

    app = FastAPI(
        title="Footprinter API",
        version=__version__,
        description="HTTP API for Footprinter — file archival and AI context system.",
    )

    # Health check (outside /api prefix)
    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Exception handler for uninitialized DB
    @app.exception_handler(DatabaseNotInitializedError)
    async def db_not_initialized_handler(request: Request, exc: DatabaseNotInitializedError):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Database not initialized. Run 'fp ingest' to populate.",
            },
        )

    # Mount routers
    app.include_router(status_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    app.include_router(entities_router, prefix="/api")

    # Conditional semantic router
    try:
        from footprinter.api.semantic import router as semantic_router

        app.include_router(semantic_router, prefix="/api")
    except ImportError:
        pass

    return app


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the Footprinter HTTP API server."""
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=host, port=port)


def cli(argv: list[str] | None = None) -> None:
    """Console_script entry point — parse CLI args, validate host, start server."""
    parser = argparse.ArgumentParser(
        prog="fp-api",
        description="Start the Footprinter HTTP API server.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument(
        "--allow-insecure-bind",
        action="store_true",
        help="Allow binding to non-loopback interfaces (no authentication!).",
    )
    args = parser.parse_args(argv)

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

    main(host=args.host, port=args.port)


if __name__ == "__main__":
    cli()
