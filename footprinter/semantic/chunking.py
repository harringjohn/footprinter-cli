"""Pure chunking function for splitting content into overlapping chunks."""

import logging
import warnings
from typing import List, Tuple

DEFAULT_CHUNK_SIZE = 1000  # chars — tuned for MiniLM-L6-v2 (256-token window)
DEFAULT_CHUNK_OVERLAP = 0.15  # fraction of chunk_size (15%)

_logger = logging.getLogger(__name__)


def _get_chunk_size() -> int:
    try:
        from footprinter.source_registry import get_config

        return get_config().get("limits", {}).get("chunk_size", DEFAULT_CHUNK_SIZE)
    except Exception:
        _logger.debug("Config unavailable for chunk_size, using default %d", DEFAULT_CHUNK_SIZE)
        return DEFAULT_CHUNK_SIZE


def chunk_content(
    content: str,
    chunk_size: int | None = None,
    chunk_overlap: float = DEFAULT_CHUNK_OVERLAP,
) -> List[Tuple[str, int, int]]:
    """
    Split content into overlapping chunks with word-boundary awareness.

    Args:
        content: Text to split.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Fractional overlap (0.0–1.0) between consecutive chunks.

    Returns:
        List of (chunk_text, chunk_index, total_chunks) tuples.
    """
    if chunk_size is None:
        chunk_size = _get_chunk_size()

    if chunk_overlap < 0:
        raise ValueError(
            f"chunk_overlap must be non-negative, got {chunk_overlap}"
        )

    if chunk_overlap >= 1.0:
        warnings.warn(
            "Passing chunk_overlap as absolute characters is deprecated; "
            "use a fractional value in [0.0, 1.0) instead (e.g. 0.15 for 15%).",
            DeprecationWarning,
            stacklevel=2,
        )
        effective_overlap = int(chunk_overlap)
    else:
        effective_overlap = int(chunk_overlap * chunk_size)

    if effective_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap produces {effective_overlap} chars of overlap, "
            f"which must be less than chunk_size ({chunk_size})"
        )

    if len(content) <= chunk_size:
        return [(content, 0, 1)]

    chunks = []
    start = 0
    chunk_index = 0

    while start < len(content):
        end = start + chunk_size

        # Try to break at word boundary
        if end < len(content):
            # Look for space within last 200 chars of chunk
            space_pos = content.rfind(" ", end - 200, end)
            if space_pos > start:
                end = space_pos

        chunk_text = content[start:end].strip()
        if chunk_text:
            chunks.append((chunk_text, chunk_index, -1))  # Total set later
            chunk_index += 1

        # Move start with overlap
        start = end - effective_overlap if end < len(content) else end

    # Set total_chunks
    total = len(chunks)
    return [(text, idx, total) for text, idx, _ in chunks]
