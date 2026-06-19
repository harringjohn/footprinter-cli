import re

# Flat excerpt ceiling shared across every content-bearing search source.
# Retires the ad-hoc 200 / 250 / 500 slices that excerpts were built at before.
EXCERPT_BUDGET = 500


def _make_slug(name: str) -> str:
    """Convert a display name to a URL-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_excerpt(
    text: str,
    *,
    source: str,
    budget: int = EXCERPT_BUDGET,
    chars_available: int | None = None,
) -> dict:
    """Build the uniform excerpt-contract fields for a content-bearing result.

    All content-bearing search sources (keyword + semantic) funnel their
    excerpt text through this helper so the result shape, size budget, and
    provenance are identical everywhere.

    Parameters
    ----------
    text:
        The candidate excerpt text. Callers that pass the full underlying
        content let ``chars_available`` default to ``len(text)``; callers that
        pass a pre-windowed slice (e.g. a query-centered chunk window) must
        pass the full content length via ``chars_available`` so ``has_more``
        is accurate.
    source:
        The ``excerpt_source`` provenance value naming where ``text`` came from
        (e.g. ``chunk``, ``content_preview``, ``body_preview``, ``title``).
    budget:
        Maximum excerpt length. Defaults to the shared ``EXCERPT_BUDGET``.
    chars_available:
        Total content available behind the excerpt. Defaults to ``len(text)``.

    Returns
    -------
    dict with keys ``excerpt``, ``excerpt_source``, ``chars_returned``,
    ``chars_available``, ``has_more``. Chunk sources add ``chunk_index`` /
    ``total_chunks`` at the call site.
    """
    text = text or ""
    excerpt = text[:budget]
    available = chars_available if chars_available is not None else len(text)
    chars_returned = len(excerpt)
    return {
        "excerpt": excerpt,
        "excerpt_source": source,
        "chars_returned": chars_returned,
        "chars_available": available,
        "has_more": available > chars_returned,
    }
