"""semantic_service — embedding search with FTS5 fallback and access control.

D2 access rule: semantic matches are content-derived, so visible items also
require access='allow' (presence in results reveals content).
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
from footprinter.services.includes import status_arg_for_role
from footprinter.services.roles import Role
from footprinter.utils.text import build_excerpt

logger = logging.getLogger(__name__)

_VALID_SOURCES = frozenset({"chats", "files", "all"})

# The uniform excerpt contract (footprinter_search shares the same field names).
_EXCERPT_FIELDS = {
    "excerpt",
    "excerpt_source",
    "chars_returned",
    "chars_available",
    "has_more",
}

_CHAT_FIELDS = {
    "chat_id",
    "chat_title",
    "relevance_score",
    "source",
    "created_at",
    "message_id",
    # chunk_index / total_chunks present only on chunk-sourced excerpts.
    "chunk_index",
    "total_chunks",
    *_EXCERPT_FIELDS,
}

_FILE_FIELDS = {
    "id",
    "name",
    "path",
    "content_type",
    "size_bytes",
    "modified_at",
    "relevance_score",
    "chunk_index",
    "total_chunks",
    # Top-N matched chunks for this file (each a per-chunk excerpt dict).
    "chunks",
    *_EXCERPT_FIELDS,
}

# Per-chunk dict shape for the ``chunks`` list — excerpt contract + chunk index
# fields only. No governance data rides along on individual chunks.
_CHUNK_FIELDS = {
    "relevance_score",
    "chunk_index",
    "total_chunks",
    *_EXCERPT_FIELDS,
}

# Search outcome: ok (vector worked), degraded (FTS5 fallback), failed (both crashed)
_OK = "ok"
_DEGRADED = "degraded"
_FAILED = "failed"

# Vector-read widening: full chunk text up to a configurable cap, and the
# top-N matched chunks per file. Defaults track vectorization.chunk_size
# (~1000 chars ≈ 250 tokens) and a conservative N.
_DEFAULT_MAX_CHUNK_CHARS = 1000
_DEFAULT_MAX_CHUNKS_PER_FILE = 3


def _get_max_chunk_chars() -> int:
    """Per-chunk excerpt cap (chars). ``0`` means no cap — return whole chunk.

    Lazy config resolver: reads ``semantic.max_chunk_chars`` at call time so
    config changes are picked up without re-import, falling back to the module
    default on any failure.
    """
    try:
        from footprinter.source_registry import get_config

        val = get_config().get("semantic", {}).get(
            "max_chunk_chars", _DEFAULT_MAX_CHUNK_CHARS
        )
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            return val
        logger.warning(
            "semantic.max_chunk_chars: expected non-negative int, using default %d",
            _DEFAULT_MAX_CHUNK_CHARS,
        )
        return _DEFAULT_MAX_CHUNK_CHARS
    except Exception as e:
        logger.debug("Config unavailable for max_chunk_chars: %s", e)
        return _DEFAULT_MAX_CHUNK_CHARS


def _get_max_chunks_per_file() -> int:
    """Number of matched chunks to return per file (≥1).

    Lazy config resolver: reads ``semantic.max_chunks_per_file``, clamps to at
    least 1, and falls back to the module default on any failure.
    """
    try:
        from footprinter.source_registry import get_config

        val = get_config().get("semantic", {}).get(
            "max_chunks_per_file", _DEFAULT_MAX_CHUNKS_PER_FILE
        )
        if isinstance(val, int) and not isinstance(val, bool):
            return max(1, val)
        logger.warning(
            "semantic.max_chunks_per_file: expected int, using default %d",
            _DEFAULT_MAX_CHUNKS_PER_FILE,
        )
        return _DEFAULT_MAX_CHUNKS_PER_FILE
    except Exception as e:
        logger.debug("Config unavailable for max_chunks_per_file: %s", e)
        return _DEFAULT_MAX_CHUNKS_PER_FILE


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
    include_unlisted: bool = False,
    include_removed: bool = False,
) -> dict:
    """Search chats and/or files by semantic similarity.

    Returns dict with source-specific keys (``chats``, ``files``), ``summary``,
    and optionally ``note`` and ``suppressed``.  Returns ``{"status": ...}``
    for validation errors.

    ``include_unlisted`` / ``include_removed`` are ADMIN-only — VIEWER callers
    accept them but the listed-only default still applies.
    """
    if not query or len(query) < 3:
        return {"status": "invalid_query"}

    if source not in _VALID_SOURCES:
        return {"status": "invalid_source"}

    status_arg = status_arg_for_role(
        role,
        include_unlisted=include_unlisted,
        include_removed=include_removed,
    )

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
            status_arg=status_arg,
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
            status_arg=status_arg,
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
    *,
    status_arg: "str | list[str] | None" = None,
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
            results = chat_fts5_fallback(conn, query, limit, status=status_arg)
        except Exception as fallback_err:
            logger.warning("Chat FTS5 fallback failed: %s", fallback_err)
            return [], ["Chat search failed — try footprinter_search"], 0, _FAILED

    # Enrich with visibility from DB
    chat_ids = [r.get("chat_id") for r in results if r.get("chat_id")]
    vis_lookup = (
        enrich_chat_visibility(conn, chat_ids, status=status_arg) if chat_ids else {}
    )

    # Vector hits excerpt from the matched chunk; the FTS5 fallback only has
    # the chat title. (Message-derived chat excerpts are a follow-up issue.)
    excerpt_source = "title" if status == _DEGRADED else "chunk"

    for r in results:
        db_row = vis_lookup.get(r.get("chat_id"))
        r["id"] = r.get("chat_id")
        r["account"] = db_row["account"] if db_row else ""
        r["visibility"] = db_row["visibility"] if db_row else "hidden"
        r["access"] = db_row["access"] if db_row else None
        r.update(
            build_excerpt(
                r.get("snippet") or "",
                source=excerpt_source,
                chars_available=r.get("content_length"),
            )
        )
        r.pop("snippet", None)
        r.pop("content_length", None)

    # Access control filtering
    if role.sees_all:
        filtered = results
        suppressed = 0
    else:
        # D2: presence in semantic results reveals content — exclude anything
        # not both visible AND allowed. Fail-closed on null/missing values.
        filtered = [
            r for r in results
            if resolve_inherit_visibility(r.get("visibility")) == "full"
            and resolve_inherit_permission(r.get("access")) == "allow"
        ]
        suppressed = len(results) - len(filtered)

    # Trim visible results to presentation fields
    trimmed = [_trim_chat_result(r) if r.get("visibility") == "full" else r for r in filtered]

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
    *,
    status_arg: "str | list[str] | None" = None,
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
            enriched = file_fts5_fallback(conn, query, limit, status=status_arg)
        except Exception as fallback_err:
            logger.warning("File FTS5 fallback failed: %s", fallback_err)
            return [], ["File search failed — try footprinter_search"], 0, _FAILED
    else:
        max_chunk_chars = _get_max_chunk_chars()
        for r in raw_results:
            distance = r.get("distance") or 0
            r["relevance_score"] = round(max(0, 1 - (distance / 2)), 3)
            # Vector file hit → matched-chunk excerpt. content_snippet is the
            # full chunk; build_excerpt applies the single configurable cap.
            # 0 means "no cap" — pass a budget ≥ the chunk length so the helper
            # returns the whole chunk rather than slicing to empty.
            text = r.get("content_snippet") or ""
            budget = max_chunk_chars if max_chunk_chars > 0 else len(text)
            r.update(
                build_excerpt(
                    text,
                    source="chunk",
                    budget=budget,
                    chars_available=r.get("content_length"),
                )
            )
            r.pop("content_snippet", None)
            r.pop("content_length", None)

        grouped, dropped = _top_chunks_by_file(raw_results, _get_max_chunks_per_file())
        if dropped > 0:
            logger.warning(
                "Dropped %d vector results with missing file_id",
                dropped,
            )

        file_ids = [r["file_id"] for r in grouped if r.get("file_id")]
        if file_ids:
            db_lookup = enrich_file_metadata(conn, file_ids, status=status_arg)
            for r in grouped:
                db_row = db_lookup.get(r["file_id"])
                if db_row:
                    r.update(db_row)
            enriched = [r for r in grouped if r.get("id")]

    # Access control filtering
    if role.sees_all:
        filtered = enriched
        suppressed = 0
    else:
        # D2: presence in semantic results reveals content — exclude anything
        # not both visible AND allowed. Fail-closed on null/missing values.
        filtered = [
            r for r in enriched
            if resolve_inherit_visibility(r.get("visibility")) == "full"
            and resolve_inherit_permission(r.get("access")) == "allow"
        ]
        suppressed = len(enriched) - len(filtered)

    trimmed = [_trim_file_result(r) if r.get("visibility") == "full" else r for r in filtered]
    trimmed = trimmed[:limit]

    if status == _DEGRADED:
        notes.append("Results are keyword-based (semantic search unavailable)")
    if dropped > 0:
        notes.append(f"Dropped {dropped} results with missing file_id. Run 'fp doctor semantic' to fix.")

    return trimmed, notes, suppressed, status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_excerpt(result: Dict) -> Dict:
    """Project a vector result to a per-chunk excerpt dict.

    Carries only the excerpt contract + chunk-index + relevance fields — no
    governance data — so each entry in a file's ``chunks`` list is safe to
    surface to any role.
    """
    return {k: v for k, v in result.items() if k in _CHUNK_FIELDS}


def _top_chunks_by_file(results: List[Dict], n: int) -> tuple[List[Dict], int]:
    """Group vector hits by file_id, keep the top-N chunks per file.

    Returns ``(rows, dropped)`` where each row is the file's best chunk
    (highest relevance) carrying that chunk's top-level excerpt fields, plus a
    ``chunks`` list of the top-N matched chunks for the file ordered by
    relevance descending (stable on ties by ``chunk_index`` ascending). The
    first ``chunks`` entry mirrors the row's top-level excerpt. Results with a
    missing ``file_id`` are dropped and counted.
    """
    groups: Dict[int, List[Dict]] = {}
    dropped = 0
    for r in results:
        fid = r.get("file_id")
        if fid is None:
            dropped += 1
            continue
        groups.setdefault(fid, []).append(r)

    rows: List[Dict] = []
    for chunks in groups.values():
        ordered = sorted(
            chunks,
            key=lambda c: (-c.get("relevance_score", 0), c.get("chunk_index", 0)),
        )
        top = ordered[:n]
        row = dict(top[0])  # representative row = best chunk's fields
        row["chunks"] = [_chunk_excerpt(c) for c in top]
        rows.append(row)
    return rows, dropped


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
