"""List the 20 most recently indexed files in Footprinter.

Demonstrates footprinter.db.files.list_files() — paginated file listing
with filtering by source, status, and content type.
"""

import sqlite3
import sys

from footprinter.db.files import list_files
from footprinter.paths import get_db_path

db_path = get_db_path()
if not db_path.exists():
    print("No database found. Run 'fp setup' to get started.")
    sys.exit(0)

conn = sqlite3.connect(str(db_path), timeout=10)
conn.row_factory = sqlite3.Row

result = list_files(conn, limit=20)
files = result["files"]

if not files:
    print("No files indexed yet. Run 'fp ingest' to get started.")
    sys.exit(0)

# Print a simple table
print(f"{'Name':<40} {'Source':<12} {'Size':>10} {'Modified'}")
print("-" * 80)
for f in files:
    size = f["size_bytes"] or 0
    if size >= 1_000_000:
        size_str = f"{size / 1_000_000:.1f} MB"
    elif size >= 1_000:
        size_str = f"{size / 1_000:.1f} KB"
    else:
        size_str = f"{size} B"
    name = (f["name"] or "")[:39]
    modified = (f["modified_at"] or "")[:19]
    print(f"{name:<40} {f['source'] or '':<12} {size_str:>10} {modified}")

print(f"\nShowing {len(files)} of {result['pagination']['total']} files.")
conn.close()
