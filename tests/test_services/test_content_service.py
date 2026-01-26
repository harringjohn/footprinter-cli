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
                "footprinter.mcp.extraction.get_extractor_for_file",
                return_value="markdown",
            ),
            patch(
                "footprinter.mcp.extraction.extract_text",
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
                "footprinter.mcp.extraction.get_extractor_for_file",
                return_value="pdf",
            ),
            patch(
                "footprinter.mcp.extraction.extract_text",
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
                "footprinter.mcp.extraction.get_extractor_for_file",
                return_value="binary",
            ),
        ):
            result = content_service.read_file(conn, metadata, format="raw")

        assert result["status"] == "ok"
        assert result["content"] == "raw bytes here"
        assert result["metadata"]["extraction_method"] == "raw"


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
