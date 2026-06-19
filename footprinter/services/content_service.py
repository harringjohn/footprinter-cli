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

# Default MCP read-path size cap, in MB. Mirrors the configurable ingest caps
# (indexing.max_file_size_mb, vectorization.max_vectorize_size_mb) so the read
# tool can return content the index already holds. 10 MB closes the historical
# 500 KB silent-truncation gap while staying well under the MCP tool-result
# payload ceiling. Overridable via indexing.max_read_size_mb; 0 = no cap.
_DEFAULT_MAX_READ_MB = 10


def _get_max_read_bytes() -> int:
    """Resolve the MCP read-path byte cap from config, lazily.

    Reads ``indexing.max_read_size_mb`` (MB) and converts to bytes. A value of
    ``0`` means "no cap" (read the entire file). Falls back to the documented
    default on any error so a missing/corrupt config never breaks reads.
    """
    try:
        from footprinter.source_registry import get_config

        mb = get_config().get("indexing", {}).get("max_read_size_mb", _DEFAULT_MAX_READ_MB)
        mb = int(mb)
        if mb < 0:
            mb = _DEFAULT_MAX_READ_MB
        return mb * 1024 * 1024
    except Exception:
        logger.debug(
            "Config unavailable for indexing.max_read_size_mb, using default %d MB",
            _DEFAULT_MAX_READ_MB,
        )
        return _DEFAULT_MAX_READ_MB * 1024 * 1024


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
    max_read_bytes = 0

    if source == "local":
        max_read_bytes = _get_max_read_bytes()
        data = _read_local_file_bytes(meta.get("path", ""), max_bytes=max_read_bytes)
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

    # Extraction failed. If the file was truncated by the read cap, the byte
    # range is structurally incomplete and a raw decode would return unusable
    # binary while masquerading as a successful read. Signal the truncation
    # explicitly instead of falling back.
    if _was_truncated(data, max_read_bytes, meta.get("size_bytes")):
        meta["extraction_method"] = extractor_type
        meta["extraction_success"] = False
        meta["extraction_error"] = error
        meta["extraction_incomplete"] = True
        meta["truncated"] = True
        cap_mb = max_read_bytes // (1024 * 1024)
        logger.warning(
            "Extraction failed for %s on a file truncated at the %d MB read cap: %s",
            name,
            cap_mb,
            error,
        )
        return {
            "status": "extraction_failed",
            "metadata": meta,
            "message": (
                f"file:{meta.get('id')} extraction failed on a file truncated at the "
                f"{cap_mb} MB read cap; raise indexing.max_read_size_mb to read more "
                f"(0 = no cap): {error}"
            ),
        }

    # Not truncated — fall back to raw decode (existing behavior).
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


def _was_truncated(
    data: bytes, max_read_bytes: int, size_bytes: Optional[int] = None
) -> bool:
    """Decide whether ``data`` was truncated by the read cap.

    With a cap in effect (``max_read_bytes > 0``), a read that returns at least
    ``max_read_bytes`` bytes means the file was cap-sized or larger and the
    returned range is likely incomplete. The indexed ``size_bytes`` corroborates
    when present but is not required (it can be stale).
    """
    if max_read_bytes <= 0:
        return False
    if len(data) >= max_read_bytes:
        return True
    try:
        if size_bytes is not None and int(size_bytes) > max_read_bytes:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _read_local_file_bytes(path: str, max_bytes: int = 500_000) -> Optional[bytes]:
    """Read a local file as raw bytes.

    ``max_bytes <= 0`` reads the entire file (no cap).
    """
    try:
        p = _validate_local_path(path)
        if not p.exists():
            return None
        with open(p, "rb") as f:
            data = f.read() if max_bytes <= 0 else f.read(max_bytes)
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
