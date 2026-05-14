"""Path display utilities."""

import os


def abbreviate_home(path: str) -> str:
    """Replace ``$HOME`` prefix with ``~`` for display."""
    if not path:
        return path or ""
    home = os.path.expanduser("~")
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    elif path == home:
        return "~"
    return path
