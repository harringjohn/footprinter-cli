"""MIME type to content type mapping.

Used by both the orchestrator and Drive files adapter.
"""


def mime_to_content_type(mime_type: str) -> str:
    """Convert MIME type to short content type string.

    Returns a short label for known types (e.g. "pdf", "gdoc"),
    or truncates the MIME subtype to 8 chars for unknown types.
    Returns "unknown" for falsy input.
    """
    if not mime_type:
        return "unknown"
    mime_map = {
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
    return mime_map.get(mime_type, mime_type.split("/")[-1][:8])
