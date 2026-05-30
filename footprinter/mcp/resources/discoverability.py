"""Discoverability resources: live state for MCP clients orienting themselves.

These resources let an AI client browse what's in the system before issuing
tool calls — system status, the project inventory, and the active access
policies that govern what the MCP surface will reveal. Each handler opens a
read-only connection via ``get_db()`` and delegates to the existing service
or db layer; resources do not introduce new business logic.

Role: MCP runs under ``Role.VIEWER`` (hidden-client filtering applied). The
access-policies resource itself is policy *metadata*, not gated data, so it
goes through ``db.policies`` directly — mirroring the CLI pattern in
``footprinter/cli/permission_cmd.py``.
"""

from footprinter.db import policies as db_policies
from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.services import project_service, status_service
from footprinter.services.roles import Role


@handle_db_errors
def system_status() -> dict:
    """Live status snapshot under VIEWER role: counts and breakdowns by source.

    Same payload as ``footprinter_status`` and ``footprinter://context/summary``.
    Exposed here under the discoverability namespace so clients reading the
    resource list see a system-state entry next to ``projects`` and
    ``access-policies``.
    """
    with get_db() as conn:
        return status_service.get_status(conn, role=Role.VIEWER)


@handle_db_errors
def projects_list() -> dict:
    """Project inventory under VIEWER role with hidden-client filtering applied.

    Returns up to 200 projects per the MCP surface cap (matches the
    ``footprinter_search`` limit cap). Pagination keys are present on the
    payload for clients that need to page further.
    """
    with get_db() as conn:
        return project_service.list_(conn, role=Role.VIEWER, limit=200)


@handle_db_errors
def access_policies() -> dict:
    """Active visibility + permission policies as raw rows.

    Lets a client see the access shape of the system: which scopes are
    visible/opaque/hidden and which are allow/deny. Calls ``db.policies``
    directly — there is no service-layer wrapper for policy reads, and the
    CLI uses the same pattern.
    """
    with get_db() as conn:
        return {
            "visibility": db_policies.list_visibility_policies(conn),
            "permission": db_policies.list_permission_policies(conn),
        }
