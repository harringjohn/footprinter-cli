"""Resolve curated-context Markdown for the super-entity orientation tools.

Given a super-entity row (project / client / folder) this module locates an
editable Markdown file holding longer-form curated context and returns the
uniform excerpt-contract block (see ``footprinter.utils.text.build_excerpt``)
plus the resolved ``context_path``. Convention-first, column as override:

- any type with ``context_path`` set → that file wins (explicit override);
- ``folder`` → auto-detect ``README.md`` in the folder's ``path``;
- ``client`` → convention ``<context_root>/context/client-<slug>.md``. In
  production the caller (``access_service.attach_curated_context``) supplies
  ``context_root = footprinter.paths.get_home()``, so the convention resolves
  under ``$FOOTPRINTER_HOME`` (default ``~/.footprinter/``) →
  ``~/.footprinter/context/client-<slug>.md``;
- ``project`` → no convention (no path column) — override only.

The resolver is missing-file tolerant: an unset pointer, an absent file, or an
unreadable file yields ``None`` (no curated-context block) rather than raising,
so orientation tools never fail because a README was deleted.

Hardening (defense-in-depth on top of the ADMIN-written ``context_path`` and the
folder auto-detect being confined to the indexed folder's own README):

- **Size cap.** The candidate is read with a bounded ``read(_MAX_CONTEXT_BYTES)``
  (1 MB) rather than an unbounded ``read_text()``, so a very large or multi-GB
  file is never fully loaded into memory on an orientation-tool call. The decoded
  text — and therefore ``chars_available`` — reflects the bounded read.
- **Home confinement.** The candidate is resolved (collapsing symlinks) and must
  lie under a confinement root; a path or symlink whose target escapes every root
  resolves to ``None`` (treated like a missing file). The roots are ``Path.home()``
  (the defense-in-depth default — every indexed entity lives under it, and the
  project override has no natural entity root) **and** the caller-supplied
  ``context_root`` when given. Including ``context_root`` keeps the client
  convention reachable when the home root the caller wires in
  (``$FOOTPRINTER_HOME``) is relocated outside ``Path.home()`` — otherwise the
  convention path would silently fail confinement and never fire.
- **Error logging.** Permission/decode failures (``OSError``) are logged at debug
  with the path before returning ``None``, so they are diagnosable rather than
  silently dropped. A genuinely missing file stays silent (not an error).

**VIEWER exposure policy.** This resolver always returns the full block (excerpt
included). The role-aware gating — VIEWER sees pointer + provenance only
(``context_path`` / ``excerpt_source`` / ``chars_available``), ADMIN sees the
excerpt body — is applied downstream in
``footprinter.services.access_service.attach_curated_context``, which knows the
caller's role.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from footprinter.utils.text import build_excerpt

logger = logging.getLogger(__name__)

# Upper bound on bytes read from a curated-context file. Curated notes are small
# Markdown (the excerpt budget is 500 chars, two orders of magnitude under this
# cap), so 1 MB bounds the read without ever truncating a realistic note. Mirrors
# the bounded-read idiom in ``content_service`` (``open(p, "rb").read(max_bytes)``)
# while staying well under that path's 10 MB general read cap.
_MAX_CONTEXT_BYTES = 1 * 1024 * 1024


def _confine_to_roots(
    candidate: Path, extra_root: Optional[Path] = None
) -> Optional[Path]:
    """Return ``candidate`` resolved, only if it lies under a confinement root.

    The roots are ``Path.home()`` (the defense-in-depth default) and, when given,
    ``extra_root`` — the caller-supplied ``context_root``. Including ``extra_root``
    keeps the client convention reachable when that root (``$FOOTPRINTER_HOME``) is
    relocated outside ``Path.home()``; without it the convention path would
    silently fail confinement and the documented convention would never fire.

    Mirrors ``content_service._validate_local_path`` (``Path.resolve()`` +
    ``startswith(str(root) + os.sep)``), but returns ``None`` rather than raising
    — the curated resolver is missing-file tolerant, so a confinement failure is
    treated like an absent file. ``.resolve()`` collapses symlinks, so a symlink
    whose target escapes every root is rejected.
    """
    resolved = candidate.resolve()
    roots = [Path.home().resolve()]
    if extra_root is not None:
        roots.append(Path(extra_root).expanduser().resolve())
    for root in roots:
        if str(resolved).startswith(str(root) + os.sep) or resolved == root:
            return resolved
    return None


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
        resolved. The production caller
        (``access_service.attach_curated_context``) passes
        ``footprinter.paths.get_home()`` — i.e. ``$FOOTPRINTER_HOME`` (default
        ``~/.footprinter/``) — so the convention resolves to
        ``~/.footprinter/context/client-<slug>.md``. It also serves as an extra
        confinement root alongside ``Path.home()``, so the convention stays
        reachable when ``$FOOTPRINTER_HOME`` is relocated outside ``Path.home()``.
        ``None`` disables the client convention (only tests omit it).

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

    confined = _confine_to_roots(candidate, extra_root=context_root)
    if confined is None:
        logger.debug("curated context outside confinement root: %s", candidate)
        return None

    try:
        if not confined.is_file():
            return None
        with open(confined, "rb") as f:
            raw = f.read(_MAX_CONTEXT_BYTES)
        text = raw.decode("utf-8", errors="replace")
    except OSError as e:
        logger.debug("curated context read failed for %s: %s", confined, e)
        return None

    return {
        "context_path": str(candidate),
        **build_excerpt(text, source="context_md"),
    }
