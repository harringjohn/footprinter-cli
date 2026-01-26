"""Role enum for the service layer."""

from enum import Enum


class Role(Enum):
    """Caller role — determines write access and metadata visibility.

    Interface layers assign the role:
    - CLI passes Role.ADMIN (full access, local user)
    - MCP passes Role.VIEWER (read-only, filtered metadata)
    """

    ADMIN = "admin"
    VIEWER = "viewer"

    @property
    def can_write(self) -> bool:
        """Whether this role permits write operations."""
        return self in (Role.ADMIN,)

    @property
    def sees_all(self) -> bool:
        """Whether this role can see all metadata (including sensitive paths)."""
        return self == Role.ADMIN
