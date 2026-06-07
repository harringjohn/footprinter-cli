"""
Chat indexer for importing and querying AI chat exports.

Usage:
    python -m footprinter.ingest.chat_indexer upload ~/Downloads/claude-export.zip
    python -m footprinter.ingest.chat_indexer stats
    python -m footprinter.ingest.chat_indexer history
"""

import argparse
import json
import logging
import sys
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Dict, Optional, Tuple

from rich.console import Console
from rich.progress import track

from footprinter.db import chats as chats_db
from footprinter.db import uploads as uploads_db

from ..utils.hash_utils import compute_sha256
from ..utils.time import utc_now_iso
from .chat_parsers import ChatGPTParser, ClaudeParser
from .database import Database

logger = logging.getLogger(__name__)

# Default security limits for zip processing (used when config is absent)
_DEFAULT_MAX_DECOMPRESSED_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
_DEFAULT_MAX_ZIP_ENTRIES = 10_000
_DEFAULT_MAX_COMPRESSION_RATIO = 100  # 100:1

# Public module-level exports (backward compatibility)
MAX_DECOMPRESSED_SIZE = _DEFAULT_MAX_DECOMPRESSED_SIZE
MAX_ZIP_ENTRIES = _DEFAULT_MAX_ZIP_ENTRIES
MAX_COMPRESSION_RATIO = _DEFAULT_MAX_COMPRESSION_RATIO


def _get_zip_limits() -> tuple[int, int, int]:
    """Load zip security limits from config, falling back to defaults."""
    try:
        from footprinter.source_registry import get_config

        zip_cfg = get_config().get("limits", {}).get("zip", {})
    except Exception:
        logger.debug("Config unavailable for zip limits, using defaults")
        zip_cfg = {}
    default_mb = _DEFAULT_MAX_DECOMPRESSED_SIZE // (1024 * 1024)
    max_size = zip_cfg.get("max_decompressed_size_mb", default_mb) * 1024 * 1024
    max_entries = zip_cfg.get("max_entries", _DEFAULT_MAX_ZIP_ENTRIES)
    max_ratio = zip_cfg.get("max_compression_ratio", _DEFAULT_MAX_COMPRESSION_RATIO)
    return max_size, max_entries, max_ratio


class ChatIndexer:
    """Manager for importing and querying chat history."""

    def __init__(self, db: Database):
        self.db = db
        self._vector_store = None  # lazy

    def _get_vector_store(self):
        if self._vector_store is None:
            try:
                from footprinter.semantic.vector_store import VectorStore

                self._vector_store = VectorStore.get_instance()
            except (ImportError, Exception):
                self._vector_store = False  # sentinel: don't retry
        return self._vector_store if self._vector_store is not False else None

    def _validate_zip(self, zf: zipfile.ZipFile, extract_dir: Path) -> None:
        """Validate zip contents for path traversal, size, and compression ratio."""
        max_size, max_entries, max_ratio = _get_zip_limits()
        entries = zf.infolist()

        if len(entries) > max_entries:
            raise ValueError(f"Zip contains {len(entries)} entries, exceeds limit of {max_entries}")

        extract_root = extract_dir.resolve()
        total_decompressed = 0
        total_compressed = 0

        for info in entries:
            # Reject absolute paths
            if info.filename.startswith("/"):
                raise ValueError(f"Zip contains absolute path: {info.filename}")

            # Reject path traversal
            target = (extract_dir / info.filename).resolve()
            if not str(target).startswith(str(extract_root)):
                raise ValueError(f"Zip contains path traversal: {info.filename}")

            total_decompressed += info.file_size
            total_compressed += info.compress_size

        if total_decompressed > max_size:
            raise ValueError(
                f"Zip decompressed size {total_decompressed} bytes exceeds limit of {max_size} bytes"
            )

        if total_compressed > 0:
            ratio = total_decompressed / total_compressed
            if ratio > max_ratio:
                raise ValueError(f"Zip compression ratio {ratio:.1f}:1 exceeds limit of {max_ratio}:1")

    def upload(self, file_path: Path, console: Optional[Console] = None) -> Dict:
        """
        Upload and import a chat export (zip or directory).

        Accepts a .zip file or extracted directory. Zip files are hashed
        to prevent duplicate imports. Chats and messages are
        deduplicated on import.

        Args:
            file_path: Path to .zip file or extracted directory
            console: Optional rich Console for progress UI. None = silent.

        Returns:
            Dict with upload statistics
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        is_zip = file_path.suffix.lower() == ".zip"

        if is_zip:
            hash_ctx = console.status("Computing hash…") if console else nullcontext()
            with hash_ctx:
                file_hash = compute_sha256(str(file_path))
            if not file_hash:
                raise ValueError(f"Could not compute hash for {file_path}")

            existing = uploads_db.get_upload_by_hash(self.db.conn, file_hash)
            if existing:
                logger.info(f"File already uploaded on {existing['uploaded_at']}")
                return {
                    "status": "duplicate",
                    "upload_id": existing["id"],
                    "previous_upload": existing,
                }

            file_size = file_path.stat().st_size

            with tempfile.TemporaryDirectory() as tmpdir:
                extract_dir = Path(tmpdir) / "extract"
                with zipfile.ZipFile(file_path, "r") as zf:
                    self._validate_zip(zf, extract_dir)
                    zf.extractall(extract_dir)

                source, extract_dir = self._detect_source(extract_dir)

                upload_id = uploads_db.create_upload(
                    self.db.conn,
                    {
                        "filename": file_path.name,
                        "file_hash": file_hash,
                        "file_size": file_size,
                        "type": "chat",
                        "source": source,
                        "status": "processing",
                    },
                )

                try:
                    result = self._import_with_dedup(extract_dir, source, console=console)
                    uploads_db.update_upload(
                        self.db.conn,
                        upload_id,
                        status="completed",
                        completed_at=utc_now_iso(),
                        items_added=result["chats_added"],
                        items_updated=result["chats_updated"],
                        items_total=result["chats_added"] + result["chats_updated"],
                    )
                    result["upload_id"] = upload_id
                    result["status"] = "completed"
                    return result
                except Exception as e:
                    uploads_db.update_upload(
                        self.db.conn,
                        upload_id,
                        status="failed",
                        error_message=str(e),
                        completed_at=utc_now_iso(),
                    )
                    raise
        else:
            # Directory import (alternative to single-file)
            source, file_path = self._detect_source(file_path)
            return self._import_with_dedup(file_path, source, console=console)

    def _detect_source(self, extract_dir: Path) -> Tuple[str, Path]:
        """Detect chat export source from directory contents.

        Returns:
            Tuple of (source, resolved_dir) where resolved_dir contains conversations.json
        """
        conv_file = extract_dir / "conversations.json"
        if not conv_file.exists():
            # Search one level deep for subdirectory-wrapped exports
            for child in extract_dir.iterdir():
                if child.is_dir() and (child / "conversations.json").exists():
                    conv_file = child / "conversations.json"
                    extract_dir = child
                    break
            else:
                raise ValueError("conversations.json not found in export")

        with open(conv_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list) or len(data) == 0:
            raise ValueError("conversations.json is empty or invalid")

        first = data[0]
        if "uuid" in first and "chat_messages" in first:
            return ("claude", extract_dir)
        if "mapping" in first:
            return ("chatgpt", extract_dir)

        raise ValueError("Unknown export format")

    def _import_with_dedup(
        self, export_dir: Path, source: str, console: Optional[Console] = None
    ) -> Dict:
        """Import chats with message deduplication."""
        if source == "claude":
            parser = ClaudeParser(export_dir)
        elif source == "chatgpt":
            conv_file = export_dir / "conversations.json"
            parser = ChatGPTParser(conv_file)
        else:
            raise ValueError(f"Unknown source: {source}")

        stats = parser.get_stats()
        logger.info(f"{source.capitalize()} export contains:")
        logger.info(f"  {stats['total_chats']} chats")
        logger.info(f"  {stats['chats_with_messages']} with messages")
        logger.info(f"  {stats['total_messages']} total messages")
        logger.info(f"  Date range: {stats['earliest_chat']} to {stats['latest_chat']}")

        chats_added = 0
        chats_updated = 0
        messages_imported = 0
        errors = 0

        chat_iter = parser.parse_chats()
        if console is not None:
            chat_iter = track(
                chat_iter,
                total=stats["total_chats"],
                description="Importing chats",
                console=console,
            )

        for conv_data in chat_iter:
            try:
                # Check if chat already exists
                existing_id = chats_db.get_chat_id_by_uuid(self.db.conn, conv_data["external_id"])

                if existing_id:
                    # Delete old messages and vectors before re-import
                    chats_db.delete_chat_messages(self.db.conn, existing_id)
                    store = self._get_vector_store()
                    if store:
                        try:
                            store.delete_chat(existing_id)
                        except Exception:
                            pass

                # Insert/replace chat
                chats_db.insert_chat(
                    self.db.conn,
                    {
                        "external_id": conv_data["external_id"],
                        "account": conv_data["source"],  # Map source → account
                        "title": conv_data["title"],
                        "created_at": conv_data["created_at"],
                        "updated_at": conv_data["updated_at"],
                        "message_count": conv_data["message_count"],
                        "metadata": conv_data.get("metadata", {}),
                    },
                )

                # Count only after successful insert
                if existing_id:
                    chats_updated += 1
                else:
                    chats_added += 1

                internal_id = chats_db.get_chat_id_by_uuid(self.db.conn, conv_data["external_id"])

                for msg in conv_data["messages"]:
                    msg_id = chats_db.insert_message(
                        self.db.conn,
                        {
                            "chat_id": internal_id,
                            "message_id": msg["message_id"],
                            "role": msg["role"],
                            "content": msg["content"],
                            "created_at": msg["created_at"],
                            "metadata": msg.get("metadata", {}),
                        },
                    )
                    messages_imported += 1

                # Commit per-chat: all messages for this chat
                self.db.conn.commit()

                if console is None:
                    total = chats_added + chats_updated
                    if total % 100 == 0:
                        logger.info(f"Imported {total} chats...")

            except Exception as e:
                logger.error(f"Error importing chat {conv_data.get('external_id')}: {e}")
                errors += 1

        return {
            "chats_added": chats_added,
            "chats_updated": chats_updated,
            "messages_imported": messages_imported,
            "errors": errors,
        }

    def get_stats(self) -> Dict:
        """Get chat history statistics."""
        cursor = self.db.conn.cursor()

        # Chat stats
        cursor.execute("SELECT COUNT(*) as count FROM chats")
        chat_count = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM messages")
        msg_count = cursor.fetchone()["count"]

        # By account
        cursor.execute("""
            SELECT account, COUNT(*) as count
            FROM chats
            GROUP BY account
        """)
        by_account = {row["account"]: row["count"] for row in cursor.fetchall()}

        # Date range
        cursor.execute("""
            SELECT MIN(created_at) as earliest, MAX(created_at) as latest
            FROM chats
        """)
        dates = cursor.fetchone()

        # Top chats by message count
        cursor.execute("""
            SELECT title, message_count, created_at
            FROM chats
            ORDER BY message_count DESC
            LIMIT 10
        """)
        top_chats = [dict(row) for row in cursor.fetchall()]

        return {
            "total_chats": chat_count,
            "total_messages": msg_count,
            "by_account": by_account,
            "earliest_chat": dates["earliest"],
            "latest_chat": dates["latest"],
            "top_chats": top_chats,
        }


def main():
    """CLI entry point for importing and managing AI chat history."""
    parser = argparse.ArgumentParser(description="Import and manage AI chat history")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # upload command
    upload_parser = subparsers.add_parser("upload", help="Upload and import chat export (zip or directory)")
    upload_parser.add_argument("file_path", type=Path, help="Path to chat export zip file or directory")

    # stats command
    subparsers.add_parser("stats", help="Show chat history statistics")

    # history command
    history_parser = subparsers.add_parser("history", help="Show recent upload history")
    history_parser.add_argument("--limit", type=int, default=10, help="Number of uploads to show (default: 10)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize database and manager
    from footprinter.paths import get_db_path

    db = Database(str(get_db_path()))
    manager = ChatIndexer(db)

    try:
        if args.command == "upload":
            logger.info(f"Uploading chat export from {args.file_path}")
            result = manager.upload(args.file_path)

            if result.get("status") == "duplicate":
                prev = result["previous_upload"]
                logger.warning("This file was already uploaded.")
                logger.info(f"  Uploaded: {prev['uploaded_at']}")
                logger.info(f"  Items: {prev['items_added']} added, {prev['items_updated']} updated")
            else:
                logger.info("=" * 60)
                logger.info("Upload complete!")
                logger.info(f"  Upload ID: {result['upload_id']}")
                logger.info(f"  Chats: {result['chats_added']} added, {result['chats_updated']} updated")
                logger.info(f"  Messages: {result['messages_imported']}")
                if result["errors"]:
                    logger.warning(f"  Errors: {result['errors']}")

        elif args.command == "stats":
            stats = manager.get_stats()
            logger.info("=" * 60)
            logger.info("CHAT HISTORY STATISTICS")
            logger.info("=" * 60)
            logger.info(f"Total chats: {stats['total_chats']}")
            logger.info(f"Total messages: {stats['total_messages']}")
            logger.info("By account:")
            for account, count in stats.get("by_account", {}).items():
                logger.info(f"  {account}: {count}")
            logger.info(f"Date range: {stats['earliest_chat']} to {stats['latest_chat']}")
            logger.info("Top chats by message count:")
            for conv in stats.get("top_chats", []):
                title = conv["title"][:50] + "..." if len(conv["title"] or "") > 50 else conv["title"]
                logger.info(f"  {conv['message_count']:4d} msgs: {title}")

        elif args.command == "history":
            uploads = uploads_db.get_recent_uploads(db.conn, upload_type="chat", limit=args.limit)
            logger.info("=" * 60)
            logger.info("CHAT UPLOAD HISTORY")
            logger.info("=" * 60)
            if not uploads:
                logger.info("No uploads found.")
            for upload in uploads:
                logger.info(f"{upload['uploaded_at']} - {upload['filename']}")
                logger.info(f"  Source: {upload['source']}  Status: {upload['status']}")
                logger.info(f"  Added: {upload['items_added']}  Updated: {upload['items_updated']}")
                if upload.get("error_message"):
                    logger.info(f"  Error: {upload['error_message']}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
