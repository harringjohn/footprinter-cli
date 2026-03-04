"""Status tool: system overview via status_service.

Thin MCP adapter — all query logic lives in the service layer.
"""

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.services import status_service
from footprinter.services.roles import Role


@handle_db_errors
def footprinter_status() -> dict:
    """System status: record counts, sync times, and breakdowns for all data sources."""
    with get_db() as conn:
        return status_service.get_status(conn, role=Role.VIEWER)
