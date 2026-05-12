#!/usr/bin/env python3
"""
Backfill MD5 hashes for existing local files.

Google Drive uses MD5 checksums for file verification. This script computes
MD5 hashes for existing local files that don't have one, enabling
hash-based linking between local files and their Drive backups.

Usage:
    python scripts/backfill_md5_hashes.py           # Process all
    python scripts/backfill_md5_hashes.py --dry-run # Preview only
    python scripts/backfill_md5_hashes.py --limit 1000  # Process batch
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from footprinter.utils.hash_utils import compute_md5  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "footprinter.db"


def backfill_md5_hashes(dry_run: bool = False, limit: int = None):
    """
    Backfill MD5 hashes for local files.

    Args:
        dry_run: If True, only report what would be done
        limit: Optional limit on number of files to process
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find local files without md5_hash
    query = """
        SELECT id, path, name
        FROM files
        WHERE source = 'local'
          AND md5_hash IS NULL
          AND status != 'removed'
    """
    if limit:
        query += " LIMIT ?"
        cursor.execute(query, (limit,))
    else:
        cursor.execute(query)
    rows = cursor.fetchall()

    print(f"Found {len(rows)} local files without MD5 hash")

    if dry_run:
        print("\nDry run - showing first 10 files:")
        for row in rows[:10]:
            exists = "exists" if os.path.exists(row["path"]) else "MISSING"
            print(f"  [{exists}] {row['name']}")
        if len(rows) > 10:
            print(f"  ... and {len(rows) - 10} more")
        return

    processed = 0
    updated = 0
    missing = 0
    errors = 0
    batch_size = 100

    start_time = datetime.now()
    print(f"\nStarting backfill at {start_time.strftime('%H:%M:%S')}...")

    for row in rows:
        file_path = row["path"]
        file_id = row["id"]

        processed += 1

        # Check if file exists
        if not os.path.exists(file_path):
            missing += 1
            continue

        # Compute MD5
        md5_hash = compute_md5(file_path)

        if md5_hash is None:
            errors += 1
            if errors <= 5:
                print(f"  Error hashing: {row['name']}")
            continue

        # Update database
        try:
            cursor.execute(
                """
                UPDATE files SET md5_hash = ? WHERE id = ?
            """,
                (md5_hash, file_id),
            )
            updated += 1

            # Commit in batches
            if updated % batch_size == 0:
                conn.commit()
                elapsed = (datetime.now() - start_time).total_seconds()
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Processed {processed}/{len(rows)} ({rate:.1f}/sec), updated {updated}")

        except sqlite3.Error as e:
            errors += 1
            if errors <= 5:
                print(f"  DB error for {row['name']}: {e}")

    # Final commit
    conn.commit()
    conn.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\nBackfill complete in {elapsed:.1f}s:")
    print(f"  Processed: {processed}")
    print(f"  Updated:   {updated}")
    print(f"  Missing:   {missing}")
    print(f"  Errors:    {errors}")

    # Show verification query
    print("\nVerify with:")
    print(
        '  sqlite3 ~/.footprinter/footprinter.db'
        ' "SELECT source, COUNT(*) as total,'
        " COUNT(md5_hash) as has_md5 FROM files"
        " WHERE status!='removed' GROUP BY source\""
    )


def main():
    parser = argparse.ArgumentParser(description="Backfill MD5 hashes for local files")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--limit", type=int, help="Limit number of files to process")

    args = parser.parse_args()

    backfill_md5_hashes(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
