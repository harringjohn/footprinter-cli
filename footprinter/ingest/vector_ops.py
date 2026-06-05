"""CLI utilities — rebuild_vectors with phase isolation and interrupt safety."""

import logging
import signal
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from footprinter.paths import get_db_path

logger = logging.getLogger(__name__)


def _load_batch_size() -> int:
    try:
        from footprinter.source_registry import get_config

        return get_config().get("limits", {}).get("vector_batch_size", 100)
    except Exception:
        logger.debug("Config unavailable for vector_batch_size, using default 100")
        return 100


_BATCH_SIZE = _load_batch_size()

# Graceful shutdown flag — set by SIGINT/SIGTERM handler
_shutdown = False


def _handle_shutdown(signum, frame):
    """Signal handler that requests graceful shutdown."""
    global _shutdown
    _shutdown = True
    sig_name = signal.Signals(signum).name
    logger.warning("Received %s — finishing current batch...", sig_name)


def repair_fts(quiet: bool = False):
    """Drop and rebuild all FTS search indexes from base table data."""
    from rich.console import Console

    from footprinter.ingest.database import Database

    console = Console() if not quiet else None

    db = Database(str(get_db_path()))

    if console:
        before = db.check_fts_health()
        console.print()
        console.print("[bold]FTS Repair[/bold]")
        console.print()
        for table, info in before.items():
            status = info["status"]
            icon = {"ok": "[green]ok[/green]", "error": "[red]error[/red]"}.get(status, status)
            console.print(f"  Before: {table}  {icon}")

    result = db.repair_fts()
    db.close()

    if console:
        console.print()
        for table, counts in result.items():
            console.print(f"  After:  {table}  [green]{counts['after']}[/green] rows")
        console.print()
        console.print("[bold green]FTS repair complete[/bold green]")
        console.print()


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------


def _preflight_check(conn, cursor, files_enabled, chats_enabled, console, mode: str = "full") -> dict:
    """Run pre-flight validation before rebuild.

    Returns dict with counts: {"files": N, "messages": M, "chats": K}.
    Raises RuntimeError if validation fails.
    """
    # Test DB writability
    try:
        cursor.execute("CREATE TEMP TABLE IF NOT EXISTS _fp_preflight (x INTEGER)")
        cursor.execute("INSERT INTO _fp_preflight VALUES (1)")
        cursor.execute("DELETE FROM _fp_preflight")
    except sqlite3.OperationalError as e:
        raise RuntimeError(f"Database is not writable: {e}") from e

    # Count items to process — in incremental/sync mode, count only
    # items that actually need processing (new/modified/removed).
    counts = {"files": 0, "messages": 0, "chats": 0}
    incremental = mode in ("incremental", "sync")
    if files_enabled:
        if incremental:
            cursor.execute(
                "SELECT COUNT(*) FROM files "
                "WHERE source = 'local' AND status = 'listed' AND path IS NOT NULL"
                " AND vectorize != 0"
                " AND (vectorized_at IS NULL OR modified_at > vectorized_at)"
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM files "
                "WHERE source = 'local' AND status = 'listed' AND path IS NOT NULL"
                " AND vectorize != 0"
            )
        counts["files"] = cursor.fetchone()[0]
    if chats_enabled:
        if incremental:
            cursor.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE content IS NOT NULL AND TRIM(content) != ''"
                " AND status = 'listed'"
                " AND vectorize != 0"
                " AND vectorized_at IS NULL"
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE content IS NOT NULL AND TRIM(content) != ''"
                " AND status = 'listed'"
                " AND vectorize != 0"
            )
        counts["messages"] = cursor.fetchone()[0]
        if incremental:
            cursor.execute(
                "SELECT COUNT(*) FROM chats "
                "WHERE status = 'listed'"
                " AND vectorize != 0"
                " AND metadata_vectorized_at IS NULL"
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM chats "
                "WHERE status = 'listed'"
                " AND vectorize != 0"
            )
        counts["chats"] = cursor.fetchone()[0]

    if console:
        parts = []
        if files_enabled:
            parts.append(f"{counts['files']} files")
        if chats_enabled:
            parts.append(f"{counts['messages']} messages")
            parts.append(f"{counts['chats']} chats")
        label = "Will process" if incremental else "Will vectorize"
        console.print(f"  {label}: {', '.join(parts)}")

    return counts


# ---------------------------------------------------------------------------
# Phase functions
# ---------------------------------------------------------------------------


def _cleanup_removed_vectors(conn, cursor, store, *, clean_files=True, clean_messages=True, clean_chats=True) -> dict:
    """Remove vectors for files, messages, and chats with status='removed'.

    Returns {"removed": N, "removed_messages": M, "removed_chats": C}.
    """
    removed_count = 0
    msg_count = 0
    chat_count = 0

    # --- Files ---
    if clean_files:
        cursor.execute("SELECT id FROM files WHERE status = 'removed' AND vectorized_at IS NOT NULL")
        for f in cursor.fetchall():
            try:
                store.delete_file(f["id"])
                cursor.execute(
                    "UPDATE files SET vectorized_at = NULL, vectorized_chunks = NULL WHERE id = ?",
                    (f["id"],),
                )
                removed_count += 1
            except Exception as e:
                logger.warning("Failed to remove vectors for file %s: %s", f["id"], e)

    # --- Messages ---
    if clean_messages:
        cursor.execute("SELECT id FROM messages WHERE status = 'removed' AND vectorized_at IS NOT NULL")
        for m in cursor.fetchall():
            try:
                store.delete_message(m["id"])
                cursor.execute(
                    "UPDATE messages SET vectorized_at = NULL, vectorized_chunks = NULL WHERE id = ?",
                    (m["id"],),
                )
                msg_count += 1
            except Exception as e:
                logger.warning("Failed to remove vectors for message %s: %s", m["id"], e)

    # --- Chats ---
    if clean_chats:
        cursor.execute("SELECT id FROM chats WHERE status = 'removed' AND metadata_vectorized_at IS NOT NULL")
        for c in cursor.fetchall():
            try:
                store.delete_chat(c["id"])
                cursor.execute(
                    "UPDATE chats SET metadata_vectorized_at = NULL WHERE id = ?",
                    (c["id"],),
                )
                # delete_chat also removes message chunks for this chat —
                # clear their vectorization state to keep DB in sync
                cursor.execute(
                    "UPDATE messages SET vectorized_at = NULL,"
                    " vectorized_chunks = NULL"
                    " WHERE chat_id = ? AND vectorized_at IS NOT NULL",
                    (c["id"],),
                )
                chat_count += 1
            except Exception as e:
                logger.warning("Failed to remove vectors for chat %s: %s", c["id"], e)

    conn.commit()
    return {"removed": removed_count, "removed_messages": msg_count, "removed_chats": chat_count}


def _vectorize_files(conn, cursor, store, extractor, vec_config, console, mode: str = "full") -> dict:
    """Vectorize local files.

    Returns {"done": N, "chunks": M, "interrupted": bool}.
    """
    global _shutdown

    file_types = vec_config.get("file_types")
    exclude_patterns = vec_config.get("exclude_patterns", [])
    where_parts = [
        "source = 'local'",
        "status = 'listed'",
        "path IS NOT NULL",
        "vectorize != 0",
    ]
    params: list = []

    # Incremental/sync: only process new or modified files
    if mode in ("incremental", "sync"):
        where_parts.append("(vectorized_at IS NULL OR modified_at > vectorized_at)")

    if file_types:
        like_clauses = " OR ".join("LOWER(path) LIKE ?" for _ in file_types)
        where_parts.append(f"({like_clauses})")
        params.extend(f"%{ext.lower()}" for ext in file_types)
    for pat in exclude_patterns:
        sql_pat = pat.replace("**", "%").replace("*", "%")
        where_parts.append("path NOT LIKE ?")
        params.append(sql_pat)

    cursor.execute(
        f"SELECT id, path as file_path FROM files WHERE {' AND '.join(where_parts)} ORDER BY id",
        params,
    )
    files = cursor.fetchall()
    total = len(files)
    done = 0
    chunks = 0

    # Use upsert in incremental/sync mode (handles both new and modified)
    use_upsert = mode in ("incremental", "sync")

    for f in files:
        if _shutdown:
            conn.commit()
            if console:
                console.print(f"  [yellow]Interrupted[/yellow] at {done}/{total} files")
            return {"done": done, "chunks": chunks, "interrupted": True}

        try:
            fpath = Path(f["file_path"])
            if not fpath.exists():
                continue
            file_chunks = extractor.extract_with_chunking(fpath)
            if file_chunks:
                metadata = {"file_type": fpath.suffix.lower(), "file_name": fpath.name}
                try:
                    if use_upsert:
                        store.upsert_file(f["id"], f["file_path"], file_chunks, metadata)
                    else:
                        store.index_file(f["id"], f["file_path"], file_chunks, metadata)
                except Exception as e:
                    logger.warning("Chroma write failed for file %s: %s", f["file_path"], e)
                    continue
                # SQLite update only after chroma success
                cursor.execute(
                    "UPDATE files SET vectorized_at = CURRENT_TIMESTAMP, vectorized_chunks = ? WHERE id = ?",
                    (len(file_chunks), f["id"]),
                )
                done += 1
                chunks += len(file_chunks)
                if done % 100 == 0:
                    conn.commit()
                    if console:
                        console.print(f"  Vectorizing files: {done}/{total}")
        except Exception as e:
            logger.debug("Skipped file %s: %s", f["file_path"], e)

    conn.commit()
    return {"done": done, "chunks": chunks, "interrupted": False}


def _vectorize_messages(conn, cursor, store, console, mode: str = "full") -> dict:
    """Vectorize chat messages.

    Returns {"done": N, "interrupted": bool}.
    """
    global _shutdown

    from footprinter.semantic.chunking import chunk_content

    incremental_filter = ""
    use_upsert = mode in ("incremental", "sync")
    if use_upsert:
        incremental_filter = "AND message.vectorized_at IS NULL"

    cursor.execute(
        f"""
        SELECT message.id, message.chat_id, message.role, message.content,
               message.created_at, chat.title, chat.account as source
        FROM messages message
        JOIN chats chat ON message.chat_id = chat.id
        WHERE message.content IS NOT NULL AND message.content != ''
            AND message.status = 'listed'
            AND message.vectorize != 0
            {incremental_filter}
        ORDER BY message.id
        """
    )
    messages = cursor.fetchall()
    total = len(messages)
    done = 0

    batch_ids: list = []
    batch_texts: list = []
    batch_metas: list = []
    batch_msg_ids: list = []
    batch_msg_chunks: dict = {}  # msg_id -> chunk count

    def flush_batch() -> int:
        """Flush current batch to chroma then SQLite. Returns count flushed."""
        if not batch_ids:
            return 0
        count = len(batch_msg_ids)
        try:
            embeddings = store.ef(batch_texts)
            chroma_op = store._chats.upsert if use_upsert else store._chats.add
            chroma_op(
                ids=list(batch_ids),
                embeddings=embeddings,
                documents=list(batch_texts),
                metadatas=list(batch_metas),
            )
        except Exception as e:
            logger.warning("Chroma write failed for message batch: %s", e)
            batch_ids.clear()
            batch_texts.clear()
            batch_metas.clear()
            batch_msg_ids.clear()
            batch_msg_chunks.clear()
            return 0
        # SQLite update only after chroma success
        for mid in batch_msg_ids:
            cursor.execute(
                "UPDATE messages SET vectorized_at = CURRENT_TIMESTAMP, vectorized_chunks = ? WHERE id = ?",
                (batch_msg_chunks.get(mid, 0), mid),
            )
        batch_ids.clear()
        batch_texts.clear()
        batch_metas.clear()
        batch_msg_ids.clear()
        batch_msg_chunks.clear()
        return count

    for msg in messages:
        if _shutdown:
            done += flush_batch()
            conn.commit()
            if console:
                console.print(f"  [yellow]Interrupted[/yellow] at {done}/{total} messages")
            return {"done": done, "interrupted": True}

        try:
            content = msg["content"]
            if not content or not content.strip():
                continue
            msg_chunks = chunk_content(content)
            for chunk_text, chunk_index, total_chunks in msg_chunks:
                batch_ids.append(f"msg_{msg['id']}_chunk_{chunk_index}")
                batch_texts.append(chunk_text)
                batch_metas.append(
                    {
                        "message_id": msg["id"],
                        "chat_id": msg["chat_id"],
                        "chunk_index": chunk_index,
                        "total_chunks": total_chunks,
                        "content_length": len(chunk_text),
                        "source": msg["source"] or "unknown",
                        "role": msg["role"] or "unknown",
                        "chat_title": (msg["title"] or "(untitled)")[:200],
                        "created_at": msg["created_at"] or "",
                        "message_position": 0,
                    }
                )
            batch_msg_chunks[msg["id"]] = len(msg_chunks)
            batch_msg_ids.append(msg["id"])
            if len(batch_ids) >= _BATCH_SIZE:
                done += flush_batch()
                conn.commit()
                if console:
                    console.print(f"  Vectorizing messages: {done}/{total}")
        except Exception as e:
            logger.debug("Skipped message %s: %s", msg["id"], e)

    done += flush_batch()
    conn.commit()
    return {"done": done, "interrupted": False}


def _vectorize_chat_info(conn, cursor, store, console, mode: str = "full") -> dict:
    """Vectorize chat info records.

    Returns {"done": N, "interrupted": bool}.
    """
    global _shutdown

    incremental_filter = ""
    if mode in ("incremental", "sync"):
        incremental_filter = "AND metadata_vectorized_at IS NULL"

    cursor.execute(
        f"""
        SELECT id, title, account as source, created_at, message_count
        FROM chats
        WHERE status = 'listed'
            AND vectorize != 0
            {incremental_filter}
        ORDER BY id
        """
    )
    chats = cursor.fetchall()
    total = len(chats)
    done = 0

    batch_ids: list = []
    batch_texts: list = []
    batch_metas: list = []
    batch_conv_ids: list = []

    def flush_batch() -> int:
        """Flush current batch to chroma then SQLite. Returns count flushed."""
        if not batch_ids:
            return 0
        count = len(batch_conv_ids)
        try:
            embeddings = store.ef(batch_texts)
            store._chats.upsert(
                ids=list(batch_ids),
                embeddings=embeddings,
                documents=list(batch_texts),
                metadatas=list(batch_metas),
            )
        except Exception as e:
            logger.warning("Chroma write failed for chat info batch: %s", e)
            batch_ids.clear()
            batch_texts.clear()
            batch_metas.clear()
            batch_conv_ids.clear()
            return 0
        # SQLite update only after chroma success
        for cid in batch_conv_ids:
            cursor.execute(
                "UPDATE chats SET metadata_vectorized_at = CURRENT_TIMESTAMP WHERE id = ?",
                (cid,),
            )
        batch_ids.clear()
        batch_texts.clear()
        batch_metas.clear()
        batch_conv_ids.clear()
        return count

    for conv in chats:
        if _shutdown:
            done += flush_batch()
            conn.commit()
            if console:
                console.print(f"  [yellow]Interrupted[/yellow] at {done}/{total} chats")
            return {"done": done, "interrupted": True}

        try:
            text_parts = [f"Chat: {conv['title'] or '(untitled)'}"]
            text_parts.append(f"Source: {conv['source']}")
            searchable_text = "\n\n".join(text_parts)

            batch_ids.append(f"chat_info_{conv['id']}")
            batch_texts.append(searchable_text)
            batch_metas.append(
                {
                    "chat_id": conv["id"],
                    "chat_title": (conv["title"] or "(untitled)")[:200],
                    "source": conv["source"] or "unknown",
                    "created_at": conv["created_at"] or "",
                    "message_count": conv["message_count"] or 0,
                    "chunk_type": "chat_info",
                }
            )
            batch_conv_ids.append(conv["id"])
            if len(batch_ids) >= _BATCH_SIZE:
                done += flush_batch()
                conn.commit()
                if console:
                    console.print(f"  Vectorizing chat info: {done}/{total}")
        except Exception as e:
            logger.debug("Skipped chat %s: %s", conv["id"], e)

    done += flush_batch()
    conn.commit()
    return {"done": done, "interrupted": False}


# ---------------------------------------------------------------------------
# Sync verification
# ---------------------------------------------------------------------------


def _sync_verify(cursor, store, files_enabled, chats_enabled, console) -> None:
    """Compare DB vectorization counts against chroma document counts."""
    if console:
        console.print()
        console.print("[bold]Sync verification[/bold]")

    if files_enabled:
        cursor.execute(
            "SELECT COALESCE(SUM(vectorized_chunks), 0) FROM files"
            " WHERE vectorized_at IS NOT NULL AND status = 'listed'"
        )
        db_file_chunks = cursor.fetchone()[0]
        chroma_file_chunks = store.get_file_stats().get("total_chunks", 0)

        if db_file_chunks == chroma_file_chunks:
            if console:
                console.print(
                    f"  [green]\u2713[/green] Files: DB={db_file_chunks} chunks, chroma={chroma_file_chunks} chunks"
                )
        else:
            if console:
                console.print(
                    f"  [yellow]\u26a0[/yellow] Files: DB={db_file_chunks}"
                    f" chunks, chroma={chroma_file_chunks} chunks"
                    f" (discrepancy: {abs(db_file_chunks - chroma_file_chunks)})"
                )

    if chats_enabled:
        # Chroma stores message chunks + chat_info docs in one collection.
        # DB side: SUM(vectorized_chunks) for messages (chunk count),
        # COUNT(*) for chat_info (each chat = exactly 1 chroma doc).
        cursor.execute(
            "SELECT COALESCE(SUM(vectorized_chunks), 0) FROM messages"
            " WHERE status = 'listed' AND vectorized_at IS NOT NULL"
        )
        db_msg_chunks = cursor.fetchone()[0]

        # Detect stale chunk counts: messages vectorized before the
        # vectorized_chunks column was added will have chunks=0.
        cursor.execute(
            "SELECT COUNT(*) FROM messages"
            " WHERE status = 'listed' AND vectorized_at IS NOT NULL AND vectorized_chunks = 0"
        )
        stale_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chats WHERE status = 'listed' AND metadata_vectorized_at IS NOT NULL")
        db_chat_info_count = cursor.fetchone()[0]
        chroma_chat_docs = store.get_chat_stats().get("total_documents", 0)

        if console:
            if stale_count > 0:
                console.print(
                    f"  [yellow]\u26a0[/yellow] Chats: {stale_count} messages"
                    " missing chunk counts — re-run"
                    " [bold]fp doctor semantic[/bold] to populate"
                )
            else:
                db_total = db_msg_chunks + db_chat_info_count
                if db_total == chroma_chat_docs:
                    console.print(
                        f"  [green]\u2713[/green] Chats:"
                        f" DB={db_msg_chunks} message chunks"
                        f" + {db_chat_info_count} chat info,"
                        f" chroma={chroma_chat_docs} documents"
                    )
                else:
                    console.print(
                        f"  [yellow]\u26a0[/yellow] Chats:"
                        f" DB={db_msg_chunks} message chunks"
                        f" + {db_chat_info_count} chat info,"
                        f" chroma={chroma_chat_docs} documents"
                        f" (discrepancy:"
                        f" {abs(db_total - chroma_chat_docs)})"
                    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def rebuild_vectors(
    quiet: bool = False,
    source: str = "all",
    phase: Optional[str] = None,
    mode: str = "incremental",
):
    """Rebuild the vector store from database contents.

    Args:
        quiet: Suppress Rich output.
        source: Which vectors to rebuild — "files", "chats", or "all".
        phase: Run a single phase — "files", "messages", or "chat_info".
               Overrides source. When set, chroma is not deleted.
        mode: Rebuild mode — "incremental" (default, new/modified/removed only),
              "sync" (incremental + count verification), or "full" (delete all, rebuild).
    """
    global _shutdown
    _shutdown = False

    import shutil

    from rich.console import Console

    from footprinter.paths import get_chroma_path
    from footprinter.source_registry import get_config

    from ..semantic.vector_store import (
        VectorStore,
        _chat_vectorization_enabled,
        _file_vectorization_enabled,
    )
    from .full_content_extractor import FullContentExtractor

    config = get_config()
    console = Console() if not quiet else None

    # Determine which phases are enabled based on phase/source and flags
    do_files = source in ("files", "all")
    do_chats = source in ("chats", "all")

    if phase == "files":
        files_enabled = _file_vectorization_enabled()
        chats_enabled = False
    elif phase in ("messages", "chat_info"):
        files_enabled = False
        chats_enabled = _chat_vectorization_enabled()
    else:
        files_enabled = do_files and _file_vectorization_enabled()
        chats_enabled = do_chats and _chat_vectorization_enabled()

    # Guard: refuse when vectorization is disabled for requested phases
    if not files_enabled and not chats_enabled:
        if console:
            console.print()
            console.print(
                "[bold yellow]Vectorization is not enabled[/bold yellow] — "
                "run [bold]fp setup[/bold] or add a [bold]semantic:[/bold] "
                "section to config."
            )
            console.print()
        return

    # Install signal handlers for graceful shutdown
    old_sigint = signal.getsignal(signal.SIGINT)
    old_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    conn = None
    try:
        # Open DB connection — before any destructive action
        conn = sqlite3.connect(str(get_db_path()), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.cursor()

        # Pre-flight validation
        try:
            _preflight_check(conn, cursor, files_enabled, chats_enabled, console, mode=mode)
        except RuntimeError as e:
            if console:
                console.print(f"\n[bold red]Pre-flight failed:[/bold red] {e}")
            return

        # Chroma handling: full mode (no phase) deletes everything;
        # incremental/sync and single-phase preserve existing chroma.
        if mode == "full" and phase is None:
            # Full rebuild — delete chroma and start fresh
            VectorStore.reset_instance()
            chroma_path = get_chroma_path()
            if chroma_path.exists():
                shutil.rmtree(chroma_path)
                if console:
                    console.print(f"[dim]Deleted {chroma_path}[/dim]")
            store = VectorStore.get_instance()
            (chroma_path / VectorStore._REBUILD_STAMP).write_text(uuid.uuid4().hex)
        else:
            # Incremental/sync/single-phase — check existing chroma integrity
            try:
                store = VectorStore.get_instance()
                integrity = store.check_integrity()
                if integrity["status"] == "corrupted":
                    if console:
                        console.print()
                        console.print(f"[bold red]Chroma is corrupted:[/bold red] {integrity['error']}")
                        console.print("Run [bold]fp doctor semantic full[/bold] to rebuild from scratch.")
                    return
            except ImportError:
                raise

        # Build extractor (vec_config also used by _vectorize_files for SQL pre-filtering)
        vec_config = config.get("vectorization", {})
        extractor = FullContentExtractor.from_config(config)

        if console:
            console.print()
            console.print(f"[bold]Rebuilding vectors[/bold]  [dim]({mode})[/dim]")
            console.print()

        # Determine which phases to run
        run_files = files_enabled and (phase is None or phase == "files")
        run_messages = chats_enabled and (phase is None or phase == "messages")
        run_chat_info = chats_enabled and (phase is None or phase == "chat_info")

        results = {}

        # Cleanup removed vectors (incremental/sync only)
        if mode in ("incremental", "sync") and not _shutdown:
            results["cleanup"] = _cleanup_removed_vectors(
                conn,
                cursor,
                store,
                clean_files=run_files,
                clean_messages=run_messages,
                clean_chats=run_chat_info,
            )

        if run_files and not _shutdown:
            results["files"] = _vectorize_files(
                conn,
                cursor,
                store,
                extractor,
                vec_config,
                console,
                mode=mode,
            )

        if run_messages and not _shutdown:
            results["messages"] = _vectorize_messages(
                conn,
                cursor,
                store,
                console,
                mode=mode,
            )

        if run_chat_info and not _shutdown:
            results["chat_info"] = _vectorize_chat_info(
                conn,
                cursor,
                store,
                console,
                mode=mode,
            )

        # Sync verification — compare DB and chroma counts
        if mode == "sync" and not _shutdown:
            _sync_verify(cursor, store, files_enabled, chats_enabled, console)

        # Summary
        if console:
            console.print()
            interrupted = any(r.get("interrupted") for r in results.values())
            label = (
                "[bold yellow]Rebuild interrupted[/bold yellow]"
                if interrupted
                else "[bold green]Rebuild complete[/bold green]"
            )
            console.print(label)

            cleanup = results.get("cleanup", {})

            if mode in ("incremental", "sync"):
                # Show categorized counts
                files_r = results.get("files", {})
                if run_files:
                    new_count = files_r.get("done", 0)
                    removed_count = cleanup.get("removed", 0)
                    chunks_count = files_r.get("chunks", 0)
                    console.print(f"  Files: {new_count} new/modified ({chunks_count} chunks), {removed_count} removed")
                elif do_files:
                    console.print("  Files: skipped (--source chats)")
            else:
                if "files" in results:
                    r = results["files"]
                    console.print(f"  Files: {r['done']} vectorized ({r['chunks']} chunks)")
                elif run_files:
                    console.print("  Files: skipped (disabled)")
                elif do_files:
                    console.print("  Files: skipped (--source chats)")

            if "messages" in results:
                msg_removed = cleanup.get("removed_messages", 0)
                msg_line = f"  Messages: {results['messages']['done']} vectorized"
                if msg_removed:
                    msg_line += f", {msg_removed} removed"
                console.print(msg_line)
            elif run_messages:
                console.print("  Messages: skipped (disabled)")

            if "chat_info" in results:
                chat_removed = cleanup.get("removed_chats", 0)
                chat_line = f"  Chats: {results['chat_info']['done']} info indexed"
                if chat_removed:
                    chat_line += f", {chat_removed} removed"
                console.print(chat_line)
            elif run_chat_info:
                console.print("  Chats: skipped (disabled)")
            elif do_chats:
                console.print("  Chats: skipped (--source files)")

            console.print()
    finally:
        if conn is not None:
            conn.close()
        # Restore original signal handlers
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
