"""footprinter.db — public Python API for querying and managing indexed data.

Stable signatures, type hints, plain dict returns.
All functions take sqlite3.Connection as their first parameter.
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
