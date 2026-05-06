"""Search endpoint for Footprinter HTTP API."""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from footprinter.api import MAX_LIMIT
from footprinter.api.db import get_conn
from footprinter.services import search_service
from footprinter.services.roles import Role

router = APIRouter(tags=["search"])


@router.get("/search")
def search(
    conn=Depends(get_conn),
    query: str = "",
    sources: Optional[str] = Query(None, description="Comma-separated source filter"),
    project: Optional[str] = None,
    client: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    account: Optional[str] = None,
    sender: Optional[str] = None,
    days_back: Optional[int] = None,
    folder: Optional[str] = None,
    mime_type: Optional[str] = None,
):
    """Keyword search across indexed content."""
    source_list = [s.strip() for s in sources.split(",")] if sources else None
    return search_service.search(
        conn,
        role=Role.ADMIN,
        query=query,
        sources=source_list,
        project=project,
        client=client,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        account=account,
        sender=sender,
        days_back=days_back,
        folder=folder,
        mime_type=mime_type,
    )
