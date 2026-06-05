# Footprinter MCP Server

Model Context Protocol server providing AI assistant access to Footprinter's indexed data. Supports metadata search, entity queries, and content reads — all gated by a two-layer access control model (visibility + access). Designed for Claude Desktop integration.

## Setup

Install Footprinter, then configure MCP for Claude Desktop:

```bash
fp setup mcp --claude   # Auto-configure Claude Desktop (creates backup)
fp setup mcp            # Print the JSON snippet to add manually
```

Or add to `~/Library/Application Support/Claude/claude_desktop_config.json` manually:

```json
{
  "mcpServers": {
    "footprinter": {
      "command": "fp-mcp"
    }
  }
}
```

## Available Tools

### Discovery

| Tool | Description |
|------|-------------|
| `footprinter_status` | List all indexed data sources with record counts, update times, and summary statistics |

### Search

| Tool | Description |
|------|-------------|
| `footprinter_search` | Cross-source keyword search (files, emails, chats, browser) with source-specific filters. Returns metadata only. |

**Parameters:**
- `query` — Search term
- `sources` — Which sources to search (default: all)
- `project` / `client` — Filter by project or client name
- `account` — Filter by account (applies to emails and files)
- `sender` — Partial match on email sender (emails only)
- `days_back` — Only emails from last N days (emails only)
- `folder` — Path prefix filter (files only)
- `mime_type` — Exact MIME type filter (files only)
- `date_from` / `date_to` — Date range filters
- `limit` — Max results per source (default: 50)

### Entity Queries

| Tool | Description |
|------|-------------|
| `footprinter_project` | Get project metadata, file counts, and top content types |
| `footprinter_client` | Get client info with all projects and aggregate stats |
| `footprinter_folder` | Get folder contents, subfolders, and metadata |

### Semantic Search

| Tool | Description |
|------|-------------|
| `footprinter_semantic` | Find semantically similar chats and/or files (hybrid vector + FTS5 search) |

**Parameters:**
- `query` — Natural language search query (minimum 3 characters)
- `source` — Which collection(s) to search: "chats", "files", or "all" (default: "all")
- `limit` — Max results per collection (default: 10)

### Content Access

| Tool | Description |
|------|-------------|
| `footprinter_read` | Read file/email/chat content with permission enforcement |

**Permission Enforcement:**
- Checks the permission hierarchy before serving content
- Returns metadata even when access is denied
- Supports local files (read from disk) and Drive files (download via API)

## Visibility Layer

Before checking permissions, MCP enforces a visibility layer that controls whether items appear in results and how much metadata is exposed.

**Visibility States:**

| State | In Results? | Metadata Exposed | Content Readable? |
|-------|-------------|------------------|-------------------|
| `hidden` | No (excluded) | None | No |
| `opaque` | Yes (minimal) | files: `id`, `content_type`, `source`, `project_id` (other entity types expose their own minimal set — see `reference/permission-policies-and-access-control.md`) | No |
| `full` | Yes (full) | All fields | Yes (if permitted) |

**Resolution:** Policies in `visibility_policies` are the source of truth. The recalculation engine (`footprinter/access_stamper.py`) resolves policies per entity using most-restrictive-wins semantics (`hidden` > `opaque` > `full`) and writes cached values to `visibility` columns. MCP tools read these cached columns at query time — no live policy resolution during requests.

**Scope hierarchy** (checked in order, most-restrictive-wins):
- Files: `file:{id}` → folder prefix → folder FK → `project:{id}` → `client:{id}` → `source:files` → `global`
- Emails: `email:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:emails` → `global`
- Chats: `chat:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:chats` → `global`
- Baseline (no policies match): `BASELINE_VISIBILITY = 'opaque'`

**Note:** Browser history uses source-level policy only—there is no item-level or folder hierarchy for browser history entries.

## Permission Hierarchy

All tools returning content enforce a two-layer security model. `footprinter_read` blocks the entire response when permission is denied. `footprinter_search` strips content fields (snippets, summaries) while preserving metadata so the item still appears in results. `footprinter_semantic` excludes denied items entirely — semantic matches are content-derived, so presence in results reveals content (see decision D2).

1. **Visibility check** (above) — determines if item is accessible at all
2. **Permission check** (below) — determines if content can be read

**Permission Resolution:** Policies in `permission_policies` are the source of truth, resolved and cached to `access` columns by the same recalculation engine. Deny-wins semantics: if ANY matching policy is `deny`, access is blocked regardless of other allow rules.

**Scope hierarchy** (checked in order, deny-wins):
- Files: `file:{id}` → folder prefix → `project:{id}` → `client:{id}` → `source:files` → `global`
- Emails: `email:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:emails` → `global`
- Chats: `chat:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:chats` → `global`
- Baseline (no policies match): `BASELINE_PERMISSION = True` (allow)

**Note:** Browser history uses source-level policy only—there is no item-level or folder hierarchy for browser history entries.

## Error Codes

MCP tools return specific error codes for access control:

| Code | Meaning | When Returned |
|------|---------|---------------|
| `NOT_FOUND` | Item is hidden | Item's visibility resolves to `hidden` |
| `VISIBILITY_RESTRICTED` | Item is opaque | Item's visibility resolves to `opaque` (returns minimal metadata) |
| `PERMISSION_DENIED` | Read access denied | Item is visible but its resolved `access` is `deny` |

## Module Structure

```
footprinter/mcp/
├── __init__.py
├── __main__.py      # Entry point
├── server.py        # FastMCP server setup, tool registration
├── db.py            # Database connection helper
├── errors.py        # Error codes and helpers
├── extraction.py    # Parameter extraction utilities
└── tools/
    ├── __init__.py
    ├── status.py      # footprinter_status
    ├── search.py      # footprinter_search
    ├── navigation.py  # footprinter_project, footprinter_client, footprinter_folder
    ├── semantic.py    # footprinter_semantic
    └── read.py        # footprinter_read
```

## Running Standalone

```bash
fp-mcp
```
