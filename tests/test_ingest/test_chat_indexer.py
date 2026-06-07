"""Tests for ChatIndexer — inline vectorization removed.

Chat vectorization is now handled by the post-ingest follow-up stage
(run_vectorization), not inline during import. These tests verify
that the inline methods no longer exist.
"""


def test_messages_not_vectorized_inline():
    """ChatIndexer should not have a _vectorize_message method."""
    from footprinter.ingest.chat_indexer import ChatIndexer

    assert not hasattr(ChatIndexer, "_vectorize_message")


def test_chat_info_not_vectorized_inline():
    """ChatIndexer should not have a _vectorize_chat_info method."""
    from footprinter.ingest.chat_indexer import ChatIndexer

    assert not hasattr(ChatIndexer, "_vectorize_chat_info")
