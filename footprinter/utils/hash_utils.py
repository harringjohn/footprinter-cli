"""
Hash computation utilities for Footprinter.

Provides consistent hash computation for both local files and Google Drive matching.
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_md5(file_path: str) -> Optional[str]:
    """
    Compute MD5 hash matching Google Drive's md5Checksum.

    Google Drive uses MD5 for file checksums, so this enables
    hash-based matching between local files and Drive files.

    Args:
        file_path: Path to the file to hash

    Returns:
        32-character lowercase hex MD5 hash, or None on error
    """
    try:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except (IOError, OSError) as e:
        logger.debug(f"Could not compute MD5 for {file_path}: {e}")
        return None


def compute_sha256(file_path: str) -> Optional[str]:
    """
    Compute SHA-256 hash for content deduplication.

    SHA-256 is used for content deduplication and integrity checks
    within the local file system.

    Args:
        file_path: Path to the file to hash

    Returns:
        64-character lowercase hex SHA-256 hash, or None on error
    """
    try:
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    except (IOError, OSError) as e:
        logger.debug(f"Could not compute SHA-256 for {file_path}: {e}")
        return None
