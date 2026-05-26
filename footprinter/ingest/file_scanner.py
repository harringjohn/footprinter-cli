"""
File system scanner with exclusion support.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional

from ..utils.hash_utils import compute_md5, compute_sha256
from ..utils.time import UTC_FMT

logger = logging.getLogger(__name__)


def _get_creation_time(stat_result: os.stat_result) -> float:
    """Return the best available file creation timestamp.

    On macOS (and Python 3.12+ on Linux with supported filesystems),
    ``st_birthtime`` gives the true creation time. On older Linux,
    ``st_ctime`` is the inode-change time (chmod, chown) — not creation —
    so we fall back to ``st_mtime`` as the closest available proxy.
    """
    if hasattr(stat_result, "st_birthtime") and stat_result.st_birthtime > 0:
        return stat_result.st_birthtime
    return stat_result.st_mtime


def _expand_home(pattern: str) -> str:
    """Expand ~ to the current user's home directory in regex patterns.

    Patterns starting with ^~/ have the ~ replaced with the regex-escaped
    home directory path, making them platform-agnostic.
    """
    if pattern.startswith("^~/"):
        home = re.escape(os.path.expanduser("~"))
        return "^" + home + pattern[2:]
    return pattern


class FileScanner:
    """File system scanner with configurable exclusion patterns."""

    def __init__(
        self,
        config: Dict,
        since_datetime: Optional[datetime] = None,
        scan_roots: Optional[List[str]] = None,
        known_paths: Optional[set] = None,
    ):
        """
        Initialize file scanner.

        Args:
            config: Configuration dictionary
            since_datetime: If provided, only scan files modified after this datetime
                           (for incremental indexing)
            scan_roots: When set, scan only these paths instead of
                config["directories"] (used by `fp setup folders add`).
            known_paths: Already-indexed local file paths. When provided,
                files with old mtime at unknown paths are yielded as moved files.
        """
        self.config = config
        self.since_datetime = since_datetime
        self.scan_roots = scan_roots
        self.known_paths = known_paths
        self.always_exclusions = self._compile_always_exclusions()
        self.sensitive_exclusions = self._compile_sensitive_exclusions()
        self.supported_extensions = set(config.get("indexing", {}).get("supported_extensions", []))
        # 0 = no size limit (matches config.example.yaml)
        self.max_file_size = config.get("indexing", {}).get("max_file_size_mb", 0) * 1024 * 1024

    def _compile_always_exclusions(self) -> List[re.Pattern]:
        """Compile 'always' exclusion patterns (apply to all folders)."""
        patterns = []
        exclusions = self.config.get("exclusions", {})

        for pattern in exclusions.get("always", []):
            patterns.append(re.compile(_expand_home(pattern)))

        return patterns

    def _compile_sensitive_exclusions(self) -> List[re.Pattern]:
        """Compile sensitive exclusion patterns (apply to all folders)."""
        patterns = []
        exclusions = self.config.get("exclusions", {})

        for pattern in exclusions.get("sensitive", []):
            patterns.append(re.compile(_expand_home(pattern)))

        return patterns

    def should_exclude(self, file_path: str, active_always: Optional[List[re.Pattern]] = None) -> bool:
        """
        Check if file should be excluded based on patterns.

        v3 Architecture (2026-01): Index ALL files in ~/Work and ~/Personal.
        Hidden files are indexed with status='unlisted', not excluded.

        Only exclude:
        - always: Regeneratable dependencies (node_modules, venv, .git internals)
        - sensitive: Credentials and keys (.aws, .ssh, .kube)

        Args:
            file_path: Absolute path to evaluate.
            active_always: Optional override list of always-patterns. When the
                caller is scanning an explicitly configured root, patterns whose
                only purpose was to skip that area at home-scan time are filtered
                out. Sensitive patterns are never overridable — credentials must
                never be indexed regardless of how a directory was configured.
        """
        always_patterns = self.always_exclusions if active_always is None else active_always

        # Check 'always' exclusions (node_modules, venv, .git internals, etc.)
        # These are regeneratable dependencies and system noise
        for pattern in always_patterns:
            if pattern.search(file_path):
                logger.debug(f"Excluding {file_path} (always pattern)")
                return True

        # Check sensitive exclusions (.aws, .ssh, .kube - credentials)
        # These should never be indexed for security
        for pattern in self.sensitive_exclusions:
            if pattern.search(file_path):
                logger.debug(f"Excluding {file_path} (sensitive pattern)")
                return True

        # NOTE: Hidden files (starting with .) are NOT excluded
        # They are indexed with status='unlisted' in the database

        return False

    def is_supported_file(self, file_path: Path) -> bool:
        """Check if file type is supported."""
        if not self.supported_extensions:
            return True  # No filter = support all
        return file_path.suffix.lower() in self.supported_extensions

    def get_file_metadata(self, file_path: Path, skip_hashing: bool = False) -> Optional[Dict]:
        """Extract file metadata.

        When ``skip_hashing`` is True, the expensive sha256/md5 reads are
        skipped and the corresponding fields are returned as ``None``. Used
        by ``fp ingest --preview`` where hashing dominates wall
        clock on large trees and is unnecessary for a summary.
        """
        try:
            stat = file_path.stat()

            # Skip if too large (0 = no limit)
            if self.max_file_size > 0 and stat.st_size > self.max_file_size:
                logger.debug(f"Skipping {file_path} (too large: {stat.st_size} bytes)")
                return None

            file_path_str = str(file_path.absolute())

            if skip_hashing:
                sha256_hash = None
                md5_hash = None
            else:
                # Calculate both hashes:
                # - SHA-256 for content deduplication
                # - MD5 for Google Drive matching (Drive uses MD5)
                sha256_hash = compute_sha256(file_path_str)
                md5_hash = compute_md5(file_path_str)

            return {
                "file_path": file_path_str,
                "file_name": file_path.name,
                "file_type": file_path.suffix.lower() or "no_extension",
                "file_size": stat.st_size,
                "created_at": datetime.fromtimestamp(_get_creation_time(stat), tz=timezone.utc).strftime(UTC_FMT),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(UTC_FMT),
                "accessed_at": datetime.fromtimestamp(stat.st_atime, tz=timezone.utc).strftime(UTC_FMT),
                "sha256_hash": sha256_hash,
                "md5_hash": md5_hash,
                "metadata": {
                    "permissions": oct(stat.st_mode)[-3:],
                },
            }
        except (OSError, OverflowError) as e:
            logger.error(f"Error reading metadata for {file_path}: {e}")
            return None

    def scan_directory(self, directory: str, skip_hashing: bool = False) -> Generator[Dict, None, None]:
        """
        Scan directory and yield file metadata.

        Yields file metadata dictionaries for indexing.
        If since_datetime is set, only yields files modified after that time.
        When ``skip_hashing`` is True, sha256/md5 are not computed (preview mode).
        """
        directory_path = Path(directory).expanduser().resolve()

        if not directory_path.exists():
            logger.error(f"Directory does not exist: {directory_path}")
            return

        if not directory_path.is_dir():
            logger.error(f"Path is not a directory: {directory_path}")
            return

        # Explicitly configuring a directory opts in: drop any always-patterns
        # that would exclude every file under this root (e.g. `^~/Downloads/.*`
        # should not silently zero out a configured `~/Downloads/sample-data/`).
        # Probe with a synthetic descendant so we drop only patterns that match
        # arbitrary contents — not patterns that happen to share a substring
        # with the root path. Sensitive patterns are never dropped.
        scan_root_str = str(directory_path)
        probe_descendant = os.path.join(scan_root_str, "__probe__")
        active_always = [p for p in self.always_exclusions if not p.search(probe_descendant)]
        deactivated = len(self.always_exclusions) - len(active_always)
        if deactivated:
            logger.info(
                f"Configured root {directory_path} opts in past {deactivated} "
                f"always-exclusion pattern(s) for this scan"
            )

        if self.since_datetime:
            logger.info(f"Scanning directory: {directory_path} (incremental since {self.since_datetime})")
        else:
            logger.info(f"Scanning directory: {directory_path}")

        file_count = 0
        excluded_count = 0
        skipped_unchanged = 0
        moved_count = 0
        error_count = 0
        seen_real_paths: set[str] = set()

        try:
            for root, dirs, files in os.walk(directory_path, followlinks=True):
                # Check if current directory should be excluded
                # Also check resolved path for directory symlinks
                root_path = Path(root)
                if self.should_exclude(root, active_always=active_always):
                    dirs[:] = []  # Don't recurse into this directory
                    excluded_count += len(files)
                    continue
                if root_path.is_symlink():
                    resolved_root = str(root_path.resolve())
                    # Resolved symlink target may live outside the configured
                    # root — apply the full exclusion set, not the relaxed list.
                    if (
                        self.should_exclude(resolved_root)
                        or resolved_root in seen_real_paths
                    ):
                        dirs[:] = []
                        excluded_count += len(files)
                        continue
                    seen_real_paths.add(resolved_root)

                for file_name in files:
                    file_path = Path(root) / file_name

                    # Skip broken symlinks early (before stat() call)
                    if file_path.is_symlink() and not file_path.exists():
                        logger.debug(f"Skipping broken symlink: {file_path}")
                        continue

                    # Skip excluded files (check symlink path)
                    if self.should_exclude(str(file_path), active_always=active_always):
                        excluded_count += 1
                        continue

                    # For symlinks, also check the resolved target against exclusions.
                    # The target may live outside the configured root, so apply the
                    # full exclusion set rather than the per-scan relaxed list.
                    if file_path.is_symlink():
                        real_path = str(file_path.resolve())
                        if self.should_exclude(real_path):
                            excluded_count += 1
                            continue
                    else:
                        real_path = str(file_path.resolve())

                    # Dedup: skip if we've already seen this real path
                    if real_path in seen_real_paths:
                        logger.debug(f"Skipping duplicate path: {file_path} -> {real_path}")
                        continue
                    seen_real_paths.add(real_path)

                    # Skip unsupported file types
                    if not self.is_supported_file(file_path):
                        continue

                    # For incremental indexing, skip files not modified since last run
                    if self.since_datetime:
                        try:
                            mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                            since = (
                                self.since_datetime.replace(tzinfo=timezone.utc)
                                if self.since_datetime.tzinfo is None
                                else self.since_datetime
                            )
                            if mtime <= since:
                                if self.known_paths is not None and str(file_path.absolute()) not in self.known_paths:
                                    moved_count += 1
                                    logger.debug("Moved file detected: %s", file_path)
                                else:
                                    skipped_unchanged += 1
                                    continue
                        except OSError:
                            logger.debug("Could not check mtime for %s, processing anyway", file_path)

                    # Get metadata
                    metadata = self.get_file_metadata(file_path, skip_hashing=skip_hashing)
                    if metadata:
                        file_count += 1
                        yield metadata
                    else:
                        error_count += 1

        except OSError as e:
            logger.error(f"Error scanning directory {directory_path}: {e}")
            error_count += 1

        if self.since_datetime:
            logger.info(
                f"Scan complete: {file_count} new/modified,"
                f" {moved_count} moved,"
                f" {skipped_unchanged} unchanged,"
                f" {excluded_count} excluded,"
                f" {error_count} errors"
            )
        else:
            logger.info(f"Scan complete: {file_count} files, {excluded_count} excluded, {error_count} errors")

    def scan_all_directories(self, skip_hashing: bool = False) -> Generator[Dict, None, None]:
        """Scan all configured directories (or only ``scan_roots`` when set).

        ``skip_hashing`` propagates to ``scan_directory`` for the preview path
        (preview mode), where the heavy md5/sha256 reads are unnecessary.
        """
        directories = (
            self.scan_roots if self.scan_roots is not None else self.config.get("directories", [])
        )

        for directory in directories:
            yield from self.scan_directory(directory, skip_hashing=skip_hashing)
