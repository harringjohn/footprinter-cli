"""Unified VectorStore: single entry point for all ChromaDB operations."""

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

from footprinter.paths import get_chroma_path


def _semantic_available() -> bool:
    """Check whether chromadb and onnxruntime are importable.

    Evaluated lazily (on first call, not at import time) so that test
    stubs injected into sys.modules are picked up regardless of pytest
    collection order.
    """
    try:
        import chromadb  # noqa: F401
        import onnxruntime  # noqa: F401

        return True
    except ImportError:
        return False


logger = logging.getLogger(__name__)


# File vectorization at ingest time. Off by default — opt-in via config.
def _file_vectorization_enabled() -> bool:
    try:
        from footprinter.source_registry import get_config

        return bool(get_config().get("semantic", {}).get("file_vectorization", False))
    except Exception as e:
        # Intentional broad catch: ConfigError, ImportError, AttributeError all realistic
        logger.debug("Config unavailable for file_vectorization check: %s", e)
        return False


# Chat vectorization at ingest time. Off by default — opt-in via config.
def _chat_vectorization_enabled() -> bool:
    try:
        from footprinter.source_registry import get_config

        return bool(get_config().get("semantic", {}).get("chat_vectorization", False))
    except Exception as e:
        # Intentional broad catch: ConfigError, ImportError, AttributeError all realistic
        logger.debug("Config unavailable for chat_vectorization check: %s", e)
        return False


class VectorStore:
    """Unified vector store managing both files and chats collections."""

    _instance: Optional["VectorStore"] = None
    _lock = threading.Lock()
    _REBUILD_STAMP = ".rebuild_stamp"

    def __init__(self, chroma_path: Optional[str] = None):
        if not _semantic_available():
            raise ImportError(
                "Semantic search libraries required.\n"
                "Install with: pip install footprinter-cli[semantic]"
            )
        self.chroma_path = Path(chroma_path) if chroma_path else get_chroma_path()
        self.chroma_path.mkdir(parents=True, exist_ok=True)

        import chromadb
        from chromadb.config import Settings

        self.client = chromadb.PersistentClient(
            path=str(self.chroma_path),
            settings=Settings(anonymized_telemetry=False),
        )

        self._files = self.client.get_or_create_collection(
            name="footprinter_files",
            metadata={"description": "Footprinter file contents"},
        )
        self._chats = self.client.get_or_create_collection(
            name="footprinter_chats",
            metadata={"description": "Footprinter chats"},
        )

        files_count = self._files.count()
        chats_count = self._chats.count()
        if files_count == 0 and chats_count == 0 and (_file_vectorization_enabled() or _chat_vectorization_enabled()):
            logger.warning(
                "VectorStore initialized with 0 documents in both collections "
                "(chroma_path=%s). Searches will return empty results.",
                self.chroma_path,
            )

        from footprinter.semantic.embeddings import EMBEDDING_DIM, get_embedding_function

        self.ef = get_embedding_function()
        self._embedding_dim = EMBEDDING_DIM

        stamp_path = self.chroma_path / self._REBUILD_STAMP
        try:
            self._rebuild_id: Optional[str] = stamp_path.read_text().strip() if stamp_path.exists() else None
        except OSError:
            self._rebuild_id = None

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, chroma_path: Optional[str] = None) -> "VectorStore":
        """Return a shared singleton instance (thread-safe).

        If the rebuild stamp has changed (another process ran --rebuild-vectors
        full) or the chroma directory has been deleted, the stale singleton is
        discarded and a fresh instance is created.
        """
        with cls._lock:
            if cls._instance is not None:
                stamp_path = cls._instance.chroma_path / cls._REBUILD_STAMP
                try:
                    disk_stamp = stamp_path.read_text().strip() if stamp_path.exists() else None
                except OSError:
                    disk_stamp = None
                if disk_stamp != cls._instance._rebuild_id:
                    logger.warning(
                        "Rebuild stamp changed (%s → %s) — resetting stale VectorStore singleton",
                        cls._instance._rebuild_id,
                        disk_stamp,
                    )
                    cls._instance = None

            if cls._instance is not None and not cls._instance.chroma_path.exists():
                logger.warning(
                    "Chroma path %s no longer exists — resetting stale VectorStore singleton",
                    cls._instance.chroma_path,
                )
                cls._instance = None
            if cls._instance is None:
                cls._instance = cls(chroma_path=chroma_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Clear the singleton so the next get_instance() creates a fresh one."""
        with cls._lock:
            cls._instance = None

    def check_integrity(self) -> Dict:
        """Check chroma collection integrity.

        Returns:
            {"status": "ok", "files": N, "chats": M} on success,
            {"status": "corrupted", "error": "..."} on FTS5 corruption,
            {"status": "empty", "files": 0, "chats": 0} when both empty.
        """
        try:
            files_count = self._files.count()
            chats_count = self._chats.count()
        except Exception as e:
            error_msg = str(e).lower()
            if "malformed" in error_msg or "corrupt" in error_msg:
                return {"status": "corrupted", "error": str(e)}
            raise

        if files_count == 0 and chats_count == 0:
            return {"status": "empty", "files": 0, "chats": 0}

        # Probe collections with a dummy query to verify FTS5 index
        try:
            dummy = [0.0] * self._embedding_dim
            if files_count > 0:
                self._files.query(query_embeddings=[dummy], n_results=1)
            if chats_count > 0:
                self._chats.query(query_embeddings=[dummy], n_results=1)
        except Exception as e:
            error_msg = str(e).lower()
            if any(kw in error_msg for kw in ("malformed", "corrupt", "fts5")):
                return {"status": "corrupted", "error": str(e)}
            raise

        return {"status": "ok", "files": files_count, "chats": chats_count}

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def index_file(
        self,
        file_id: int,
        file_path: str,
        chunks: List[Dict],
        metadata: Optional[Dict] = None,
    ) -> int:
        """
        Index file chunks into the files collection.

        Args:
            file_id: Database ID of the file.
            file_path: Path to the file.
            chunks: List of chunk dicts with 'content', 'chunk_index', 'total_chunks'.
            metadata: Additional metadata to store with each chunk.

        Returns:
            Number of chunks indexed.
        """
        if not chunks:
            return 0

        ids = []
        contents = []
        metas = []
        for chunk in chunks:
            ids.append(f"file_{file_id}_chunk_{chunk['chunk_index']}")
            contents.append(chunk["content"])
            meta = dict(metadata) if metadata else {}
            meta.update(
                {
                    "file_id": file_id,
                    "file_path": file_path,
                    "chunk_index": chunk["chunk_index"],
                    "total_chunks": chunk["total_chunks"],
                    "content_length": len(chunk["content"]),
                }
            )
            metas.append(meta)

        embeddings = self.ef(contents)
        self._files.add(
            ids=ids,
            embeddings=embeddings,
            documents=contents,
            metadatas=metas,
        )

        return len(chunks)

    def upsert_file(self, file_id, file_path, chunks, metadata=None):
        """Index file chunks, replacing any existing vectors for this file."""
        self.delete_file(file_id)
        return self.index_file(file_id, file_path, chunks, metadata)

    def search_files(
        self,
        query: str,
        n_results: int = 10,
        filter_metadata: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Semantic search across indexed file content.

        Returns:
            List of dicts with file_id, file_path, chunk_index, total_chunks,
            content_snippet, distance.
        """
        query_embedding = self.ef([query])[0]
        results = self._files.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=filter_metadata,
        )

        formatted = []
        if results["ids"] and len(results["ids"]) > 0:
            for i in range(len(results["ids"][0])):
                formatted.append(
                    {
                        "file_id": results["metadatas"][0][i].get("file_id"),
                        "file_path": results["metadatas"][0][i]["file_path"],
                        "chunk_index": results["metadatas"][0][i]["chunk_index"],
                        "total_chunks": results["metadatas"][0][i]["total_chunks"],
                        "content_snippet": results["documents"][0][i][:500],
                        "distance": results["distances"][0][i] if "distances" in results else None,
                    }
                )
        return formatted

    def delete_file(self, file_id: int) -> None:
        """Delete all chunks for a given file_id."""
        self._files.delete(where={"file_id": file_id})

    def get_file_stats(self) -> Dict:
        """Return count and collection name for files."""
        return {
            "total_chunks": self._files.count(),
            "collection_name": self._files.name,
            "model": self._embedding_dim,
        }

    # ------------------------------------------------------------------
    # Chat operations
    # ------------------------------------------------------------------

    def index_chat_message(
        self,
        message_id: int,
        chat_id: int,
        content: str,
        metadata: Dict,
    ) -> int:
        """
        Index a single chat message, auto-chunking if needed.

        Returns:
            Number of chunks indexed.
        """
        if not content or not content.strip():
            return 0

        from footprinter.semantic.chunking import chunk_content

        chunks = chunk_content(content)

        for chunk_text, chunk_index, total_chunks in chunks:
            doc_id = f"msg_{message_id}_chunk_{chunk_index}"
            embedding = self.ef([chunk_text])[0]

            meta = {
                "message_id": message_id,
                "chat_id": chat_id,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "content_length": len(chunk_text),
                **metadata,
            }

            self._chats.add(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[chunk_text],
                metadatas=[meta],
            )

        return len(chunks)

    def upsert_chat_message(self, message_id, chat_id, content, metadata):
        """Index a message, replacing any existing vectors."""
        try:
            self._chats.delete(where={"message_id": message_id})
        except Exception:  # Intentional broad catch: ChromaDB operations are best-effort cleanup
            logger.warning(
                "Failed to delete existing vectors for message_id=%s before re-index",
                message_id,
                exc_info=True,
            )
        return self.index_chat_message(message_id, chat_id, content, metadata)

    def index_chat_info(
        self,
        chat_id: int,
        title: str,
        summary: Optional[str],
        source: str,
        created_at: str,
        message_count: int,
    ) -> bool:
        """
        Index chat title+summary as a searchable document.

        Uses upsert so it can be re-indexed safely.
        """
        text_parts = [f"Chat: {title or '(untitled)'}"]
        if summary:
            text_parts.append(f"Summary: {summary}")
        text_parts.append(f"Source: {source}")

        searchable_text = "\n\n".join(text_parts)
        doc_id = f"chat_info_{chat_id}"
        embedding = self.ef([searchable_text])[0]

        metadata = {
            "chat_id": chat_id,
            "chat_title": (title or "(untitled)")[:200],
            "source": source or "unknown",
            "created_at": created_at or "",
            "message_count": message_count or 0,
            "chunk_type": "chat_info",
            "has_summary": bool(summary),
        }

        self._chats.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[searchable_text],
            metadatas=[metadata],
        )
        return True

    def search_chats(
        self,
        query: str,
        n_results: int = 20,
        source: Optional[str] = None,
        role: Optional[str] = None,
        min_score: float = 0.3,
    ) -> List[Dict]:
        """
        Hybrid search combining semantic + FTS5 keyword search via RRF.

        Returns:
            List of result dicts with chat_id, chat_title,
            relevance_score, snippet, etc.
        """
        if not query or len(query) < 3:
            return []

        from footprinter.paths import get_db_path
        from footprinter.semantic.hybrid_search import (
            extract_snippet,
            keyword_search,
            reciprocal_rank_fusion,
        )

        # 1. Semantic search via ChromaDB
        query_embedding = self.ef([query])[0]

        where_filter = None
        if source or role:
            conditions = []
            if source:
                conditions.append({"source": source})
            if role:
                conditions.append({"role": role})
            where_filter = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        semantic_raw = self._chats.query(
            query_embeddings=[query_embedding],
            n_results=n_results * 3,
            where=where_filter,
        )

        semantic_results = []
        seen_chats = {}

        if semantic_raw["ids"] and len(semantic_raw["ids"]) > 0:
            for i in range(len(semantic_raw["ids"][0])):
                meta = semantic_raw["metadatas"][0][i]
                content = semantic_raw["documents"][0][i]
                distance = semantic_raw["distances"][0][i] if "distances" in semantic_raw else 0

                relevance = max(0, 1 - (distance / 2))
                chat_id = meta.get("chat_id")

                if chat_id in seen_chats:
                    if relevance <= seen_chats[chat_id]:
                        continue
                seen_chats[chat_id] = relevance

                chunk_type = meta.get("chunk_type", "message")
                snippet = extract_snippet(content, query)

                semantic_results.append(
                    {
                        "chat_id": chat_id,
                        "chat_title": meta.get("chat_title", "(untitled)"),
                        "message_id": meta.get("message_id"),
                        "role": meta.get(
                            "role",
                            "info" if chunk_type == "chat_info" else "unknown",
                        ),
                        "source": meta.get("source", "unknown"),
                        "created_at": meta.get("created_at", ""),
                        "snippet": snippet,
                        "relevance_score": round(relevance, 3),
                        "chunk_type": chunk_type,
                        "chunk_index": meta.get("chunk_index", 0),
                        "total_chunks": meta.get("total_chunks", 1),
                    }
                )

        # 2. Keyword search via FTS5
        keyword_results = keyword_search(query, db_path=str(get_db_path()), account=source, limit=n_results * 2)

        # 3. Combine with RRF
        combined = reciprocal_rank_fusion(semantic_results, keyword_results)

        filtered = [r for r in combined if r["relevance_score"] >= min_score]
        return filtered[:n_results]

    def delete_message(self, message_id: int) -> None:
        """Delete all chunks for a given message_id."""
        self._chats.delete(where={"message_id": message_id})

    def delete_chat(self, chat_id: int) -> None:
        """Delete all message chunks and info doc for a chat."""
        self._chats.delete(where={"chat_id": chat_id})

    def get_chat_stats(self) -> Dict:
        """Return count and collection name for chats."""
        return {
            "total_documents": self._chats.count(),
            "collection_name": self._chats.name,
            "embedding_dimensions": self._embedding_dim,
        }
