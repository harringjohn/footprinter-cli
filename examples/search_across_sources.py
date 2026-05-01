"""Search files, emails, and chats for a keyword.

Demonstrates footprinter.db.search keyword functions — unified search
across all indexed data sources.

Usage: ./venv/bin/python3 examples/search_across_sources.py <query>
"""

import sqlite3
import sys

from footprinter.db.search import (
    search_chats_keyword,
    search_emails_keyword,
    search_files_keyword,
)
from footprinter.db.sql_utils import split_query_terms
from footprinter.paths import get_db_path

query = sys.argv[1] if len(sys.argv) > 1 else "project"
terms = split_query_terms(query)

db_path = get_db_path()
if not db_path.exists():
    print("No database found. Run 'fp setup' to get started.")
    sys.exit(0)

conn = sqlite3.connect(str(db_path), timeout=10)
conn.row_factory = sqlite3.Row

# Search each source type
for label, search_fn in [
    ("Files", search_files_keyword),
    ("Emails", search_emails_keyword),
    ("Chats", search_chats_keyword),
]:
    results = search_fn(conn, terms=terms, has_query=bool(terms), limit=5)
    print(f"\n--- {label} ({len(results)} results) ---")
    if not results:
        print("  (none)")
    for r in results:
        name = r.get("name") or r.get("subject") or r.get("title") or "(untitled)"
        print(f"  {name}")

conn.close()
