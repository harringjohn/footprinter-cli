"""Tests for footprinter.utils.mime — extracted MIME-to-content-type utility."""

from footprinter.utils.mime import mime_to_content_type


class TestMimeToContentType:
    """Tests for mime_to_content_type()."""

    def test_known_mime_types(self):
        """Each mapped MIME type returns the expected short string."""
        cases = {
            "application/pdf": "pdf",
            "application/vnd.google-apps.document": "gdoc",
            "application/vnd.google-apps.spreadsheet": "gsheet",
            "application/vnd.google-apps.presentation": "gslides",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
            "text/plain": "txt",
            "text/csv": "csv",
            "image/jpeg": "jpg",
            "image/png": "png",
            "video/mp4": "mp4",
        }
        for mime, expected in cases.items():
            assert mime_to_content_type(mime) == expected, f"mime_to_content_type({mime!r}) should be {expected!r}"

    def test_empty_string_returns_unknown(self):
        """Empty string is falsy — should return 'unknown'."""
        assert mime_to_content_type("") == "unknown"

    def test_none_returns_unknown(self):
        """None is falsy — should return 'unknown'."""
        assert mime_to_content_type(None) == "unknown"

    def test_unmapped_mime_truncates(self):
        """Unmapped MIME type falls back to subtype, truncated to 8 chars."""
        assert mime_to_content_type("application/zip") == "zip"

    def test_long_subtype_truncated(self):
        """Long subtypes get truncated to 8 characters."""
        result = mime_to_content_type("application/x-very-long-subtype")
        assert result == "x-very-l"
        assert len(result) == 8
