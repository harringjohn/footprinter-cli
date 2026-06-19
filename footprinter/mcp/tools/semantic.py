"""Semantic search tool — thin adapter over semantic_service."""

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.mcp.errors import mcp_error
from footprinter.services import semantic_service
from footprinter.services.roles import Role

_SEMANTIC_AVAILABLE = True


@handle_db_errors
def footprinter_semantic(
    query: str,
    source: str = "all",
    limit: int = 10,
    include_unlisted: bool = False,
    include_removed: bool = False,
) -> dict:
    """Search chats and/or files by meaning using semantic (embedding-based) search.

    SEARCH BEHAVIOR:
    - Uses vector embeddings to find items with similar meaning, not exact keyword
      matches. "auth problems" can find chats about "login failures"; "revenue
      forecast" can find files about "financial projections".
    - Falls back to FTS5 keyword search per-collection when ML dependencies
      (ChromaDB, sentence-transformers) are unavailable. A note is included when
      fallback is active.
    - The ``source`` parameter controls which collections to search:
      "chats" (conversations only), "files" (file content only), or "all" (both).
    - ``limit`` applies per-collection: source="all" with limit=10 returns up to
      10 chats + 10 files.
    - Files are chunked during indexing; results are deduplicated so each file
      appears at most once (best-matching chunk).

    QUERY TIPS:
    - Natural language works best: describe what you're looking for conceptually.
    - Ideal query length is 3-10 words. Very short (1 word) or very long (10+ words)
      queries reduce relevance.
    - Good: "database migration strategies" — Bad: "database"
    - Good: "client onboarding process" — Bad: "how did we onboard that new client
      who came in last month through the partner referral program"

    WHEN TO USE THIS vs footprinter_search:
    - Use THIS tool for meaning-based search — finding items by topic even when you
      don't know the exact words used.
    - Use footprinter_search for exact keyword matches in names/subjects/titles, or
      to search non-semantic sources (emails, browser history).

    Args:
        query: Natural language search query (minimum 3 characters).
        source: Which collection(s) to search: "chats", "files", or "all" (default).
        limit: Max results per collection (default 10).
        include_unlisted: ADMIN-only. Include items with status='unlisted'. VIEWER
            (the default for MCP) accepts this but always sees listed-only.
        include_removed: ADMIN-only. Include items with status='removed'. VIEWER
            (the default for MCP) accepts this but always sees listed-only.

    Returns:
        Dict with source-specific keys. "chats" and/or "files" lists are present
        based on the source parameter. Only items with both visible access AND
        read permission appear (semantic matches are content-derived, so
        presence in results reveals content — per decision D2). Visible+allowed
        chats have: chat_id, chat_title, the excerpt contract (excerpt,
        excerpt_source, chars_returned, chars_available, has_more) plus
        chunk_index/total_chunks, relevance_score, source, created_at, message_id.
        Visible+allowed files have: id, name, path, content_type, size_bytes,
        modified_at, relevance_score, the same excerpt contract, and
        chunk_index/total_chunks.
        Opaque items have minimal fields only. Hidden and permission-denied
        items are excluded. Includes "summary" with a human-readable overview.
    """
    # Validate before opening DB connection
    if not query or len(query) < 3:
        return mcp_error(
            "QUERY_INVALID",
            internal_message=f"query too short: {len(query) if query else 0}",
        )
    if source not in ("chats", "files", "all"):
        return mcp_error(
            "INVALID_INPUT",
            hint="source must be 'chats', 'files', or 'all'",
        )

    with get_db() as conn:
        return semantic_service.semantic_search(
            conn,
            query,
            role=Role.VIEWER,
            source=source,
            limit=limit,
            include_unlisted=include_unlisted,
            include_removed=include_removed,
        )
