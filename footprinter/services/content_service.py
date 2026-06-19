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

# Ceiling on the text returned in a single read result, measured in UTF-8 *bytes*.
# A single MCP tool result has a ~1 MB payload protocol wall (the same limit
# search.py caps result counts against — see footprinter/mcp/tools/search.py). The
# read cap above bounds the bytes pulled from disk, but a mid-size file (e.g. a
# multi-MB PDF) can extract to text larger than that wall and fail to return
# entirely. This ceiling bounds the returned content independently of the input
# read cap so a read always returns a usable, in-budget result.
#
# The wall is bytes, not characters: a char-count bound let multibyte UTF-8 content
# (CJK, emoji) through — 800k CJK chars is ~2.4 MB raw and more once JSON-escaped,
# still over the wall. The budget below is a UTF-8 byte budget derived from the
# ~1 MB wall with two reservations:
#   - JSON-escape expansion of the *serialized* result. Under ensure_ascii=True a
#     3-byte CJK code point escapes to "\uXXXX" (6 bytes, ~2x) and a 4-byte non-BMP
#     code point to a surrogate pair "\uXXXX\uXXXX" (12 bytes, ~3x). Sizing the
#     budget at 300 KB keeps even the non-BMP worst case (3x -> ~900 KB) under the
#     wall.
#   - Headroom for the JSON envelope, the per-type identity fields, and the
#     metadata dict that travel alongside the content.
# Slicing happens on a code-point boundary so the returned string is always valid
# UTF-8 and re-encodes within budget.
_MAX_OUTPUT_BYTES = 300_000

# Message attached when the output bound trips, pointing callers at search for
# large documents (mirrors search.py's protocol-limit phrasing).
_OUTPUT_TRUNCATED_MESSAGE = (
    "content truncated to stay within the ~1 MB tool-result protocol limit; "
    "use semantic or keyword search to locate the relevant section of large documents"
)


def _bound_output(content: str) -> tuple[str, bool]:
    """Bound returned content to the payload-safe output byte budget.

    Returns ``(content, False)`` unchanged when its UTF-8 encoding is within
    ``_MAX_OUTPUT_BYTES``. Otherwise truncates the UTF-8 bytes to the budget and
    backs off to the last whole code point (``decode(..., "ignore")`` drops a
    trailing partial code point), returning ``(sliced, True)``. The returned
    string is always valid UTF-8 and re-encodes within budget.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return content, False
    sliced = encoded[:_MAX_OUTPUT_BYTES].decode("utf-8", "ignore")
    return sliced, True


def _ok_result(content: str, meta: dict) -> dict:
    """Build an ``ok`` read result, bounding content to the output byte budget.

    When the bound trips, marks ``meta["output_truncated"] = True`` and records a
    pointer to search for large documents in ``meta["output_truncated_message"]``,
    so the result is always in-budget and usable rather than an oversized payload
    that fails to return. The pointer reaches the MCP client via ``metadata`` —
    ``read.py``'s ``_with_identity`` rebuilds the result as
    ``{identity, content, metadata}`` and forwards no top-level fields.
    """
    bounded, was_bounded = _bound_output(content)
    result: dict = {"status": "ok", "content": bounded, "metadata": meta}
    if was_bounded:
        meta["output_truncated"] = True
        meta["output_truncated_message"] = _OUTPUT_TRUNCATED_MESSAGE
    return result


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
        return _ok_result(content, meta)

    # Text mode with extraction
    extracted_text, error = extract_text(data, extractor_type)

    if extracted_text is not None:
        meta["extraction_method"] = extractor_type
        meta["extraction_success"] = True
        meta["extraction_error"] = None
        return _ok_result(extracted_text, meta)

    # Extraction failed. If the file was truncated by the read cap, the byte
    # range is structurally incomplete and a raw decode would return unusable
    # binary while masquerading as a successful read. Signal the truncation
    # explicitly instead of falling back.
    if _was_truncated(
        data, max_read_bytes, meta.get("size_bytes"), path=meta.get("path")
    ):
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
    return _ok_result(content, meta)


def read_file_from_vectors(
    conn: sqlite3.Connection,
    metadata: dict,
    query: str,
) -> dict:
    """Read file content from indexed vector chunks instead of disk.

    Parallels :func:`read_file`'s dispatch so the MCP layer stays
    source-agnostic: requires ``metadata`` from a prior ``gate_access()`` call
    (status ``ok``), reassembles the matched chunk + neighbors via
    ``semantic_service.read_file_chunks``, and returns the uniform
    ``{status, content, metadata}`` shape.

    On success the returned ``metadata`` carries ``source="vectors"`` provenance
    plus the reassembly caveats (``from_vectors`` / ``reassembled`` /
    ``incomplete`` / ``chunk_indices``) and the excerpt-contract fields. Returns
    ``read_failed`` when the file has no indexed chunks matching ``query`` or the
    vector store is unavailable.
    """
    from footprinter.services import semantic_service

    meta = dict(metadata)
    file_id = meta.get("id")
    if file_id is None:
        return {
            "status": "read_failed",
            "metadata": meta,
            "message": "file: missing id for vector read",
        }

    chunk_result = semantic_service.read_file_chunks(conn, file_id, query or "")
    status = chunk_result.get("status")
    if status in ("no_match", "unavailable"):
        return {
            "status": "read_failed",
            "metadata": meta,
            "message": f"file:{file_id} no indexed chunks for vector read ({status})",
        }

    content = chunk_result.pop("excerpt", "")
    # Stamp source provenance + carry the reassembly caveats and excerpt contract.
    meta["source"] = "vectors"
    meta["extraction_method"] = "vectors"
    meta["extraction_success"] = True
    meta["extraction_error"] = None
    meta.update(chunk_result)
    return _ok_result(content, meta)


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


def _file_exceeds_cap(path: str, max_read_bytes: int) -> Optional[bool]:
    """Probe whether a local file has any bytes past ``max_read_bytes``.

    Reads one byte past the cap via ``_read_local_file_bytes`` (which keeps the
    path-containment guard) and reports ``True`` if at least one byte exists past
    the cap, ``False`` if not. Returns ``None`` when it cannot tell — no path, or
    the read returned ``None`` (missing file / containment violation / error) —
    so the caller can apply a conservative default. The probe bytes are read
    separately and discarded; the file's primary ``data`` is never altered.
    """
    if not path:
        return None
    probe = _read_local_file_bytes(path, max_bytes=max_read_bytes + 1)
    if probe is None:
        return None
    return len(probe) > max_read_bytes


def _was_truncated(
    data: bytes,
    max_read_bytes: int,
    size_bytes: Optional[int] = None,
    path: Optional[str] = None,
) -> bool:
    """Decide whether ``data`` was truncated by the read cap.

    With a cap in effect (``max_read_bytes > 0``), a read that returns at least
    ``max_read_bytes`` bytes *may* mean the file was cut off — but a complete file
    that happens to be exactly cap-sized is not truncated. Corroborate the
    boundary with the indexed ``size_bytes``:

    - ``size_bytes > max_read_bytes`` → more bytes exist past the cap → truncated
      (also covers under-cap reads with a stale-large indexed size).
    - ``len(data) < max_read_bytes`` (and not flagged above) → under the cap →
      not truncated.
    - At the boundary with a known ``size_bytes`` (``<= cap`` here) → complete →
      not truncated.
    - At the boundary with an unknown ``size_bytes`` → probe one byte past the cap
      for a local ``path``; if the probe is inconclusive (no path / remote /
      read error) fall back to the conservative ``True`` default.
    """
    if max_read_bytes <= 0:
        return False
    try:
        if size_bytes is not None and int(size_bytes) > max_read_bytes:
            return True
        size_known = size_bytes is not None
    except (TypeError, ValueError):
        size_known = False
    if len(data) < max_read_bytes:
        return False
    # Boundary: len(data) >= max_read_bytes.
    if size_known:
        # size_bytes is not None and (after the guard above) <= cap → complete.
        return False
    probe = _file_exceeds_cap(path or "", max_read_bytes)
    if probe is None:
        return True
    return probe


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
