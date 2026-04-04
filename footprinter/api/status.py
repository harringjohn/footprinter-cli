"""Status endpoint for Footprinter HTTP API."""

from fastapi import APIRouter, Depends

from footprinter.api.db import get_conn
from footprinter.services import status_service
from footprinter.services.roles import Role

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status(conn=Depends(get_conn)):
    """Return system status and data counts."""
    return status_service.get_status(conn, role=Role.ADMIN)
