"""footprinter.services — shared service layer between interfaces and repository.

Service function signature pattern:

    def get_thing(conn: sqlite3.Connection, *, role: Role = Role.ADMIN, ...) -> dict:
        ...

Every service function:
- Takes ``conn: sqlite3.Connection`` as first positional arg
- Takes ``role: Role`` as a keyword arg (default ``Role.ADMIN``)
- Returns plain ``dict`` (matching the repository layer convention in footprinter.db)
- Uses keyword-only args for filters and options

Interface layers assign the role:
- CLI passes ``Role.ADMIN`` (full access, local user)
- MCP passes ``Role.VIEWER`` (read-only, filtered metadata)

Error return conventions:
- Read operations (get, list_) return None when the entity is not found.
- Write operations (assign, update) raise on invalid input
  (ValueError, PermissionError).
- Specialty services (search, semantic, status) return status-dicts
  with structured error information.
"""

from footprinter.services import (
    access_service,
    chat_service,
    client_service,
    content_service,
    email_service,
    file_service,
    folder_service,
    project_service,
    search_service,
    semantic_service,
    status_service,
    visit_service,
)
from footprinter.services.roles import Role

__all__ = [
    "Role",
    "access_service",
    "client_service",
    "project_service",
    "file_service",
    "folder_service",
    "chat_service",
    "content_service",
    "email_service",
    "visit_service",
    "status_service",
    "search_service",
    "semantic_service",
]
