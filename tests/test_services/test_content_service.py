"""Tests for content_service — file I/O (disk, Drive, text extraction)."""

import inspect
from unittest.mock import MagicMock, patch

from footprinter.services import content_service

# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_read_file_local_success(self):
        """Local file read returns status ok with extracted content."""
        metadata = {
            "id": 1,
            "name": "readme.md",
            "source": "local",
            "path": "/Users/u/Work/readme.md",
            "mime_type": "text/markdown",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                return_value=b"# Hello",
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="markdown",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=("# Hello", None),
            ),
        ):
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "ok"
        assert result["content"] == "# Hello"

    def test_read_file_drive_success(self):
        """Drive file read returns status ok."""
        metadata = {
            "id": 2,
            "name": "doc.pdf",
            "source": "WorkDrive",
            "external_id": "abc123",
            "account": "work",
            "mime_type": "application/pdf",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = True

        with (
            patch(
                "footprinter.services.content_service._read_remote_file_bytes",
                return_value=b"pdf-bytes",
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="pdf",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=("Extracted PDF text", None),
            ),
        ):
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "ok"
        assert result["content"] == "Extracted PDF text"

    def test_read_file_missing_external_id(self):
        """Drive file without external_id returns read_failed."""
        metadata = {
            "id": 3,
            "name": "orphan.txt",
            "source": "WorkDrive",
            "external_id": None,
            "account": "work",
            "mime_type": "text/plain",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = True

        with patch(
            "footprinter.source_registry.SourceRegistry",
            return_value=mock_registry,
        ):
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "read_failed"
        assert "missing external_id" in result["message"]

    def test_read_file_unknown_source(self):
        """File with unknown source returns read_failed."""
        metadata = {
            "id": 4,
            "name": "mystery.txt",
            "source": "unknown_source",
            "mime_type": "text/plain",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        with patch(
            "footprinter.source_registry.SourceRegistry",
            return_value=mock_registry,
        ):
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "read_failed"
        assert "unknown source" in result["message"]

    def test_read_file_null_data(self):
        """Local file returning None bytes gives read_failed."""
        metadata = {
            "id": 5,
            "name": "gone.txt",
            "source": "local",
            "path": "/Users/u/Work/gone.txt",
            "mime_type": "text/plain",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                return_value=None,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
        ):
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "read_failed"
        assert "null data" in result["message"]

    def test_read_file_raw_format(self):
        """Raw format skips extraction."""
        metadata = {
            "id": 6,
            "name": "data.bin",
            "source": "local",
            "path": "/Users/u/Work/data.bin",
            "mime_type": "application/octet-stream",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                return_value=b"raw bytes here",
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="binary",
            ),
        ):
            result = content_service.read_file(conn, metadata, format="raw")

        assert result["status"] == "ok"
        assert result["content"] == "raw bytes here"
        assert result["metadata"]["extraction_method"] == "raw"


# ---------------------------------------------------------------------------
# Configurable read cap
# ---------------------------------------------------------------------------


def _local_pdf_metadata(size_bytes=None):
    meta = {
        "id": 99,
        "name": "big.pdf",
        "source": "local",
        "path": "/Users/u/Work/big.pdf",
        "mime_type": "application/pdf",
    }
    if size_bytes is not None:
        meta["size_bytes"] = size_bytes
    return meta


class TestConfigurableReadCap:
    def _run_capture_max_bytes(self, config_side_effect):
        """Invoke read_file with extraction succeeding and capture the
        max_bytes the local read was called with."""
        captured = {}

        def fake_read(path, max_bytes=500_000):
            captured["max_bytes"] = max_bytes
            return b"# Hello"

        metadata = {
            "id": 1,
            "name": "readme.md",
            "source": "local",
            "path": "/Users/u/Work/readme.md",
            "mime_type": "text/markdown",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        cfg_patch = patch("footprinter.source_registry.get_config")
        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                side_effect=fake_read,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="markdown",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=("# Hello", None),
            ),
            cfg_patch as mock_get_config,
        ):
            mock_get_config.side_effect = config_side_effect
            result = content_service.read_file(conn, metadata)

        return captured, result

    def test_cap_sourced_from_config(self):
        """Read cap is read from indexing.max_read_size_mb (MB -> bytes)."""
        captured, result = self._run_capture_max_bytes(
            lambda *a, **k: {"indexing": {"max_read_size_mb": 2}}
        )
        assert result["status"] == "ok"
        assert captured["max_bytes"] == 2 * 1024 * 1024

    def test_default_cap_when_config_unavailable(self):
        """If config lookup raises, fall back to the documented default cap."""
        from footprinter.services.content_service import _DEFAULT_MAX_READ_MB

        def boom(*a, **k):
            raise RuntimeError("config unavailable")

        captured, result = self._run_capture_max_bytes(boom)
        assert result["status"] == "ok"
        assert captured["max_bytes"] == _DEFAULT_MAX_READ_MB * 1024 * 1024

    def test_zero_means_no_read_cap(self):
        """max_read_size_mb: 0 means read the entire file (no cap)."""
        captured, result = self._run_capture_max_bytes(
            lambda *a, **k: {"indexing": {"max_read_size_mb": 0}}
        )
        assert result["status"] == "ok"
        assert captured["max_bytes"] == 0

    def test_small_file_unaffected(self):
        """Small file with successful extraction still returns ok content."""
        captured, result = self._run_capture_max_bytes(
            lambda *a, **k: {"indexing": {"max_read_size_mb": 10}}
        )
        assert result["status"] == "ok"
        assert result["content"] == "# Hello"

    def test_truncated_extraction_failure_not_ok(self):
        """A truncated large file whose extraction fails no longer reports ok.

        Simulate the read returning exactly max_bytes bytes (truncation signal)
        and extraction failing, as happens with large PDFs.
        """
        cap_mb = 1
        cap_bytes = cap_mb * 1024 * 1024
        metadata = _local_pdf_metadata(size_bytes=cap_bytes * 5)
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        def fake_read(path, max_bytes=500_000):
            # Return exactly the cap -> truncation signal
            return b"%PDF-1.7" + b"\x00" * (max_bytes - 8)

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                side_effect=fake_read,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="pdf",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=(None, "Cannot find Root object"),
            ),
            patch("footprinter.source_registry.get_config") as mock_get_config,
        ):
            mock_get_config.return_value = {"indexing": {"max_read_size_mb": cap_mb}}
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "extraction_failed"
        assert result["metadata"]["extraction_success"] is False
        assert result["metadata"].get("truncated") is True
        assert result["metadata"].get("extraction_incomplete") is True

    def test_complete_cap_sized_file_not_truncated(self):
        """A *complete* file exactly at the cap (size_bytes == len(data) == cap)
        whose extraction fails for an unrelated reason is NOT labeled truncated.

        The read returns exactly max_bytes, but the indexed size_bytes proves the
        whole file was read, so raising the read cap would not help. Result must
        fall back to raw decode (status ok, extraction_success False) rather than
        reporting extraction_failed / truncated."""
        cap_mb = 1
        cap_bytes = cap_mb * 1024 * 1024
        metadata = _local_pdf_metadata(size_bytes=cap_bytes)
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        def fake_read(path, max_bytes=500_000):
            # Return exactly the cap; the file is complete at this size.
            return b"%PDF-1.7" + b"\x00" * (max_bytes - 8)

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                side_effect=fake_read,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="pdf",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=(None, "Cannot find Root object"),
            ),
            patch("footprinter.source_registry.get_config") as mock_get_config,
        ):
            mock_get_config.return_value = {"indexing": {"max_read_size_mb": cap_mb}}
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "ok"
        assert result["metadata"]["extraction_success"] is False
        assert result["metadata"].get("truncated") is not True
        assert result["metadata"].get("extraction_incomplete") is not True

    def test_genuinely_truncated_cap_sized_file_still_truncated(self):
        """Regression guard: a genuinely larger file (size_bytes > cap) that reads
        exactly cap bytes and fails extraction is still labeled truncated."""
        cap_mb = 1
        cap_bytes = cap_mb * 1024 * 1024
        metadata = _local_pdf_metadata(size_bytes=cap_bytes * 5)
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        def fake_read(path, max_bytes=500_000):
            return b"%PDF-1.7" + b"\x00" * (max_bytes - 8)

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                side_effect=fake_read,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="pdf",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=(None, "Cannot find Root object"),
            ),
            patch("footprinter.source_registry.get_config") as mock_get_config,
        ):
            mock_get_config.return_value = {"indexing": {"max_read_size_mb": cap_mb}}
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "extraction_failed"
        assert result["metadata"]["extraction_success"] is False
        assert result["metadata"].get("truncated") is True
        assert result["metadata"].get("extraction_incomplete") is True

    def test_was_truncated_exact_cap_size_known_not_truncated(self):
        """Unit-level boundary table for _was_truncated with a known size_bytes."""
        cap = 1024
        # Complete, exactly at cap -> not truncated.
        assert content_service._was_truncated(b"x" * cap, cap, size_bytes=cap) is False
        # More bytes exist past the cap -> truncated.
        assert content_service._was_truncated(b"x" * cap, cap, size_bytes=cap + 1) is True
        # Stale smaller size; data already covers the whole file -> not truncated.
        assert content_service._was_truncated(b"x" * cap, cap, size_bytes=cap - 1) is False
        # No cap -> not truncated.
        assert content_service._was_truncated(b"x" * cap, 0, size_bytes=cap) is False
        # Under-len read but indexed size proves more bytes exist -> truncated.
        assert content_service._was_truncated(b"x" * (cap - 1), cap, size_bytes=cap * 5) is True

    def test_was_truncated_size_unknown_uses_probe(self):
        """Size-unknown exact-boundary case is resolved by an over-cap probe.

        With size_bytes absent and len(data) == cap, _was_truncated consults a
        one-byte over-cap probe (via _read_local_file_bytes with
        max_bytes == cap + 1) rather than defaulting to truncated."""
        cap = 1024
        data = b"x" * cap
        path = "/Users/u/Work/big.pdf"

        # Probe returns more than cap bytes -> something past the cap -> truncated.
        def probe_more(p, max_bytes=500_000):
            assert max_bytes == cap + 1
            return b"x" * (cap + 1)

        with patch(
            "footprinter.services.content_service._read_local_file_bytes",
            side_effect=probe_more,
        ):
            assert (
                content_service._was_truncated(
                    data, cap, size_bytes=None, path=path
                )
                is True
            )

        # Probe returns exactly cap bytes -> nothing past the cap -> not truncated.
        def probe_exact(p, max_bytes=500_000):
            assert max_bytes == cap + 1
            return b"x" * cap

        with patch(
            "footprinter.services.content_service._read_local_file_bytes",
            side_effect=probe_exact,
        ):
            assert (
                content_service._was_truncated(
                    data, cap, size_bytes=None, path=path
                )
                is False
            )

    def test_nontruncated_extraction_failure_keeps_fallback(self):
        """A small file whose extraction fails still falls back to raw decode
        (status ok, extraction_success False). The truncation guard must key off
        truncation, not extraction failure alone."""
        cap_mb = 1
        metadata = {
            "id": 100,
            "name": "small.pdf",
            "source": "local",
            "path": "/Users/u/Work/small.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 32,
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        def fake_read(path, max_bytes=500_000):
            # Far smaller than the cap -> not truncated, decodable
            return b"not a real pdf"

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                side_effect=fake_read,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="pdf",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=(None, "Cannot find Root object"),
            ),
            patch("footprinter.source_registry.get_config") as mock_get_config,
        ):
            mock_get_config.return_value = {"indexing": {"max_read_size_mb": cap_mb}}
            result = content_service.read_file(conn, metadata)

        assert result["status"] == "ok"
        assert result["metadata"]["extraction_success"] is False
        assert result["metadata"].get("truncated") is not True


class TestOutputPayloadBound:
    """The MCP result has a ~1 MB payload ceiling. A file that extracts
    successfully but larger than that ceiling must return a bounded, in-budget
    slice (marked output_truncated), never an oversized result that fails to
    return entirely."""

    def _run(self, *, extracted_text=None, raw_bytes=None, format="text"):
        """Invoke read_file for a local file. When ``extracted_text`` is given,
        the text extractor succeeds with it; when ``raw_bytes`` is given, the
        decode path returns it (raw mode or no extractor)."""
        metadata = {
            "id": 1,
            "name": "doc.md",
            "source": "local",
            "path": "/Users/u/Work/doc.md",
            "mime_type": "text/markdown",
        }
        conn = MagicMock()
        mock_registry = MagicMock()
        mock_registry.is_remote_source.return_value = False

        read_payload = raw_bytes if raw_bytes is not None else b"# placeholder"

        with (
            patch(
                "footprinter.services.content_service._read_local_file_bytes",
                return_value=read_payload,
            ),
            patch(
                "footprinter.source_registry.SourceRegistry",
                return_value=mock_registry,
            ),
            patch(
                "footprinter.utils.extraction.get_extractor_for_file",
                return_value="markdown",
            ),
            patch(
                "footprinter.utils.extraction.extract_text",
                return_value=(extracted_text, None),
            ),
            patch("footprinter.source_registry.get_config") as mock_get_config,
        ):
            mock_get_config.return_value = {"indexing": {"max_read_size_mb": 10}}
            result = content_service.read_file(conn, metadata, format=format)

        return result

    def test_oversized_extraction_is_bounded_and_marked(self):
        from footprinter.services.content_service import _MAX_OUTPUT_CHARS

        oversized = "A" * (_MAX_OUTPUT_CHARS + 100_000)
        result = self._run(extracted_text=oversized)

        assert result["status"] == "ok"
        assert len(result["content"]) <= _MAX_OUTPUT_CHARS
        assert result["content"] != oversized
        assert result["metadata"].get("output_truncated") is True
        # A pointer to search for large documents must be present somewhere.
        msg = (result.get("message") or "") + (
            result["metadata"].get("output_truncated_message") or ""
        )
        assert "search" in msg.lower()

    def test_in_budget_extraction_unmarked(self):
        result = self._run(extracted_text="# Hello")

        assert result["status"] == "ok"
        assert result["content"] == "# Hello"
        assert not result["metadata"].get("output_truncated")

    def test_raw_mode_output_also_bounded(self):
        from footprinter.services.content_service import _MAX_OUTPUT_CHARS

        oversized = b"B" * (_MAX_OUTPUT_CHARS + 100_000)
        result = self._run(raw_bytes=oversized, format="raw")

        assert result["status"] == "ok"
        assert len(result["content"]) <= _MAX_OUTPUT_CHARS
        assert result["metadata"].get("output_truncated") is True


# ---------------------------------------------------------------------------
# _decode_bytes
# ---------------------------------------------------------------------------


class TestDecodeBytes:
    def test_decode_bytes_utf8(self):
        """UTF-8 bytes decode correctly."""
        from footprinter.services.content_service import _decode_bytes

        assert _decode_bytes(b"hello world") == "hello world"

    def test_decode_bytes_latin1_fallback(self):
        """Bytes that fail UTF-8 fall back to Latin-1."""
        from footprinter.services.content_service import _decode_bytes

        # \xe9 is 'e' in Latin-1 but invalid as a standalone UTF-8 byte
        data = b"caf\xe9"
        result = _decode_bytes(data)
        assert result == "caf\xe9".encode("latin-1").decode("latin-1")


# ---------------------------------------------------------------------------
# No inline SQL guard
# ---------------------------------------------------------------------------


class TestContentServiceNoInlineSQL:
    def test_no_raw_sql_in_content_service(self):
        """content_service must not contain inline SQL."""
        source = inspect.getsource(content_service)
        assert "cursor.execute" not in source
        assert "conn.execute" not in source
