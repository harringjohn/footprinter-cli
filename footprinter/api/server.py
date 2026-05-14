"""Footprinter HTTP API — FastAPI app factory and server entry point."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from footprinter.utils.exceptions import DatabaseNotInitializedError


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


if __name__ == "__main__":
    main()
