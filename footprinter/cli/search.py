"""
Command-line search interface — keyword, semantic, and hybrid modes.
"""

import argparse
import os
import sys

from rich.console import Console

from footprinter.cli._common import open_db, output_json

try:
    from footprinter.semantic.vector_store import VectorStore, _semantic_available

    _HAS_ML = _semantic_available()
except ImportError:
    _HAS_ML = False

console = Console()


def _normalize_file_relevance(distance: float) -> float:
    """Convert ChromaDB distance to 0-1 relevance score."""
    return max(0.0, 1.0 - (distance / 2.0))


def _resolve_mode(mode: str | None, out: Console, *, quiet: bool = False) -> str:
    """Resolve effective search mode based on request and ML availability."""
    if mode == "semantic" and not _HAS_ML:
        if not quiet:
            out.print("Semantic search requires additional dependencies.")
            out.print("  Install with:  pip install footprinter-cli\\[semantic]")
        sys.exit(1)

    if mode == "hybrid" and not _HAS_ML:
        if not quiet:
            out.print(
                "[dim]Semantic search not available — using keyword search. "
                "Run: pip install footprinter-cli\\[semantic] for AI-powered results.[/dim]"
            )
        return "keyword"

    if mode is not None:
        return mode

    # Auto-detect
    if _HAS_ML:
        return "hybrid"

    if not quiet:
        out.print(
            "[dim]Semantic search not available — using keyword search. "
            "Run: pip install footprinter-cli\\[semantic] for AI-powered results.[/dim]"
        )
    return "keyword"


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


def _keyword_search(
    query: str,
    limit: int = 10,
    type_filter: str | None = None,
    db_path: str | None = None,
) -> list[dict]:
    """Run FTS5 keyword search across files and (optionally) chats."""
    from footprinter.db.search import search_files
    from footprinter.semantic.hybrid_search import fts5_fallback_search

    if db_path is None:
        from footprinter.paths import get_db_path

        db_path = str(get_db_path())

    merged = []

    # File FTS5 search
    with open_db(db_path) as conn:
        file_data = search_files(conn, query, limit=limit, file_ext=type_filter)
        for r in file_data["results"]:
            merged.append(_fts_file_to_result(r))

    # Chat FTS5 search (skip if type filter limits to files)
    if not type_filter:
        chat_results, _ = fts5_fallback_search(
            query,
            n_results=limit,
            db_path=db_path,
        )
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


def _semantic_search(
    query: str,
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    """Run vector-only search (original behavior)."""
    store = VectorStore.get_instance()

    filter_meta = None
    if type_filter:
        filter_meta = {"file_type": type_filter}

    file_results = store.search_files(query, n_results=limit, filter_metadata=filter_meta)
    if type_filter:
        chat_results = []
    else:
        chat_results = store.search_chats(query, n_results=limit)

    merged = []
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


def _hybrid_search(
    query: str,
    limit: int = 10,
    type_filter: str | None = None,
    db_path: str | None = None,
) -> list[dict]:
    """Run hybrid search: FTS5 + vectors merged via RRF for chats, dedup for files."""
    from footprinter.semantic.hybrid_search import (
        chat_snippet,
        reciprocal_rank_fusion,
    )
    from footprinter.semantic.hybrid_search import (
        keyword_search as chat_keyword_search,
    )

    if db_path is None:
        from footprinter.paths import get_db_path

        db_path = str(get_db_path())

    # --- File merging: dedup by normalized path, boost overlaps ---
    keyword_file_results = []
    with open_db(db_path) as conn:
        from footprinter.db.search import search_files

        file_data = search_files(conn, query, limit=limit, file_ext=type_filter)
        for r in file_data["results"]:
            keyword_file_results.append(_fts_file_to_result(r))

    semantic_results = _semantic_search(query, limit=limit, type_filter=type_filter)
    semantic_file_results = [r for r in semantic_results if r["source_type"] == "file"]
    semantic_chat_results = [r for r in semantic_results if r["source_type"] == "chat"]

    # Merge files by normalized path
    seen_files = {}
    for item in semantic_file_results:
        key = _normalize_path(item["data"].get("file_path", ""))
        seen_files[key] = item

    for item in keyword_file_results:
        key = _normalize_path(item["data"].get("file_path", ""))
        if key in seen_files:
            seen_files[key]["relevance"] = min(1.0, seen_files[key]["relevance"] + 0.15)
        else:
            seen_files[key] = item

    merged = list(seen_files.values())

    # --- Chat merging: use RRF when both sources have results ---
    if not type_filter:
        raw_keyword_chats = chat_keyword_search(query, db_path=db_path, limit=limit)

        if semantic_chat_results and raw_keyword_chats:
            # Convert semantic chat results to the shape RRF expects
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
                        "relevance": r.get("fts_score", 0.5),
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


def execute_search(
    query: str,
    limit: int = 10,
    type_filter: str | None = None,
    mode: str | None = None,
    output: Console | None = None,
    db_path: str | None = None,
    json_output: bool = False,
) -> None:
    """Run search and display results.

    Shared implementation used by the ``fp search`` subcommand.
    """
    out = output or console
    effective_mode = _resolve_mode(mode, out, quiet=json_output)

    # Dispatch by mode
    try:
        if effective_mode == "keyword":
            merged = _keyword_search(query, limit=limit, type_filter=type_filter, db_path=db_path)
        elif effective_mode == "semantic":
            merged = _semantic_search(query, limit=limit, type_filter=type_filter)
        else:
            merged = _hybrid_search(query, limit=limit, type_filter=type_filter, db_path=db_path)
    except Exception as exc:
        if not json_output:
            out.print(f"[red]Search failed:[/red] {exc}")
        else:
            output_json({"query": query, "mode": effective_mode, "error": str(exc), "results": []})
        sys.exit(1)

    if json_output:
        output_json(
            {
                "query": query,
                "mode": effective_mode,
                "results": merged,
            }
        )
        return

    out.print(f"\nSearching for: '{query}' ({effective_mode} mode)")
    out.print("=" * 80)

    if not merged:
        out.print("No results found.")
        return

    # Display results
    for i, item in enumerate(merged, 1):
        if item["source_type"] == "file":
            r = item["data"]
            file_path = r.get("file_path", r.get("name", ""))
            chunk_info = ""
            if r.get("total_chunks", 1) > 1:
                chunk_info = f" (chunk {r['chunk_index'] + 1}/{r['total_chunks']})"

            out.print(f"\n{i}. [File] {file_path}{chunk_info}")
            out.print("-" * 80)
            out.print(r.get("content_snippet", ""))
            out.print()
        else:
            r = item["data"]
            title = r.get("chat_title", "(untitled)")
            source = r.get("source", "")
            source_label = f" ({source})" if source else ""

            out.print(f"\n{i}. [Chat] {title}{source_label}")
            out.print("-" * 80)
            out.print(r.get("snippet", ""))
            out.print()

    out.print("=" * 80)
    out.print(f"Showing {len(merged)} results")


def main():
    """CLI for search."""
    from footprinter.cli._common import add_json_flag

    parser = argparse.ArgumentParser(
        prog="fp search",
        description="Search across your files and chats",
    )
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument(
        "--mode",
        choices=["keyword", "semantic", "hybrid"],
        default=None,
        help="Search mode: keyword (FTS5), semantic (vectors), hybrid (both).",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Max results to return (default: 10)",
    )
    parser.add_argument("--type", help="Filter by file type (e.g., .pdf, .md). Excludes chat results.")
    add_json_flag(parser)

    args = parser.parse_args()
    execute_search(
        query=" ".join(args.query),
        limit=args.limit,
        type_filter=args.type,
        mode=args.mode,
        json_output=getattr(args, "json", False),
    )


if __name__ == "__main__":
    main()
