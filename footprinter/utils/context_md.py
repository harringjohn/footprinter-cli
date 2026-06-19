"""Resolve curated-context Markdown for the super-entity orientation tools.

Given a super-entity row (project / client / folder) this module locates an
editable Markdown file holding longer-form curated context and returns the
uniform excerpt-contract block (see ``footprinter.utils.text.build_excerpt``)
plus the resolved ``context_path``. Convention-first, column as override:

- any type with ``context_path`` set → that file wins (explicit override);
- ``folder`` → auto-detect ``README.md`` in the folder's ``path``;
- ``client`` → convention ``<context_root>/context/client-<slug>.md``;
- ``project`` → no convention (no path column) — override only.

The resolver is missing-file tolerant: an unset pointer, an absent file, or an
unreadable file yields ``None`` (no curated-context block) rather than raising,
so orientation tools never fail because a README was deleted.
"""

from pathlib import Path
from typing import Optional

from footprinter.utils.text import build_excerpt


def _candidate_path(
    row: dict, entity_type: str, context_root: Optional[Path]
) -> Optional[Path]:
    """Resolve the candidate Markdown path for a row, before existence checks."""
    override = row.get("context_path")
    if override:
        return Path(override).expanduser()

    if entity_type == "folder":
        folder_path = row.get("path")
        if folder_path:
            return Path(folder_path).expanduser() / "README.md"
        return None

    if entity_type == "client":
        slug = row.get("slug")
        if context_root is not None and slug:
            return Path(context_root).expanduser() / "context" / f"client-{slug}.md"
        return None

    # project: no path to scan → override only (handled above)
    return None


def resolve_curated_context(
    row: dict,
    entity_type: str,
    *,
    context_root: Optional[Path] = None,
) -> Optional[dict]:
    """Return the curated-context block for a super-entity row, or ``None``.

    Parameters
    ----------
    row:
        The entity row dict. Reads ``context_path`` (override, all types),
        ``path`` (folder convention), and ``slug`` (client convention).
    entity_type:
        One of ``"project"``, ``"client"``, ``"folder"``.
    context_root:
        Root under which the client ``context/client-<slug>.md`` convention is
        resolved. ``None`` disables the client convention.

    Returns
    -------
    dict | None
        On a readable Markdown hit: ``{"context_path": <resolved str>,
        **build_excerpt(text, source="context_md")}``. ``None`` when no pointer
        resolves or the file is missing/unreadable.
    """
    candidate = _candidate_path(row, entity_type, context_root)
    if candidate is None:
        return None

    try:
        if not candidate.is_file():
            return None
        text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    return {
        "context_path": str(candidate),
        **build_excerpt(text, source="context_md"),
    }
