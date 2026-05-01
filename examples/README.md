# Examples

Starter scripts demonstrating the `footprinter.db` Python API.

## Scripts

| Script | Description |
|--------|-------------|
| `list_recent_files.py` | List the 20 most recently indexed files |
| `search_across_sources.py` | Keyword search across files, emails, and chats |
| `export_chat_history.py` | Export a chat conversation as markdown |

## Usage

Run any script from the project root:

```bash
./venv/bin/python3 examples/list_recent_files.py
./venv/bin/python3 examples/search_across_sources.py "meeting notes"
./venv/bin/python3 examples/export_chat_history.py        # most recent chat
./venv/bin/python3 examples/export_chat_history.py 42     # specific chat ID
```

Scripts require an indexed database. If you haven't run ingestion yet:

```bash
fp setup      # first-time configuration
fp ingest     # index local files, emails, browser history
```
