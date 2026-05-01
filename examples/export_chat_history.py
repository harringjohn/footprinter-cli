"""Export a chat conversation as markdown.

Demonstrates footprinter.db.chats — listing chats and retrieving
full message history. Prints to stdout; redirect to save as a file.

Usage: ./venv/bin/python3 examples/export_chat_history.py [chat_id]
"""

import sqlite3
import sys

from footprinter.db.chats import get_chat_detail, list_chats
from footprinter.paths import get_db_path

db_path = get_db_path()
if not db_path.exists():
    print("No database found. Run 'fp setup' to get started.")
    sys.exit(0)

conn = sqlite3.connect(str(db_path), timeout=10)
conn.row_factory = sqlite3.Row

# Pick chat: from argument, or most recent
if len(sys.argv) > 1:
    chat_id = int(sys.argv[1])
else:
    result = list_chats(conn, limit=1, sort_by="modified_at", order="desc")
    if not result["chats"]:
        print("No chats found. Import chat exports with 'fp ingest import <file>'.")
        sys.exit(0)
    chat_id = result["chats"][0]["id"]

chat = get_chat_detail(conn, chat_id)
if not chat:
    print(f"Chat {chat_id} not found.")
    sys.exit(0)

# Format as markdown
print(f"# {chat['title'] or 'Untitled Chat'}\n")
for msg in chat.get("messages", []):
    role = (msg["role"] or "unknown").capitalize()
    print(f"## {role}\n\n{msg['content'] or ''}\n")

conn.close()
