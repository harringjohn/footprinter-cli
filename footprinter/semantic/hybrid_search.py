"""Hybrid search functions: FTS5 keyword search, snippet extraction, and RRF fusion."""

import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def extract_snippet(content: str, query: str, window: int = 250) -> str:
    """
    Extract the most relevant snippet from content based on query.

    Args:
        content: Full message content.
        query: Search query.
        window: Character window around match.

    Returns:
        Snippet with ellipsis and context.
    """
    if len(content) <= window * 2:
        return content

    # Find first query term match
    query_terms = query.lower().split()
    content_lower = content.lower()

    best_pos = 0
    for term in query_terms:
        if len(term) >= 3:  # Skip short terms
            pos = content_lower.find(term)
            if pos >= 0:
                best_pos = pos
                break

    # Calculate window
    start = max(0, best_pos - window // 2)
    end = min(len(content), best_pos + window + window // 2)

    # Expand to word boundaries
    if start > 0:
        space = content.rfind(" ", 0, start + 20)
        if space > 0:
            start = space + 1

    if end < len(content):
        space = content.find(" ", end - 20)
        if space > 0:
            end = space

    snippet = content[start:end].strip()

    # Add ellipsis
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


def chat_snippet(row: Dict) -> str:
    """Build a display snippet from a keyword_search result row."""
    return f"Title match: {row['chat_title']}"


def reciprocal_rank_fusion(semantic_results: List[Dict], keyword_results: List[Dict], k: int = 60) -> List[Dict]:
    """
    Combine semantic and keyword results using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) for each result list.
    Higher k reduces the impact of high-ranking items.

    Args:
        semantic_results: Results from semantic search.
        keyword_results: Results from FTS5 keyword search.
        k: RRF constant (default 60, standard value).

    Returns:
        Combined and re-ranked results.
    """
    rrf_scores = {}
    result_data = {}

    # Process semantic results
    for rank, result in enumerate(semantic_results):
        chat_id = result["chat_id"]
        rrf_scores[chat_id] = rrf_scores.get(chat_id, 0) + 1.0 / (k + rank + 1)
        if chat_id not in result_data:
            result_data[chat_id] = result
            result_data[chat_id]["semantic_rank"] = rank + 1
            result_data[chat_id]["keyword_rank"] = None

    # Process keyword results
    for rank, result in enumerate(keyword_results):
        chat_id = result["chat_id"]
        rrf_scores[chat_id] = rrf_scores.get(chat_id, 0) + 1.0 / (k + rank + 1)

        if chat_id not in result_data:
            # Keyword-only result — use "source" if present, fall back to "account"
            source = result.get("source", result.get("account", "unknown"))
            result_data[chat_id] = {
                "chat_id": chat_id,
                "chat_title": result["chat_title"],
                "message_id": None,
                "role": "keyword",
                "source": source,
                "created_at": result["created_at"],
                "snippet": chat_snippet(result),
                "relevance_score": 0,
                "chunk_type": "keyword_match",
                "chunk_index": 0,
                "total_chunks": 1,
                "semantic_rank": None,
                "keyword_rank": rank + 1,
            }
        else:
            result_data[chat_id]["keyword_rank"] = rank + 1

    # Sort by RRF score and update relevance_score
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

    combined = []
    for chat_id in sorted_ids:
        result = result_data[chat_id]
        result["rrf_score"] = round(rrf_scores[chat_id], 4)
        # Use RRF score as the display relevance (scaled for readability)
        result["relevance_score"] = round(min(1.0, rrf_scores[chat_id] * 30), 3)
        combined.append(result)

    return combined


def keyword_search(
    query: str,
    db_path: str,
    account: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """
    FTS5 keyword search on chat titles and summaries.

    Args:
        query: Search query.
        db_path: Path to SQLite database.
        account: Filter by account ('claude', 'chatgpt').
        limit: Maximum results.

    Returns:
        List of chat matches with FTS5 rank scores.
        Uses 'source' key (not 'account') for consistency with semantic results.
    """
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Escape special FTS5 characters and build query
    safe_query = query.replace('"', '""')
    fts_query = f'"{safe_query}"*'

    try:
        if account:
            cursor.execute(
                """
                SELECT chat.id, chat.title, chat.account, chat.created_at, chat.message_count,
                       fts.rank as fts_rank
                FROM chats_fts fts
                JOIN chats chat ON chat.id = fts.rowid
                WHERE chats_fts MATCH ?
                  AND chat.account = ?
                  AND chat.status = 'listed'
                ORDER BY fts.rank
                LIMIT ?
            """,
                (fts_query, account, limit),
            )
        else:
            cursor.execute(
                """
                SELECT chat.id, chat.title, chat.account, chat.created_at, chat.message_count,
                       fts.rank as fts_rank
                FROM chats_fts fts
                JOIN chats chat ON chat.id = fts.rowid
                WHERE chats_fts MATCH ?
                  AND chat.status = 'listed'
                ORDER BY fts.rank
                LIMIT ?
            """,
                (fts_query, limit),
            )

        results = []
        for row in cursor.fetchall():
            # FTS5 rank is negative (more negative = better match)
            fts_score = min(1.0, abs(row["fts_rank"]) / 10.0)

            results.append(
                {
                    "chat_id": row["id"],
                    "chat_title": row["title"] or "(untitled)",
                    "source": row["account"] or "unknown",
                    "created_at": row["created_at"] or "",
                    "message_count": row["message_count"] or 0,
                    "snippet": row["title"] or "",
                    "fts_score": fts_score,
                    "match_type": "keyword",
                }
            )

    except Exception as e:
        logger.error(f"FTS5 search error: {e}")
        results = []

    conn.close()
    return results


def fts5_fallback_search(
    query: str,
    n_results: int = 20,
    source: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[List[Dict], bool]:
    """
    FTS5-only fallback for when ML dependencies are unavailable.

    Normalizes result shape to match hybrid search output so consumers
    don't need to branch on search mode.

    Args:
        query: Search query.
        n_results: Max results.
        source: Filter by source/account.
        db_path: Path to SQLite database (auto-detected if None).

    Returns:
        (results, True) — True indicates fallback mode.
    """
    if db_path is None:
        from footprinter.paths import get_db_path

        db_path = str(get_db_path())

    raw = keyword_search(query, db_path=db_path, account=source, limit=n_results)

    # Normalize to match hybrid search result shape
    results = []
    for r in raw:
        results.append(
            {
                "chat_id": r["chat_id"],
                "chat_title": r["chat_title"],
                "message_id": None,
                "role": "keyword",
                "source": r["source"],
                "created_at": r["created_at"],
                "snippet": chat_snippet(r),
                "relevance_score": round(r["fts_score"], 3),
                "chunk_type": "keyword_match",
                "chunk_index": 0,
                "total_chunks": 1,
            }
        )

    return results, True
