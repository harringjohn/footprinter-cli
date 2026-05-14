"""footprinter.db — public Python API for querying and managing indexed data.

Stable signatures, type hints, plain dict returns.
All functions take sqlite3.Connection as their first parameter.

Commit convention:
- Insert functions (insert_file, insert_email, etc.) never call
  conn.commit() — the caller commits after batch operations.
- CRUD operations that actually modify rows (update_*_relationships,
  update_file_status, etc.) commit before returning. No-op calls
  (e.g. no fields passed) return True without committing.
"""

from footprinter.db import (
    browser,
    chats,
    clients,
    emails,
    files,
    folders,
    messages,
    policies,
    projects,
    search,
    sql_utils,
    status,
    uploads,
)

__all__ = [
    "browser",
    "chats",
    "clients",
    "emails",
    "files",
    "folders",
    "messages",
    "policies",
    "projects",
    "search",
    "sql_utils",
    "status",
    "uploads",
]
