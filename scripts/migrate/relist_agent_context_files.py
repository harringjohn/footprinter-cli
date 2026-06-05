#!/usr/bin/env python3
"""
Relist files under .claude/ and .context/ that were marked unlisted by the
dot-folder rule.

These directories contain curated agent context that should be VIEWER-visible.
The pipeline's _determine_file_status() previously marked ALL dot-directory
contents as unlisted; this script fixes existing rows.

Usage:
    python scripts/migrate/relist_agent_context_files.py           # Process all
    python scripts/migrate/relist_agent_context_files.py --dry-run  # Preview only
    python scripts/migrate/relist_agent_context_files.py --limit 1000
"""

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from footprinter.access_stamper import stamp_entities  # noqa: E402
from footprinter.db.files import AGENT_CONTEXT_DIRS  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "footprinter.db"

BATCH_SIZE = 100


def _build_path_clauses() -> tuple[str, list]:
    """Build SQL path LIKE clauses from AGENT_CONTEXT_DIRS."""
    clauses = [f"path LIKE '%/.{d.lstrip('.')}/%'" for d in AGENT_CONTEXT_DIRS]
    return " OR ".join(clauses), []


def relist_agent_context_files(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Relist agent-context files that were incorrectly marked unlisted.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        dry_run: If True, report what would change without modifying.
        limit: Optional cap on number of rows to update.

    Returns:
        Dict with 'found' and 'updated' counts.
    """
    path_sql, path_params = _build_path_clauses()

    query = f"""
        SELECT id, path, name
        FROM files
        WHERE status = 'unlisted'
          AND status_reason = 'in_dot_folder'
          AND ({path_sql})
          AND name NOT LIKE '%.local.%'
    """
    params = list(path_params)

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    found = len(rows)

    if dry_run:
        print(f"Found {found} files to relist (dry run — no changes)")
        for row in rows[:10]:
            print(f"  {row['name']:40s} {row['path']}")
        if found > 10:
            print(f"  ... and {found - 10} more")
        return {"found": found, "updated": 0}

    changed_ids = [row["id"] for row in rows]

    for i in range(0, len(changed_ids), BATCH_SIZE):
        batch = changed_ids[i : i + BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        cursor.execute(
            f"""
            UPDATE files
            SET status = 'listed',
                status_reason = NULL,
                status_changed_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            batch,
        )
        conn.commit()

    if changed_ids:
        stamp_entities(conn, {"file": changed_ids})

    updated = len(changed_ids)
    print(f"Relisted {updated}/{found} files")

    return {"found": found, "updated": updated}


def main():
    parser = argparse.ArgumentParser(
        description="Relist .claude/ and .context/ files marked unlisted by dot-folder rule"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--limit", type=int, help="Limit number of files to process")

    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        result = relist_agent_context_files(conn, dry_run=args.dry_run, limit=args.limit)
        print(f"\nFound: {result['found']}")
        print(f"Updated: {result['updated']}")
        print(
            "\nVerify with:\n"
            "  sqlite3 ~/.footprinter/footprinter.db"
            " \"SELECT status, COUNT(*) FROM files"
            " WHERE (path LIKE '%/.claude/%' OR path LIKE '%/.context/%')"
            " GROUP BY status\""
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
