"""Tests for FullContentExtractor size gates."""

import logging


class TestVectorizeSizeGate:
    """The vectorize size cap protects against oversized files regardless of
    indexing.max_file_size_mb.
    """

    def test_vectorize_size_gate_skips_oversized_txt(self, tmp_path, caplog):
        """A .txt over the cap returns no chunks and logs at INFO with path + size."""
        from footprinter.ingest.full_content_extractor import FullContentExtractor

        extractor = FullContentExtractor(max_vectorize_size_bytes=1024)

        big = tmp_path / "big.txt"
        big.write_bytes(b"x" * 4096)

        with caplog.at_level(logging.INFO, logger="footprinter.ingest.full_content_extractor"):
            chunks = extractor.extract_with_chunking(big)

        assert chunks == []
        size_str = str(big.stat().st_size)
        path_str = big.name
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any(path_str in r.getMessage() and size_str in r.getMessage() for r in info_records), (
            f"Expected INFO log mentioning {path_str} and {size_str}, got: "
            f"{[r.getMessage() for r in info_records]}"
        )

    def test_vectorize_size_gate_allows_under_threshold(self, tmp_path):
        """A file under the cap is chunked normally."""
        from footprinter.ingest.full_content_extractor import FullContentExtractor

        extractor = FullContentExtractor(max_vectorize_size_bytes=4096)

        small = tmp_path / "small.txt"
        small.write_text("hello world")

        chunks = extractor.extract_with_chunking(small)
        assert len(chunks) >= 1
        assert chunks[0]["content"] == "hello world"

    def test_vectorize_cap_applies_even_when_indexing_limit_zero(self, tmp_path, caplog):
        """indexing.max_file_size_mb=0 (no limit) must still honor the vectorize cap."""
        from footprinter.ingest.full_content_extractor import FullContentExtractor

        extractor = FullContentExtractor(
            max_file_size_bytes=0,
            max_vectorize_size_bytes=1024,
        )

        big = tmp_path / "big.txt"
        big.write_bytes(b"y" * 2048)

        with caplog.at_level(logging.INFO, logger="footprinter.ingest.full_content_extractor"):
            chunks = extractor.extract_with_chunking(big)

        assert chunks == []


class TestFromConfigVectorizeSize:
    """from_config reads vectorization.max_vectorize_size_mb with a 100MB default."""

    def test_from_config_reads_max_vectorize_size_mb(self):
        from footprinter.ingest.full_content_extractor import FullContentExtractor

        extractor = FullContentExtractor.from_config(
            {"vectorization": {"max_vectorize_size_mb": 50}}
        )
        assert extractor.max_vectorize_size_bytes == 50 * 1024 * 1024

    def test_from_config_default_is_100mb_when_unset(self):
        from footprinter.ingest.full_content_extractor import FullContentExtractor

        extractor = FullContentExtractor.from_config({})
        assert extractor.max_vectorize_size_bytes == 100 * 1024 * 1024
