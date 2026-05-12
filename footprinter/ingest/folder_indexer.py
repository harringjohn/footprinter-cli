"""
Folder structure indexer for Footprinter.

Scans ~/Work and ~/Personal to discover folder structure before file indexing.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from footprinter.ingest.file_scanner import _expand_home
from footprinter.utils.time import utc_now_iso

if TYPE_CHECKING:
    from footprinter.ingest.database import Database

logger = logging.getLogger(__name__)

# Home directory
HOME = os.path.expanduser("~")


class FolderIndexer:
    """Indexes folder structure for Footprinter."""

    # Directories to always skip (system/build caches)
    SKIP_DIRS = {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "site-packages",
        ".next",
        ".sfdx",
        ".sf",
        ".pytest_cache",
        ".mypy_cache",
        ".eggs",
        ".tox",
        ".nox",
        ".cache",
        "dist",
        "build",
    }

    def __init__(self, config: Dict, db: Database):
        """
        Initialize folder scanner.

        Args:
            config: Configuration dictionary
            db: Shared Database instance
        """
        self.config = config
        self.db = db
        self.always_exclusions = self._compile_always_exclusions()
        self.sensitive_exclusions = self._compile_sensitive_exclusions()

    def _compile_always_exclusions(self) -> List[re.Pattern]:
        """Compile 'always' exclusion patterns (apply to all folders).

        Mirrors FileScanner._compile_always_exclusions so file and folder
        scanners enforce the same config (FPR-1641).
        """
        return self._compile_pattern_list("always")

    def _compile_sensitive_exclusions(self) -> List[re.Pattern]:
        """Compile 'sensitive' exclusion patterns (credentials — never overridable).

        Mirrors FileScanner._compile_sensitive_exclusions. These apply even
        when a directory is explicitly configured as a scan root.
        """
        return self._compile_pattern_list("sensitive")

    def _compile_pattern_list(self, key: str) -> List[re.Pattern]:
        exclusions = self.config.get("exclusions", {})
        return [re.compile(_expand_home(p)) for p in exclusions.get(key, [])]

    def _dir_is_excluded(
        self,
        dirpath: str,
        active_always: Optional[List[re.Pattern]] = None,
    ) -> bool:
        """Return True when a hypothetical descendant of dirpath matches a config pattern.

        Exclusion patterns are written against file paths and typically require
        content after the directory name (e.g. ``.*/node_modules/.*``). Probing
        a synthetic descendant — same trick as file_scanner.py:188-190 — lets us
        prune the directory itself, not just its children.

        ``active_always`` lets the caller substitute a relaxed always-list for
        an opted-in scan root. Sensitive patterns are never overridable.
        """
        always = self.always_exclusions if active_always is None else active_always
        probe = os.path.join(dirpath, "__probe__")
        return any(p.search(probe) for p in always) or any(
            p.search(probe) for p in self.sensitive_exclusions
        )

    def should_skip_dir(self, dir_name: str) -> bool:
        """Check if directory should be skipped.

        v3 Architecture (2026-01): Scan ALL folders including hidden ones.
        Hidden folders are scanned so their files can be indexed with status='unlisted'.

        Only skip regeneratable build/cache directories (node_modules, venv, etc.)
        """
        # Skip known build/cache directories (regeneratable dependencies)
        if dir_name.lower() in self.SKIP_DIRS:
            return True

        # NOTE: Hidden directories (starting with .) are NOT skipped
        # They are scanned so their files can be indexed with status='unlisted'
        # Filter hidden folders in the Web UI, not at scan time

        return False

    def scan_folders(self, root_paths: List[str]) -> List[Dict]:
        """
        Scan folder structure starting from root paths.

        Args:
            root_paths: List of root paths to scan (e.g., ['~/Work', '~/Personal'])

        Returns:
            List of folder dictionaries
        """
        folders = []

        for root_path in root_paths:
            expanded_root = os.path.expanduser(root_path)
            if not os.path.isdir(expanded_root):
                logger.warning(f"Root path does not exist: {expanded_root}")
                continue

            # Explicitly configuring a directory opts in: drop any always-patterns
            # that would exclude every descendant of this root (mirrors
            # file_scanner.py:182-196). Sensitive patterns are never dropped.
            probe_descendant = os.path.join(expanded_root, "__probe__")
            active_always = [
                p for p in self.always_exclusions if not p.search(probe_descendant)
            ]
            deactivated = len(self.always_exclusions) - len(active_always)
            if deactivated:
                logger.info(
                    f"Configured root {expanded_root} opts in past "
                    f"{deactivated} always-exclusion pattern(s) for this scan"
                )

            logger.info(f"Scanning folders in {expanded_root}...")

            for dirpath, dirnames, _ in os.walk(expanded_root):
                # Skip the current dir entirely (no emit, no descent) when it
                # matches a config exclusion (FPR-1641).
                if self._dir_is_excluded(dirpath, active_always=active_always):
                    dirnames[:] = []
                    continue

                # Filter out child directories: by basename (SKIP_DIRS) and by
                # config exclusion.
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not self.should_skip_dir(d)
                    and not self._dir_is_excluded(
                        os.path.join(dirpath, d), active_always=active_always
                    )
                ]

                # Get relative path from home
                if dirpath.startswith(HOME):
                    relative_path = dirpath[len(HOME) :]
                else:
                    relative_path = dirpath

                # Get parent path
                parent_path = os.path.dirname(dirpath)

                folder = {
                    "path": dirpath,
                    "relative_path": relative_path,
                    "name": os.path.basename(dirpath) or relative_path,
                    "parent_path": parent_path if parent_path != dirpath else None,
                    "scanned_at": utc_now_iso(),
                }

                folders.append(folder)

        logger.info(f"Found {len(folders)} folders")
        return folders

    def save_folders(self, folders: List[Dict]) -> Tuple[int, int, int]:
        """
        Save folders to database.

        Args:
            folders: List of folder dictionaries

        Returns:
            Tuple of (inserted_count, updated_count, unchanged_count).
            A row that was previously status='removed' and is reactivated by
            this scan is bucketed as ``inserted`` (mirrors files.py:609-612).
        """
        cursor = self.db.conn.cursor()

        inserted = 0
        updated = 0
        unchanged = 0

        for folder in folders:
            try:
                # Try insert first
                cursor.execute(
                    """
                    INSERT INTO folders
                    (path, relative_path, name, parent_path, scanned_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        folder["path"],
                        folder["relative_path"],
                        folder["name"],
                        folder["parent_path"],
                        folder["scanned_at"],
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # Fast path: skip the UPDATE when persistent identifying fields
                # are unchanged. scanned_at is regenerated each scan, so it's
                # excluded from the comparison. Mirrors files.py:486-508.
                # A status='removed' row never takes the fast path — reactivation
                # is itself a meaningful change (FPR-1708).
                cursor.execute(
                    "SELECT relative_path, name, parent_path, status FROM folders WHERE path = ?",
                    (folder["path"],),
                )
                existing = cursor.fetchone()
                if (
                    existing is not None
                    and existing["status"] != "removed"
                    and existing["relative_path"] == folder["relative_path"]
                    and existing["name"] == folder["name"]
                    and existing["parent_path"] == folder["parent_path"]
                ):
                    unchanged += 1
                    continue

                cursor.execute(
                    """
                    UPDATE folders
                    SET relative_path = ?,
                        name = ?,
                        parent_path = ?,
                        scanned_at = ?,
                        updated_at = CURRENT_TIMESTAMP,
                        status = CASE WHEN status = 'removed' THEN 'listed' ELSE status END,
                        status_reason = CASE WHEN status = 'removed' THEN NULL ELSE status_reason END,
                        status_changed_at = CASE WHEN status = 'removed'
                                                 THEN CURRENT_TIMESTAMP
                                                 ELSE status_changed_at END
                    WHERE path = ?
                """,
                    (
                        folder["relative_path"],
                        folder["name"],
                        folder["parent_path"],
                        folder["scanned_at"],
                        folder["path"],
                    ),
                )
                if existing is not None and existing["status"] == "removed":
                    inserted += 1
                else:
                    updated += 1

        self.db.conn.commit()

        logger.info(
            f"Saved folders: {inserted} inserted, {updated} updated, {unchanged} unchanged"
        )
        return inserted, updated, unchanged

    def get_folder_stats(self) -> Dict:
        """Get statistics about indexed folders."""
        cursor = self.db.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM folders")
        total = cursor.fetchone()[0]

        return {"total_folders": total}


def main():
    """Run folder indexer from command line."""
    from footprinter.ingest.database import Database
    from footprinter.paths import get_db_path
    from footprinter.source_registry import get_config

    # Load config
    config = get_config()

    # Database
    db = Database(str(get_db_path()))

    # Create indexer
    indexer = FolderIndexer(config, db)

    # Scan folders
    root_paths = config.get("directories", [])
    if not root_paths:
        raise ValueError("No directories configured. Add directories to config/config.yaml.")
    folders = indexer.scan_folders(root_paths)

    # Save to database
    inserted, updated, unchanged = indexer.save_folders(folders)

    # Log stats
    stats = indexer.get_folder_stats()
    logger.info("Folder Scan Complete:")
    logger.info(f"  Total folders: {stats['total_folders']}")


if __name__ == "__main__":
    main()
