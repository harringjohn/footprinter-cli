"""semantic_service — embedding search with FTS5 fallback and access control.

D2 access rule: semantic matches are content-derived, so visible items also
require mcp_read='allow' (presence in results reveals content).
"""

import logging
import sqlite3
from typing import Dict, List

from footprinter.db.search import (
    chat_fts5_fallback,
    enrich_chat_visibility,
    enrich_file_metadata,
    file_fts5_fallback,
)
from footprinter.services.access_service import (
    resolve_inherit_permission,
    resolve_inherit_visibility,
)
from footprinter.services.roles import Role

logger = logging.getLogger(__name__)

_VALID_SOURCES = frozenset({"chats", "files", "all"})

_CHAT_FIELDS = {
    "chat_id",
    "chat_title",
    "snippet",
    "relevance_score",
    "source",
    "created_at",
    "message_id",
}

_FILE_FIELDS = {
    "id",
    "name",
    "path",
    "content_type",
    "size_bytes",
    "modified_at",
    "relevance_score",
    "snippet",
}

# Search outcome: ok (vector worked), degraded (FTS5 fallback), failed (both crashed)
_OK = "ok"
_DEGRADED = "degraded"
_FAILED = "failed"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    role: Role = Role.ADMIN,
    source: str = "all",
    limit: int = 10,
) -> dict:
    """Search chats and/or files by semantic similarity.

    Returns dict with source-specific keys (``chats``, ``files``), ``summary``,
    and optionally ``note`` and ``suppressed``.  Returns ``{"status": ...}``
    for validation errors.
    """
    if not query or len(query) < 3:
        return {"status": "invalid_query"}

    if source not in _VALID_SOURCES:
        return {"status": "invalid_source"}

    result: dict = {"query": query}
    all_notes: list[str] = []
    total_suppressed = 0
    chat_status = _OK
    file_status = _OK

    if source in ("chats", "all"):
        chats, notes, suppressed, chat_status = _search_chats(
            conn,
            query,
            limit,
            role,
        )
        result["chats"] = chats
        all_notes.extend(notes)
        total_suppressed += suppressed

    if source in ("files", "all"):
        files, notes, suppressed, file_status = _search_files(
            conn,
            query,
            limit,
            role,
        )
        result["files"] = files
        all_notes.extend(notes)
        total_suppressed += suppressed

    if total_suppressed > 0:
        result["suppressed"] = total_suppressed

    summary_parts = []
    if "chats" in result:
        summary_parts.append(
            _build_chat_summary(result["chats"], query, status=chat_status),
        )
    if "files" in result:
        summary_parts.append(
            _build_file_summary(result["files"], query, status=file_status),
        )
    result["summary"] = " ".join(summary_parts)

    if all_notes:
        result["note"] = " ".join(dict.fromkeys(all_notes))

    return result


# ---------------------------------------------------------------------------
# Chat search
# ---------------------------------------------------------------------------


def _search_chats(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    role: Role,
) -> tuple[list[dict], list[str], int, str]:
    """Search chats via VectorStore → FTS5 fallback → enrich → filter."""
    notes: list[str] = []
    status = _OK
    results: list[dict] = []

    # Try vector search first
    try:
        from footprinter.semantic.vector_store import VectorStore

        store = VectorStore.get_instance()
        results = store.search_chats(query=query, n_results=limit)
    except Exception as e:
        logger.warning("Vector search unavailable (%s), falling back to FTS5", e)
        status = _DEGRADED
        try:
            results = chat_fts5_fallback(conn, query, limit)
        except Exception as fallback_err:
            logger.warning("Chat FTS5 fallback failed: %s", fallback_err)
            return [], ["Chat search failed — try footprinter_search"], 0, _FAILED

    # Enrich with visibility from DB
    chat_ids = [r.get("chat_id") for r in results if r.get("chat_id")]
    vis_lookup = enrich_chat_visibility(conn, chat_ids) if chat_ids else {}

    for r in results:
        db_row = vis_lookup.get(r.get("chat_id"))
        r["id"] = r.get("chat_id")
        r["account"] = db_row["account"] if db_row else ""
        r["mcp_view"] = db_row["mcp_view"] if db_row else "hidden"
        r["mcp_read"] = db_row["mcp_read"] if db_row else None

    # Access control filtering
    if role.sees_all:
        filtered = results
        suppressed = 0
    else:
        # D2: presence in semantic results reveals content — exclude anything
        # not both visible AND allowed. Fail-closed on null/missing values.
        filtered = [
            r for r in results
            if resolve_inherit_visibility(r.get("mcp_view")) == "visible"
            and resolve_inherit_permission(r.get("mcp_read")) == "allow"
        ]
        suppressed = len(results) - len(filtered)

    # Trim visible results to presentation fields
    trimmed = [_trim_chat_result(r) if r.get("mcp_view") == "visible" else r for r in filtered]

    if status == _DEGRADED:
        notes.append("Results are keyword-based (semantic search unavailable)")

    return trimmed, notes, suppressed, status


# ---------------------------------------------------------------------------
# File search
# ---------------------------------------------------------------------------


def _search_files(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    role: Role,
) -> tuple[list[dict], list[str], int, str]:
    """Search files via VectorStore (+ enrich) or FTS5 fallback → filter."""
    notes: list[str] = []
    status = _OK
    enriched: List[Dict] = []
    dropped = 0

    try:
        from footprinter.semantic.vector_store import VectorStore

        store = VectorStore.get_instance()
        raw_results = store.search_files(query=query, n_results=limit * 3)
    except Exception as e:
        logger.warning("Vector search unavailable (%s), falling back to FTS5", e)
        status = _DEGRADED
        try:
            enriched = file_fts5_fallback(conn, query, limit)
        except Exception as fallback_err:
            logger.warning("File FTS5 fallback failed: %s", fallback_err)
            return [], ["File search failed — try footprinter_search"], 0, _FAILED
    else:
        for r in raw_results:
            distance = r.get("distance") or 0
            r["relevance_score"] = round(max(0, 1 - (distance / 2)), 3)
            r["snippet"] = r.get("content_snippet", "")

        deduped, dropped = _deduplicate_by_file(raw_results)
        if dropped > 0:
            logger.warning(
                "Dropped %d vector results with missing file_id",
                dropped,
            )

        file_ids = [r["file_id"] for r in deduped if r.get("file_id")]
        if file_ids:
            db_lookup = enrich_file_metadata(conn, file_ids)
            for r in deduped:
                db_row = db_lookup.get(r["file_id"])
                if db_row:
                    r.update(db_row)
            enriched = [r for r in deduped if r.get("id")]

    # Access control filtering
    if role.sees_all:
        filtered = enriched
        suppressed = 0
    else:
        # D2: presence in semantic results reveals content — exclude anything
        # not both visible AND allowed. Fail-closed on null/missing values.
        filtered = [
            r for r in enriched
            if resolve_inherit_visibility(r.get("mcp_view")) == "visible"
            and resolve_inherit_permission(r.get("mcp_read")) == "allow"
        ]
        suppressed = len(enriched) - len(filtered)

    trimmed = [_trim_file_result(r) if r.get("mcp_view") == "visible" else r for r in filtered]
    trimmed = trimmed[:limit]

    if status == _DEGRADED:
        notes.append("Results are keyword-based (semantic search unavailable)")
    if dropped > 0:
        notes.append(f"Dropped {dropped} results with missing file_id. Run --rebuild-vectors to fix.")

    return trimmed, notes, suppressed, status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deduplicate_by_file(results: List[Dict]) -> tuple[List[Dict], int]:
    """Group by file_id, keep highest-relevance chunk per file."""
    best: Dict[int, Dict] = {}
    dropped = 0
    for r in results:
        fid = r.get("file_id")
        if fid is None:
            dropped += 1
            continue
        existing = best.get(fid)
        if existing is None or r.get("relevance_score", 0) > existing.get("relevance_score", 0):
            best[fid] = r
    return list(best.values()), dropped


def _trim_chat_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k in _CHAT_FIELDS}


def _trim_file_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k in _FILE_FIELDS}


def _build_chat_summary(
    chats: list[dict],
    query: str,
    *,
    status: str = _OK,
) -> str:
    visible = [c for c in chats if c.get("chat_title")]
    opaque_count = len(chats) - len(visible)
    count = len(chats)
    if count > 0:
        label = "chat" if count == 1 else "chats"
        top_titles = [c["chat_title"] for c in visible[:3]]
        summary = f"Found {count} {label} matching '{query}'."
        if top_titles:
            summary += f" Top: {', '.join(repr(t) for t in top_titles)}."
        if opaque_count > 0:
            summary += f" ({opaque_count} with restricted visibility.)"
        if status == _DEGRADED:
            summary += " (keyword match — semantic search was unavailable)"
    else:
        if status == _FAILED:
            summary = f"Chat search failed for '{query}' — try footprinter_search for keyword matching."
        elif status == _DEGRADED:
            summary = f"Semantic search unavailable — keyword search returned no chats for '{query}'."
        else:
            summary = (
                f"No chats found for '{query}'. "
                f"Tips: try different keywords, use footprinter_search "
                f"for broader keyword matching across files/emails/browser."
            )
    return summary


def _build_file_summary(
    files: list[dict],
    query: str,
    *,
    status: str = _OK,
) -> str:
    visible = [f for f in files if f.get("name")]
    opaque_count = len(files) - len(visible)
    count = len(files)
    if count > 0:
        label = "file" if count == 1 else "files"
        top_names = [f["name"] for f in visible[:3]]
        summary = f"Found {count} {label} matching '{query}'."
        if top_names:
            summary += f" Top: {', '.join(repr(n) for n in top_names)}."
        if opaque_count > 0:
            summary += f" ({opaque_count} with restricted visibility.)"
        if status == _DEGRADED:
            summary += " (keyword match — semantic search was unavailable)"
    else:
        if status == _FAILED:
            summary = f"File search failed for '{query}' — try footprinter_search for keyword matching."
        elif status == _DEGRADED:
            summary = f"Semantic search unavailable — keyword search returned no files for '{query}'."
        else:
            summary = (
                f"No files found for '{query}'. "
                f"Tips: try different keywords, use footprinter_search "
                f"for exact keyword matching across file names/paths."
            )
    return summary
