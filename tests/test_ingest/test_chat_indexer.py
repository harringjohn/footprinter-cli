"""Tests for chat indexer vectorization gating."""

from unittest.mock import MagicMock, patch


def test_vectorization_skipped_when_chat_vectorization_disabled():
    """Chat vectorization should be skipped when semantic.chat_vectorization is False."""
    from footprinter.ingest.chat_indexer import ChatIndexer

    mock_db = MagicMock()
    indexer = ChatIndexer(mock_db)

    # Force a vector store to be available
    mock_store = MagicMock()
    indexer._vector_store = mock_store

    msg = {"content": "Hello world", "role": "user", "created_at": "2026-01-01"}
    conv_data = {"source": "claude", "title": "Test Chat", "message_count": 1}

    with patch("footprinter.ingest.chat_indexer._chat_vectorization_enabled", return_value=False):
        indexer._vectorize_message(1, 1, msg, conv_data)
        indexer._vectorize_chat_info(1, conv_data)

    # Neither method should have called the vector store
    mock_store.upsert_chat_message.assert_not_called()
    mock_store.index_chat_info.assert_not_called()
