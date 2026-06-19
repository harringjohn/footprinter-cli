"""search_service — multi-source keyword search with visibility filtering.

Orchestrates per-source searches (files, emails, chats, browser) and applies
role-based visibility filtering + content stripping via ``search()``.

Also provides mode-based search (keyword / semantic / hybrid) via the mode
engine: ``mode_search`` and its ``keyword_search`` / ``semantic_search`` /
``hybrid_search`` helpers.

Access control / reconciliation
-------------------------------
The mode engine (``mode_search`` and its keyword/semantic/hybrid helpers) is
**CLI-only**. It runs with implicit ADMIN scope and applies **no access control**
(no access filtering, no permission stripping). It returns raw
FTS5 / vector hits straight from ``db.search`` and the vector store, with no
``visibility`` / ``access`` enrichment, and so it carries no ``role`` parameter.
It must not be wired into a VIEWER-facing path without first building a real
access-control pipeline; doing so would recreate the false sense of access
control that motivated dropping ``role`` from this engine.

The role-aware paths live elsewhere:

- ``search()`` in this module honors ``role`` for keyword search (visibility
  filtering + content stripping).
- ``services/semantic_service.semantic_search()`` honors ``role`` (with D2
  "visible AND allowed" filtering) for semantic search.

MCP tools use those role-aware paths — ``footprinter_search`` calls
``search()`` and ``footprinter_semantic`` calls ``semantic_service``. They do
not call the mode engine.
"""

import os
import sqlite3
from typing import Optional

from footprinter.db.search import (
    chat_fts5_fallback,
    search_browser_keyword,
    search_chats_keyword,
    search_emails_keyword,
    search_files,
    search_files_keyword,
)
from footprinter.db.sql_utils import split_query_terms
from footprinter.services.access_service import (
    filter_results_list,
    strip_content_for_denied,
)
from footprinter.services.includes import status_arg_for_role
from footprinter.services.roles import Role

DEFAULT_SOURCES = ["files", "emails", "chats", "browser"]


def search(
    conn: sqlite3.Connection,
    *,
    role: Role = Role.ADMIN,
    query: str = "",
    sources: Optional[list[str]] = None,
    project: Optional[str] = None,
    client: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    account: Optional[str] = None,
    sender: Optional[str] = None,
    days_back: Optional[int] = None,
    folder: Optional[str] = None,
    mime_type: Optional[str] = None,
    include_unlisted: bool = False,
    include_removed: bool = False,
) -> dict:
    """Search across indexed sources by keyword.

    Returns dict with per-source result lists, a ``counts`` dict with
    per-source ``returned`` (int) and ``has_more`` (bool) for truncation
    detection, and a ``suppressed`` count when visibility filtering
    removes items.  VIEWER role: hidden items excluded, opaque items
    minimized, content stripped for permission-denied items.

    ``include_unlisted`` / ``include_removed`` are ADMIN-only — VIEWER callers
    accept them but the service ignores them and applies the listed-only default.
    """
    if not sources:
        sources = list(DEFAULT_SOURCES)

    status_arg = status_arg_for_role(
        role,
        include_unlisted=include_unlisted,
        include_removed=include_removed,
    )

    results: dict = {}
    counts: dict = {}
    total_suppressed = 0
    has_query = bool(query and query.strip())
    terms = split_query_terms(query) if has_query else []
    has_query = has_query and bool(terms)

    if "files" in sources:
        file_results = search_files_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            account=account,
            folder=folder,
            mime_type=mime_type,
            limit=limit + 1,
            exclude_hidden=not role.sees_all,
            status=status_arg,
        )
        has_more = len(file_results) > limit
        file_results = file_results[:limit]
        if role.sees_all:
            results["files"] = file_results
        else:
            # Strip denied content BEFORE filter_results_list — the latter
            # removes the governance ``access`` field on full-visibility rows,
            # which strip_content_for_denied reads to decide what to redact.
            strip_content_for_denied("file", file_results)
            filtered, suppressed = filter_results_list("file", file_results)
            results["files"] = filtered
            total_suppressed += suppressed
        counts["files"] = {"returned": len(results["files"]), "has_more": has_more}

    if "emails" in sources:
        email_results = search_emails_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            account=account,
            sender=sender,
            days_back=days_back,
            limit=limit + 1,
            exclude_hidden=not role.sees_all,
            status=status_arg,
        )
        has_more = len(email_results) > limit
        email_results = email_results[:limit]
        if role.sees_all:
            results["emails"] = email_results
        else:
            # Strip denied content BEFORE filter_results_list — the latter now
            # removes the governance ``access`` field on full-visibility rows,
            # which strip_content_for_denied reads to decide what to redact.
            strip_content_for_denied("email", email_results)
            filtered, suppressed = filter_results_list("email", email_results)
            results["emails"] = filtered
            total_suppressed += suppressed
        counts["emails"] = {"returned": len(results["emails"]), "has_more": has_more}

    if "chats" in sources:
        chat_results = search_chats_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            limit=limit + 1,
            exclude_hidden=not role.sees_all,
            status=status_arg,
        )
        has_more = len(chat_results) > limit
        chat_results = chat_results[:limit]
        if role.sees_all:
            results["chats"] = chat_results
        else:
            # Strip denied content BEFORE filter_results_list — see emails above.
            strip_content_for_denied("chat", chat_results)
            filtered, suppressed = filter_results_list("chat", chat_results)
            results["chats"] = filtered
            total_suppressed += suppressed
        counts["chats"] = {"returned": len(results["chats"]), "has_more": has_more}

    if "browser" in sources:
        browser_results = search_browser_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            date_from=date_from,
            date_to=date_to,
            limit=limit + 1,
            exclude_hidden=not role.sees_all,
            status=status_arg,
        )
        has_more = len(browser_results) > limit
        browser_results = browser_results[:limit]
        if role.sees_all:
            results["browser"] = browser_results
        else:
            filtered, suppressed = filter_results_list("visit", browser_results)
            results["browser"] = filtered
            total_suppressed += suppressed
        counts["browser"] = {"returned": len(results["browser"]), "has_more": has_more}

    if total_suppressed > 0:
        results["suppressed"] = total_suppressed

    results["counts"] = counts
    return results


# ---------------------------------------------------------------------------
# Mode-based search (keyword / semantic / hybrid)
# ---------------------------------------------------------------------------


def ml_available() -> bool:
    """Check if semantic/ML dependencies are available."""
    try:
        from footprinter.semantic.vector_store import _semantic_available

        return _semantic_available()
    except ImportError:
        return False


def _normalize_file_relevance(distance: float) -> float:
    """Convert ChromaDB distance to 0-1 relevance score."""
    return max(0.0, 1.0 - (distance / 2.0))


def _normalize_path(path: str) -> str:
    """Normalize a file path for dedup comparison."""
    if not path:
        return ""
    return os.path.normpath(os.path.expanduser(path))


def _fts_file_to_result(row: dict) -> dict:
    """Convert a search_files() result row into the merged result format."""
    return {
        "source_type": "file",
        "relevance": row.get("fts_score", 0.5),
        "data": {
            "file_path": row["path"] or row["name"],
            "chunk_index": 0,
            "total_chunks": 1,
            "content_snippet": f"{row['name']} ({row['content_type'] or 'file'})",
            "name": row["name"],
            "source": row["source"],
            "modified_at": row.get("modified_at", ""),
        },
    }


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    """FTS5 keyword search across files and (optionally) chats."""
    merged: list[dict] = []

    file_data = search_files(conn, query, limit=limit, file_ext=type_filter)
    for r in file_data["results"]:
        merged.append(_fts_file_to_result(r))

    if not type_filter:
        chat_results = chat_fts5_fallback(conn, query, limit)
        for r in chat_results:
            merged.append(
                {
                    "source_type": "chat",
                    "relevance": r.get("relevance_score", 0.5),
                    "data": {
                        "chat_title": r.get("chat_title", "(untitled)"),
                        "source": r.get("source", ""),
                        "snippet": r.get("snippet", ""),
                        "chat_id": r.get("chat_id"),
                    },
                }
            )

    merged.sort(key=lambda x: x["relevance"], reverse=True)
    return merged[:limit]


def semantic_search(
    query: str,
    *,
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    """Vector-only search via VectorStore singleton (no DB connection needed)."""
    from footprinter.semantic.vector_store import VectorStore

    store = VectorStore.get_instance()

    filter_meta = None
    if type_filter:
        filter_meta = {"file_type": type_filter}

    file_results = store.search_files(query, n_results=limit, filter_metadata=filter_meta)
    if type_filter:
        chat_results = []
    else:
        chat_results = store.search_chats(query, n_results=limit)

    merged: list[dict] = []
    for r in file_results:
        distance = r.get("distance", 0.0)
        merged.append(
            {
                "source_type": "file",
                "relevance": _normalize_file_relevance(distance),
                "data": r,
            }
        )

    for r in chat_results:
        merged.append(
            {
                "source_type": "chat",
                "relevance": r.get("relevance_score", 0.0),
                "data": r,
            }
        )

    merged.sort(key=lambda x: x["relevance"], reverse=True)
    return merged[:limit]


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    """Hybrid search: FTS5 + vectors merged via RRF for chats, dedup for files."""
    from footprinter.semantic.hybrid_search import (
        chat_snippet,
        reciprocal_rank_fusion,
    )

    # --- File merging: dedup by normalized path, boost overlaps ---
    keyword_file_results: list[dict] = []
    file_data = search_files(conn, query, limit=limit, file_ext=type_filter)
    for r in file_data["results"]:
        keyword_file_results.append(_fts_file_to_result(r))

    semantic_results = semantic_search(query, limit=limit, type_filter=type_filter)
    semantic_file_results = [r for r in semantic_results if r["source_type"] == "file"]
    semantic_chat_results = [r for r in semantic_results if r["source_type"] == "chat"]

    seen_files: dict[str, dict] = {}
    for item in semantic_file_results:
        key = _normalize_path(item["data"].get("file_path", ""))
        seen_files[key] = item

    for item in keyword_file_results:
        key = _normalize_path(item["data"].get("file_path", ""))
        if key in seen_files:
            seen_files[key]["relevance"] = min(1.0, seen_files[key]["relevance"] + 0.15)
        else:
            seen_files[key] = item

    merged: list[dict] = list(seen_files.values())

    # --- Chat merging: use RRF when both sources have results ---
    if not type_filter:
        raw_keyword_chats = chat_fts5_fallback(conn, query, limit)

        if semantic_chat_results and raw_keyword_chats:
            semantic_for_rrf = []
            for item in semantic_chat_results:
                d = item["data"]
                semantic_for_rrf.append(
                    {
                        "chat_id": d.get("chat_id", d.get("chat_title", "")),
                        "chat_title": d.get("chat_title", ""),
                        "message_id": d.get("message_id"),
                        "role": d.get("role", ""),
                        "source": d.get("source", ""),
                        "created_at": d.get("created_at", ""),
                        "snippet": d.get("snippet", ""),
                        "relevance_score": item["relevance"],
                        "chunk_type": d.get("chunk_type", "message"),
                        "chunk_index": d.get("chunk_index", 0),
                        "total_chunks": d.get("total_chunks", 1),
                    }
                )

            rrf_results = reciprocal_rank_fusion(semantic_for_rrf, raw_keyword_chats)
            for r in rrf_results:
                merged.append(
                    {
                        "source_type": "chat",
                        "relevance": r.get("relevance_score", 0.0),
                        "data": {
                            "chat_title": r.get("chat_title", "(untitled)"),
                            "source": r.get("source", ""),
                            "snippet": r.get("snippet", ""),
                            "chat_id": r.get("chat_id"),
                        },
                    }
                )
        elif semantic_chat_results:
            merged.extend(semantic_chat_results)
        elif raw_keyword_chats:
            for r in raw_keyword_chats:
                merged.append(
                    {
                        "source_type": "chat",
                        "relevance": r.get("relevance_score", 0.5),
                        "data": {
                            "chat_title": r.get("chat_title", "(untitled)"),
                            "source": r.get("source", ""),
                            "snippet": chat_snippet(r),
                            "chat_id": r.get("chat_id"),
                        },
                    }
                )

    merged.sort(key=lambda x: x["relevance"], reverse=True)
    return merged[:limit]


def mode_search(
    query: str,
    *,
    mode: str = "hybrid",
    limit: int = 10,
    type_filter: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Dispatch to keyword/semantic/hybrid search by mode.

    ``conn`` is required for keyword and hybrid modes (FTS5 queries).
    Semantic mode uses only VectorStore and does not need a connection.
    """
    if mode == "semantic":
        return semantic_search(query, limit=limit, type_filter=type_filter)
    if conn is None:
        raise ValueError(f"mode={mode!r} requires a database connection")
    if mode == "keyword":
        return keyword_search(conn, query, limit=limit, type_filter=type_filter)
    return hybrid_search(conn, query, limit=limit, type_filter=type_filter)
