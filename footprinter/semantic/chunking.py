"""Pure chunking function for splitting content into overlapping chunks."""

from typing import List, Tuple

DEFAULT_CHUNK_SIZE = 1000  # chars — tuned for MiniLM-L6-v2 (256-token window)
DEFAULT_CHUNK_OVERLAP = 150  # chars (15% of default chunk size)


def chunk_content(
    content: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Tuple[str, int, int]]:
    """
    Split content into overlapping chunks with word-boundary awareness.

    Args:
        content: Text to split.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Character overlap between consecutive chunks.

    Returns:
        List of (chunk_text, chunk_index, total_chunks) tuples.
    """
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
        start = end - chunk_overlap if end < len(content) else end

    # Set total_chunks
    total = len(chunks)
    return [(text, idx, total) for text, idx, _ in chunks]
