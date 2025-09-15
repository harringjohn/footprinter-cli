import re


def _make_slug(name: str) -> str:
    """Convert a display name to a URL-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
