"""content_service — file content I/O (disk reads, remote reads, text extraction).

Extracted from the former ``read_service`` module.  This module owns all
filesystem and remote storage I/O.  Access gating lives in ``access_service``.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_file(
    conn: sqlite3.Connection,
    metadata: dict,
    *,
    format: Literal["text", "raw"] = "text",
) -> dict:
    """Read file content from disk or a remote connector.

    Requires ``metadata`` from a prior ``gate_access()`` call (status ``ok``).
    Returns dict with ``status`` (``ok`` or error), ``content``, ``metadata``.
    """
    from footprinter.source_registry import SourceRegistry
    from footprinter.utils.extraction import extract_text, get_extractor_for_file

    meta = dict(metadata)
    source = meta.get("source", "")
    name = meta.get("name", "")
    mime_type = meta.get("mime_type", "")

    # Get raw bytes
    registry = SourceRegistry(conn)
    data: Optional[bytes] = None

    if source == "local":
        data = _read_local_file_bytes(meta.get("path", ""))
    elif registry.is_remote_source(source):
        external_id = meta.get("external_id")
        account = meta.get("account")
        if external_id and account:
            data = _read_remote_file_bytes(source, external_id, account, mime_type)
        else:
            return {
                "status": "read_failed",
                "metadata": meta,
                "message": f"file:{meta.get('id')} missing external_id or account",
            }
    else:
        return {
            "status": "read_failed",
            "metadata": meta,
            "message": f"file:{meta.get('id')} unknown source={source}",
        }

    if data is None:
        return {
            "status": "read_failed",
            "metadata": meta,
            "message": f"file:{meta.get('id')} null data from {source}",
        }

    # Determine if extraction is needed
    extractor_type = get_extractor_for_file(name, mime_type)

    if format == "raw" or extractor_type is None:
        content = _decode_bytes(data)
        if content is None:
            return {
                "status": "decode_failed",
                "metadata": meta,
                "message": f"file:{meta.get('id')} decode failed (raw mode)",
            }
        meta["extraction_method"] = "raw"
        meta["extraction_success"] = True
        meta["extraction_error"] = None
        return {"status": "ok", "content": content, "metadata": meta}

    # Text mode with extraction
    extracted_text, error = extract_text(data, extractor_type)

    if extracted_text is not None:
        meta["extraction_method"] = extractor_type
        meta["extraction_success"] = True
        meta["extraction_error"] = None
        return {"status": "ok", "content": extracted_text, "metadata": meta}

    # Extraction failed — fall back to raw decode
    logger.warning(f"Extraction failed for {name}: {error}, falling back to raw")
    content = _decode_bytes(data)
    if content is None:
        meta["extraction_method"] = extractor_type
        meta["extraction_success"] = False
        meta["extraction_error"] = error
        return {
            "status": "extraction_failed",
            "metadata": meta,
            "message": f"file:{meta.get('id')} extraction+decode failed: {error}",
        }

    meta["extraction_method"] = "raw"
    meta["extraction_success"] = False
    meta["extraction_error"] = error
    return {"status": "ok", "content": content, "metadata": meta}


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _validate_local_path(path: str) -> Path:
    """Validate that a local path falls within the home directory.

    Defense-in-depth: prevents a corrupted database from being used
    to read arbitrary files via the MCP server.
    """
    p = Path(path).resolve()
    home = Path.home().resolve()
    if not str(p).startswith(str(home) + os.sep) and p != home:
        raise PermissionError(f"Path outside home directory: {p}")
    return p


def _read_local_file_bytes(path: str, max_bytes: int = 500_000) -> Optional[bytes]:
    """Read a local file as raw bytes."""
    try:
        p = _validate_local_path(path)
        if not p.exists():
            return None
        with open(p, "rb") as f:
            data = f.read(max_bytes)
        return data
    except PermissionError as e:
        logger.error(f"Path containment violation for {path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading local file bytes {path}: {e}")
        return None


def _read_remote_file_bytes(
    source: str,
    external_id: str,
    account: str,
    mime_type: str = "",
) -> Optional[bytes]:
    """Download raw bytes from a remote source via connector hooks."""
    from footprinter.connectors import discover_connectors, is_installed, resolve_hook

    for name, spec in discover_connectors().items():
        if not spec.seed_prefix or not source.startswith(spec.seed_prefix + "_"):
            continue
        # Single-match: each source maps to exactly one connector via seed_prefix.
        if not is_installed(spec) or not spec.read_file:
            logger.error(
                "Connector %s matches source=%s but is not installed or has no read_file hook",
                name,
                source,
            )
            return None
        try:
            fn = resolve_hook(spec.read_file)
            if fn:
                return fn(external_id, account, mime_type)
            logger.error("read_file hook for connector %s resolved to None", name)
        except Exception:
            logger.error("read_file hook failed for connector %s", name, exc_info=True)
        return None

    logger.error("No connector matches source=%s", source)
    return None


def _decode_bytes(data: bytes) -> Optional[str]:
    """Decode bytes to string, trying UTF-8 then Latin-1."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            return None
