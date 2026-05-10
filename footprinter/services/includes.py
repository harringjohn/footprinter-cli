"""Include parameter validation and helpers for the service layer."""

from footprinter.services.roles import Role


def validate_include(
    include: list[str] | None,
    valid: frozenset[str],
) -> frozenset[str]:
    """Validate and normalize an include parameter.

    Returns a frozenset of validated include names.
    Raises ValueError for invalid values.
    """
    if include is None:
        return frozenset()
    result = frozenset(include)
    invalid = result - valid
    if invalid:
        raise ValueError(f"Invalid include values: {', '.join(sorted(invalid))}. Valid: {', '.join(sorted(valid))}")
    return result


def status_arg_for_role(
    role: Role,
    *,
    include_unlisted: bool,
    include_removed: bool,
) -> "str | list[str] | None":
    """Translate ADMIN-only ``include_unlisted``/``include_removed`` flags to a status arg.

    Returns the value to pass to db-layer ``status=`` kwargs.

    - VIEWER (or any role without sees_all): always ``None`` (default = listed only).
    - ADMIN, neither flag: ``None`` (default = listed only).
    - ADMIN, ``include_unlisted=True``: ``["listed", "unlisted"]``.
    - ADMIN, ``include_removed=True``: ``["listed", "removed"]``.
    - ADMIN, both: ``"all"`` (no status filter).
    """
    if not role.sees_all:
        return None
    if include_unlisted and include_removed:
        return "all"
    if include_unlisted:
        return ["listed", "unlisted"]
    if include_removed:
        return ["listed", "removed"]
    return None
