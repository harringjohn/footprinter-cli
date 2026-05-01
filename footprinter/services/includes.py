"""Include parameter validation for the service layer."""


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
