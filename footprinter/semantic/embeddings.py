"""Shared embedding function using ChromaDB's built-in ONNX backend."""

try:
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    _SEMANTIC_AVAILABLE = True
except ImportError:
    _SEMANTIC_AVAILABLE = False

EMBEDDING_DIM = 384


def get_embedding_function():
    """Return ChromaDB's built-in ONNX embedding function (ONNXMiniLM_L6_V2).

    Returns a callable: ef(texts: list[str]) -> list[list[float]]

    Raises:
        ImportError: If chromadb is not installed.
    """
    if not _SEMANTIC_AVAILABLE:
        raise ImportError("chromadb is required for embeddings. Install with: pip install footprinter-cli[full]")
    return ONNXMiniLM_L6_V2()
