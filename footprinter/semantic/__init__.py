"""Semantic search module for Footprinter."""

try:
    from .embeddings import get_embedding_function
except ImportError:
    pass

try:
    from .vector_store import VectorStore
except ImportError:
    pass

__all__ = ["VectorStore", "get_embedding_function"]
