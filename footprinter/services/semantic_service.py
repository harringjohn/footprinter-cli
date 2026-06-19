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

# Vector-read provenance (note §5.2): the excerpt was reassembled from indexed
# chunks straight out of ChromaDB, not re-read from disk.
_VECTOR_READ_SOURCE = "vector_chunks"

# Keep-allowlist for the vector-read result: the excerpt contract + the
# reassembly provenance/caveat fields. No governance data rides along.
_VECTOR_READ_FIELDS = {
    "chunk_indices",
    "from_vectors",
    "reassembled",
    "incomplete",
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


def read_file_chunks(
    conn: sqlite3.Connection,
    file_id: int,
    query: str,
    *,
    role: Role = Role.ADMIN,
) -> dict:
    """Read a file's content straight from indexed chunks (note §5, §5.1).

    Ranks the file's chunks for ``query``, applies the §5.1 reassembly heuristic
    (isolated match → matched chunk ± 1 neighbors; adjacent matches → contiguous
    span; scattered matches or ``total_chunks <= 2`` → whole available chunk
    set), fetches the needed chunks from the vector store by chunk id, and
    reassembles them in ``chunk_index`` order.

    Returns the uniform excerpt contract plus the vector-read provenance /
    completeness caveats (``from_vectors``, ``reassembled``, ``incomplete``,
    ``chunk_indices``). On no match returns ``{"status": "no_match"}``; on
    vector-store unavailability returns ``{"status": "unavailable"}``.

    The caller is responsible for the D2 visible-AND-allowed gate — this
    function does not re-check access (it operates on an already-gated file id).
    No governance fields are surfaced.
    """
    try:
        from footprinter.semantic.vector_store import VectorStore

        store = VectorStore.get_instance()
    except Exception as e:  # noqa: BLE001 — broad: import + init both realistic
        logger.warning("Vector read unavailable (%s)", e)
        return {"status": "unavailable"}

    # Rank the file's chunks for the query, scoped to this file id.
    matches = store.search_files(
        query=query,
        n_results=_get_max_chunks_per_file() * 3,
        filter_metadata={"file_id": file_id},
    )
    matches = [m for m in matches if m.get("file_id") == file_id]
    if not matches:
        return {"status": "no_match"}

    total_chunks = matches[0].get("total_chunks") or 1
    matched_indices = sorted({m.get("chunk_index", 0) for m in matches})

    selected, incomplete = _select_chunk_span(matched_indices, total_chunks)

    chunks = _fetch_selected_chunks(store, file_id, selected, total_chunks)
    if not chunks:
        return {"status": "no_match"}

    selected_present = [c["chunk_index"] for c in chunks]
    reassembled = "\n\n".join(c.get("content", "") for c in chunks)

    result = build_excerpt(
        reassembled,
        source=_VECTOR_READ_SOURCE,
        budget=len(reassembled),  # whole reassembled span — no further slicing
        chars_available=len(reassembled),
    )
    result["chunk_indices"] = selected_present
    result["from_vectors"] = True
    result["reassembled"] = True
    # Incomplete when fewer than the file's full chunk set was returned.
    result["incomplete"] = incomplete or len(selected_present) < total_chunks
    return {k: v for k, v in result.items() if k in _VECTOR_READ_FIELDS}


def _select_chunk_span(
    matched_indices: List[int], total_chunks: int
) -> tuple[List[int], bool]:
    """Apply the §5.1 reassembly heuristic to pick which chunk indices to return.

    Returns ``(indices, incomplete)`` where ``incomplete`` is ``True`` when only a
    neighborhood span (not the whole file) was selected.

    - ``total_chunks <= 2`` → whole set (complete).
    - Expand each matched index to ``± 1`` neighbors (clamped to range) and union.
      Contiguous union → that span (a neighborhood; incomplete unless it covers
      the whole file). Gapped union (scattered matches) → whole set (complete).
    """
    last = total_chunks - 1
    if total_chunks <= 2:
        return list(range(total_chunks)), False

    expanded: set[int] = set()
    for idx in matched_indices:
        for n in (idx - 1, idx, idx + 1):
            if 0 <= n <= last:
                expanded.add(n)

    span = sorted(expanded)
    is_contiguous = span == list(range(span[0], span[-1] + 1))
    if not is_contiguous:
        # Scattered matches → fall back to the whole available set.
        return list(range(total_chunks)), False

    incomplete = len(span) < total_chunks
    return span, incomplete


def _fetch_selected_chunks(
    store, file_id: int, selected: List[int], total_chunks: int
) -> List[dict]:
    """Fetch the selected chunk indices for a file, reassembled by index.

    Prefers the flat ``get_chunks_by_ids`` lookup (note §7: ~0.2 ms regardless of
    file size); falls back to enumerating the file's chunks via
    ``get_file_chunks`` and filtering to the selected indices.
    """
    wanted = set(selected)
    ids = [f"file_{file_id}_chunk_{i}" for i in selected]
    try:
        chunks = store.get_chunks_by_ids(ids)
    except Exception as e:  # noqa: BLE001 — broad: ChromaDB lookup is best-effort
        logger.warning("get_chunks_by_ids failed (%s), enumerating file chunks", e)
        chunks = []
    if not chunks:
        chunks = [
            c
            for c in store.get_file_chunks(file_id)
            if c.get("chunk_index") in wanted
        ]
    return sorted(chunks, key=lambda c: c.get("chunk_index", 0))


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
