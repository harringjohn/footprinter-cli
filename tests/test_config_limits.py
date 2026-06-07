"""Override and fallback tests for config-driven limit accessors.

Each accessor under ``footprinter/`` reads a config key with a hardcoded default.
Most read from ``limits.<key>``; ``chunk_size`` reads from
``vectorization.chunk_size`` (co-located with ``chunk_overlap``), and
``vectorize_statuses`` reads from ``semantic.vectorize_statuses``.  These tests
verify:

1. A custom config value is respected (override).
2. A ``ConfigError`` falls back to the hardcoded default (fallback).
3. MCP / vector / chunk accessors return fresh values on every call (freshness).
"""

from unittest.mock import patch

from footprinter.source_registry import ConfigError

# ---------------------------------------------------------------------------
# api_max_limit  (default 200)
# ---------------------------------------------------------------------------

class TestApiMaxLimit:

    def test_override(self):
        from footprinter.api import _get_api_max_limit

        config = {"limits": {"api_max_limit": 500}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_api_max_limit() == 500

    def test_fallback(self):
        from footprinter.api import _get_api_max_limit

        with patch(
            "footprinter.source_registry.get_config",
            side_effect=ConfigError("no config"),
        ):
            assert _get_api_max_limit() == 200


# ---------------------------------------------------------------------------
# mcp_search_limit_cap  (default 200)
# ---------------------------------------------------------------------------

class TestMcpSearchLimitCap:

    def test_override(self):
        from footprinter.mcp.tools.search import _get_mcp_search_limit_cap

        config = {"limits": {"mcp_search_limit_cap": 500}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_mcp_search_limit_cap() == 500

    def test_fallback(self):
        from footprinter.mcp.tools.search import _get_mcp_search_limit_cap

        with patch(
            "footprinter.source_registry.get_config",
            side_effect=ConfigError("no config"),
        ):
            assert _get_mcp_search_limit_cap() == 200

    def test_reads_fresh(self):
        from footprinter.mcp.tools.search import _get_mcp_search_limit_cap

        with patch(
            "footprinter.source_registry.get_config",
            return_value={"limits": {"mcp_search_limit_cap": 300}},
        ):
            assert _get_mcp_search_limit_cap() == 300
        with patch(
            "footprinter.source_registry.get_config",
            return_value={"limits": {"mcp_search_limit_cap": 400}},
        ):
            assert _get_mcp_search_limit_cap() == 400


# ---------------------------------------------------------------------------
# vector_batch_size  (default 100)
# ---------------------------------------------------------------------------

class TestVectorBatchSize:

    def test_override(self):
        from footprinter.ingest.vector_ops import _get_batch_size

        config = {"limits": {"vector_batch_size": 250}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_batch_size() == 250

    def test_fallback(self):
        from footprinter.ingest.vector_ops import _get_batch_size

        with patch(
            "footprinter.source_registry.get_config",
            side_effect=ConfigError("no config"),
        ):
            assert _get_batch_size() == 100

    def test_reads_fresh(self):
        from footprinter.ingest.vector_ops import _get_batch_size

        with patch(
            "footprinter.source_registry.get_config",
            return_value={"limits": {"vector_batch_size": 50}},
        ):
            assert _get_batch_size() == 50
        with patch(
            "footprinter.source_registry.get_config",
            return_value={"limits": {"vector_batch_size": 75}},
        ):
            assert _get_batch_size() == 75


# ---------------------------------------------------------------------------
# chunk_size  (default 1000)
# ---------------------------------------------------------------------------

class TestChunkSize:

    def test_override(self):
        from footprinter.semantic.chunking import _get_chunk_size

        config = {"vectorization": {"chunk_size": 2000}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_chunk_size() == 2000

    def test_fallback(self):
        from footprinter.semantic.chunking import _get_chunk_size

        with patch(
            "footprinter.source_registry.get_config",
            side_effect=ConfigError("no config"),
        ):
            assert _get_chunk_size() == 1000

    def test_reads_fresh(self):
        from footprinter.semantic.chunking import _get_chunk_size

        with patch(
            "footprinter.source_registry.get_config",
            return_value={"vectorization": {"chunk_size": 500}},
        ):
            assert _get_chunk_size() == 500
        with patch(
            "footprinter.source_registry.get_config",
            return_value={"vectorization": {"chunk_size": 800}},
        ):
            assert _get_chunk_size() == 800


# ---------------------------------------------------------------------------
# vectorize_statuses  (default ["listed"])
# ---------------------------------------------------------------------------

class TestVectorizeStatuses:

    def test_override(self):
        from footprinter.ingest.processing import _get_vectorize_statuses

        config = {"semantic": {"vectorize_statuses": ["listed", "unlisted"]}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_vectorize_statuses() == ["listed", "unlisted"]

    def test_fallback(self):
        from footprinter.ingest.processing import _get_vectorize_statuses

        with patch(
            "footprinter.source_registry.get_config",
            side_effect=ConfigError("no config"),
        ):
            assert _get_vectorize_statuses() == ["listed"]

    def test_empty_list_falls_back(self):
        from footprinter.ingest.processing import _get_vectorize_statuses

        config = {"semantic": {"vectorize_statuses": []}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_vectorize_statuses() == ["listed"]

    def test_non_list_falls_back(self):
        from footprinter.ingest.processing import _get_vectorize_statuses

        config = {"semantic": {"vectorize_statuses": "listed"}}
        with patch("footprinter.source_registry.get_config", return_value=config):
            assert _get_vectorize_statuses() == ["listed"]
