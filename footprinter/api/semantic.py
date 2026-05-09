"""Semantic search endpoint for Footprinter HTTP API."""

from fastapi import APIRouter, Depends, HTTPException, Query

from footprinter.api import MAX_LIMIT
from footprinter.api.db import get_conn
from footprinter.services import semantic_service
from footprinter.services.roles import Role

router = APIRouter(tags=["semantic"])

_VALID_SOURCES = {"chats", "files", "all"}


@router.get("/semantic")
def semantic_search(
    conn=Depends(get_conn),
    query: str = Query(..., min_length=3, description="Search query (minimum 3 characters)"),
    source: str = Query("all", description="Source to search: chats, files, or all"),
    limit: int = Query(10, ge=1, le=MAX_LIMIT),
):
    """Semantic (vector) search across indexed content."""
    if source not in _VALID_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source '{source}'. Must be one of: {', '.join(sorted(_VALID_SOURCES))}",
        )
    return semantic_service.semantic_search(
        conn,
        query,
        role=Role.ADMIN,
        source=source,
        limit=limit,
    )
