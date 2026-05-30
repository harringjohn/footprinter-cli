# Interfaces

How to access Footprinter data. Covers the four entry points, the service layer that connects them, and the role model that controls what each caller can see and do.

---

## Entry Points

Footprinter exposes four interfaces. All four converge on the same service layer — the only difference is the transport.

| Entry point | Transport | Default role | Audience | When to use |
|---|---|---|---|---|
| **CLI** (`fp`) | Terminal commands | ADMIN | Local user | Interactive data management, pipeline runs, setup |
| **MCP** | MCP protocol (STDIO) | VIEWER | AI agents (Claude Desktop, Cursor, etc.) | AI-assisted queries, context retrieval |
| **HTTP API** | FastAPI on localhost | ADMIN (configurable) | Apps, scripts, non-Python consumers | Programmatic access from any language |
| **Python import** | Direct function calls | Caller's choice | Developers building on Footprinter | Maximum control, full type information, no transport overhead |

### CLI

The `fp` command is installed by `pip install footprinter-cli`. All business logic is in the service layer — the CLI handles argument parsing and output formatting.

| Command | Purpose |
|---------|---------|
| `fp setup` | Configuration wizard and system setup |
| `fp ingest` | Run the indexing pipeline |
| `fp status` | Show data counts and system health |
| `fp search` | Search across indexed content |
| `fp connect` | Manage optional integrations |
| `fp view` | Browse indexed data (files, folders, projects, clients, chats, emails, visits) |
| `fp add` | Create new entity records |
| `fp update` | Update existing entity records by ID |
| `fp upsert` | Create or update records, assign relationships, or soft-delete via `--status removed` |
| `fp data` | Import metadata corrections from CSV |
| `fp delete` | Hard-delete a super entity (irreversible) |
| `fp permission` | Manage visibility and access policies |
| `fp mcp` | Start the MCP server |
| `fp api` | Start the HTTP API server |
| `fp doctor` | Check installation health, rebuild search indexes and vector store |
| `fp uninstall` | Remove Footprinter (MCP entry, user data, package) |

Run `fp <command> --help` for full signatures and arguments.

#### Data scoping operations

Three commands cover the lifecycle of indexed entities. They look similar but have different effects — pick the one that matches your intent.

| Operation | Command | What it does | Reversible? |
|-----------|---------|--------------|-------------|
| **Create / edit** super entity | `fp upsert client --name Acme --type external` | Creates or updates a client/project record. `--status` accepts `listed` / `unlisted` / `removed`. | Yes |
| **Assign** content entity | `fp upsert file 42 --project-id 3` | Sets `project_id` / `client_id` FKs on files, folders, emails, chats, or visits. Does not change `status`. Bulk path form: `fp upsert files --folder /path --project-id 3`. | Yes — re-assign or pass `0` to clear |
| **Soft-delete** | `fp upsert client 42 --status removed` | Hides the record from default listings (`default_exclude=["removed"]`) but preserves the row and FK references. | Yes — `--status listed` to restore |
| **Hard-delete** | `fp delete client 42` | `DELETE FROM clients WHERE id = 42`. Refuses to run when any dependent record (project, file, folder, etc.) points at the entity — reassign or remove those first. | **No** |

Listings everywhere use the standardized exclude pattern via `build_status_filter()`: by default `removed` is hidden, all other statuses are visible. Pass `--status all` (or `status="all"` in service calls) to bypass; pass an explicit status to filter to it.

#### Dependency groups

| Group | What it enables |
|-------|-----------------|
| *(base)* | Core indexing, CLI formatting |
| `mcp` | MCP server for AI assistants |
| `semantic` | Semantic vector search (ChromaDB + ONNX) |
| `docs` | PDF, Word, Excel, PowerPoint content extraction |
| `api` | HTTP API server (FastAPI + Uvicorn) |
| `full` | All optional extras (semantic + docs + mcp + api) |
| `dev` | pytest, ruff, httpx |

Additional data sources are installed as connector packages via `fp connect install`, not pip extras.

### MCP

The MCP server gives AI assistants (Claude Desktop, Claude Code) structured access to your indexed data. It passes `Role.VIEWER` — read-only access with visibility filtering. Start it via `fp mcp` (usually spawned automatically by the AI client).

#### Setup

```bash
fp setup mcp --claude    # Write MCP config into Claude Desktop
fp setup mcp --check     # Verify configuration
```

#### Tools

| Tool | Description |
|------|-------------|
| `footprinter_status` | Aggregate data counts and system health |
| `footprinter_search` | Full-text keyword search across all entity types |
| `footprinter_semantic` | Semantic similarity search (requires `[semantic]` extras) |
| `footprinter_project` | Project metadata and linked entities |
| `footprinter_client` | Client metadata and linked projects |
| `footprinter_folder` | Folder metadata and contents |
| `footprinter_read` | Read file content (subject to permission checks) |

All tools respect the two-layer access control model (visibility + permissions). See [mcp-access-control.md](mcp-access-control.md) for the full security model.

### HTTP API

FastAPI routers on localhost. Thin HTTP translation: parse request, call service, return JSON. No HTML, no templates.

```bash
# Start the HTTP API server
fp api
```

### Python Import

Import `footprinter.services` directly. This is the most powerful interface — full type information, no serialization overhead, direct access to service contracts.

```python
from footprinter.cli._common import connect_db
from footprinter.paths import get_db_path
from footprinter.services import file_service
from footprinter.services.roles import Role

conn = connect_db(get_db_path())
result = file_service.list_(conn, role=Role.ADMIN, limit=10)
for f in result["files"]:
    print(f["name"], f["path"])
conn.close()
```

This is the documented public API. The rest of this document covers its contracts in detail.

---

## Layer Model

Every entry point is a thin translator between its transport and the service layer. The service layer is the single integration point.

```
                ┌─ CLI            (terminal I/O → services)
                │
Entry points:   ├─ MCP            (MCP protocol → services)
                │
                ├─ HTTP / FastAPI  (HTTP on localhost → services)
                │
                └─ Python import   (direct function calls → services)
                       │
                       ▼
Service layer:  services/  (business logic, roles, orchestration)
                 │              │
                 ▼              ▼
Data access:    db/          semantic/
               (all SQL)    (all vector ops)
                 │              │
                 ▼              ▼
Storage:      SQLite        ChromaDB
```

**Rules:**

- Entry points call `services/`. They never call `db/` or `semantic/` directly.
- Services orchestrate business logic, apply role-based filtering, and delegate data access to `db/` (SQL) and `semantic/` (vector operations).
- `db/` functions execute SQL and return plain dicts. Services call these; entry points do not.
- `semantic/` owns all vector operations (embeddings, ChromaDB queries, hybrid search).

---

## Role Model

The `Role` enum (`footprinter.services.roles.Role`) determines what a caller can see and do.

| Role | Write access | Sees all metadata | Used by |
|---|---|---|---|
| `Role.ADMIN` | Yes | Yes | CLI, HTTP API |
| `Role.VIEWER` | No | No (filtered) | MCP |

**Properties:**

- `role.can_write` — `True` for ADMIN, `False` for VIEWER. Services raise `PermissionError` on write attempts with a non-write role.
- `role.sees_all` — `True` for ADMIN, `False` for VIEWER. When `False`, visibility filtering applies.

### Visibility Filtering (VIEWER)

Items have a `visibility` column that controls what VIEWER sees:

| Visibility | Effect |
|---|---|
| `hidden` | Item excluded entirely from results |
| `opaque` | Minimal fields only (id, type, source) |
| `visible` | Full metadata returned |

Items also have a `access` column for content access:

| Permission | Effect |
|---|---|
| `allow` | Content fields included |
| `deny` | Content fields stripped; item still appears in results |

The special value `inherit` resolves to the global policy at query time. See [mcp-access-control.md](mcp-access-control.md) for the full model.

---

## Service Contracts

Most services live in `footprinter.services` and can be imported from the package. The exception is `IngestService`, which requires a direct module import (see [ingest_service](#ingest_service-class-based) below).

```python
from footprinter.services import file_service, search_service
from footprinter.services.roles import Role
```

### Signature Pattern

Every service function follows the same convention:

```python
def function_name(conn: sqlite3.Connection, *, role: Role = Role.ADMIN, ...) -> dict:
```

- `conn` — SQLite connection as the first positional argument
- `role` — keyword argument, defaults to `Role.ADMIN`
- Returns plain `dict` (no ORM objects, no custom classes)
- Filters and options are keyword-only arguments

### Entity Services

Seven entity services follow a consistent pattern. Each provides `get()`, `list_()`, and `assign()`. Some add entity-specific operations.

#### file_service

```python
file_service.get(conn, file_id: int, *, role) -> dict | None
file_service.list_(conn, *, role, project_id=None, source=None, status=None,
                   content_type=None, limit=50, page=1) -> dict
file_service.assign(conn, file_id: int, *, role, project_id=None, client_id=None) -> dict | None
```

- `list_()` returns `{"files": [...], "total": int, "page": int, "suppressed": int}`
- `assign()` raises `PermissionError` if role cannot write

#### email_service

```python
email_service.get(conn, email_id: int, *, role) -> dict | None
email_service.list_(conn, *, role, account=None, client_id=None, project_id=None,
                    query=None, has_attachments=None, sort_by="received_at",
                    order="desc", limit=50, page=1) -> dict
email_service.assign(conn, email_id: int, *, role, project_id=None, client_id=None) -> dict | None
```

#### chat_service

```python
chat_service.get(conn, chat_id: int, *, role) -> dict | None
chat_service.list_(conn, *, role, account=None, query=None, sort_by="modified_at",
                   order="desc", status=None, limit=50, page=1) -> dict
chat_service.assign(conn, chat_id: int, *, role, project_id=None, client_id=None) -> dict | None
```

#### visit_service

```python
visit_service.get(conn, entry_id: int, *, role) -> dict | None
visit_service.list_(conn, *, role, limit=50, page=1) -> dict
visit_service.assign(conn, entry_id: int, *, role, project_id=None, client_id=None) -> dict | None
```

#### folder_service

```python
folder_service.get(conn, folder_id: int, *, role) -> dict | None
folder_service.list_(conn, *, role, project_id=None, depth=1, include_hidden=False,
                     sort_by="size", limit=50, page=1) -> dict
folder_service.get_by_path(conn, path: str, *, role) -> dict | None
folder_service.assign(conn, folder_id: int, *, role, project_id=None, client_id=None) -> dict | None
```

- `get_by_path()` returns navigation data (files, subfolders, recursive count) for visible folders

#### project_service

```python
project_service.get(conn, project_id: int, *, role, include=None) -> dict | None
project_service.list_(conn, *, role, include=None, status=None, client=None,
                      project_type=None, limit=50, page=1) -> dict
project_service.resolve_by_name(conn, name: str, *, role) -> dict | None
project_service.upsert(conn, *, project_name: str, role, root_path=None, client_id=None,
                        project_type=None, description=None, github_url=None,
                        status=None, status_reason=None) -> dict
project_service.delete(conn, project_id: int, *, role) -> dict | None
```

- `include` accepts `["files"]` and/or `["folders"]` to attach nested data
- `resolve_by_name()` returns navigation data for a single match, disambiguation dict for multiple
- `upsert()` matches on `root_path` first, then `project_name`
- `delete()` is a hard delete (`DELETE FROM projects`); raises `ValueError` if dependent records exist. Use `upsert(... status='removed', status_reason='cli:delete')` for a soft-delete.

#### client_service

```python
client_service.get(conn, client_id: int, *, role, include=None) -> dict | None
client_service.list_(conn, *, role, include=None, status=None, limit=50, page=1) -> dict
client_service.resolve_by_name(conn, name: str, *, role) -> dict | None
client_service.upsert(conn, *, name: str, client_type: str, role, path_pattern=None,
                       status=None, status_reason=None, slug=None) -> dict
client_service.delete(conn, client_id: int, *, role) -> dict | None
```

- `include` accepts `["projects"]` and/or `["aggregates"]`

### Search Services

#### search_service

```python
search_service.search(conn, *, role, query="", sources=None, project=None, client=None,
                      date_from=None, date_to=None, limit=50, account=None,
                      sender=None, days_back=None, folder=None, mime_type=None) -> dict
```

Multi-source keyword search. Searches across files, emails, chats, and browser history.

- `sources` — list of source names to search. Defaults to `["files", "emails", "chats", "browser"]`
- Returns `{"files": [...], "emails": [...], "chats": [...], "browser": [...], "suppressed": int}`
- VIEWER: hidden items excluded, content stripped for permission-denied items

#### semantic_service

```python
semantic_service.semantic_search(conn, query: str, *, role, source="all", limit=10) -> dict
```

Embedding-based semantic search with FTS5 keyword fallback.

- `source` — `"all"`, `"chats"`, or `"files"`
- Returns `{"query": str, "chats": [...], "files": [...], "summary": str}`
- Falls back to FTS5 keyword search if the vector store is unavailable (returns `note` field explaining degraded results)

### Infrastructure Services

#### status_service

```python
status_service.get_status(conn, *, role) -> dict
```

System status aggregates. ADMIN gets full system status including config checks. VIEWER gets MCP-oriented counts with hidden data excluded.

#### access_service

```python
access_service.gate_access(conn, item_type: str, item_id: int, *, role) -> dict
```

Three-stage access gating for a single item. Used internally by MCP tools before reading content.

- `item_type` — `"file"`, `"email"`, or `"chat"`
- Returns on success:
  - Files: `{"status": "ok", "metadata": {...}}`
  - Emails: `{"status": "ok", "metadata": {...}, "content": str}`
  - Chats: `{"status": "ok", "metadata": {...}, "content": str}`
- Other statuses:
  - `"hidden"` — `{"status": "hidden"}` (item hidden from this role)
  - `"opaque"` — `{"status": "opaque", "metadata": {...}}` (minimal fields only)
  - `"denied"` — `{"status": "denied", "metadata": {...}}` (permission denied, opaque metadata included)
  - `"not_found"` — `{"status": "not_found"}`
  - `"invalid_type"` — `{"status": "invalid_type"}`

#### content_service

```python
content_service.read_file(conn, metadata: dict, *, format="text") -> dict
```

Read file content from local disk or remote storage. Requires metadata from a prior `gate_access()` call.

- `format` — `"text"` (with extraction for PDF, DOCX, etc.) or `"raw"`
- Returns `{"status": "ok", "content": str, "metadata": {...}}` on success

#### ingest_service (class-based)

Unlike the other services, `IngestService` is not exported from the `footprinter.services` package — import it directly from the module:

```python
from footprinter.services.ingest_service import IngestService

svc = IngestService(conn, get_db=None)

svc.begin(pipe, mode=None, trigger=None) -> int           # start tracking an ingest run
svc.complete(ingest_id, result=None, metadata=None)        # mark completed
svc.fail(ingest_id, error)                                 # mark failed
svc.last_run(pipe) -> datetime | None                      # most recent completion time
svc.run_pipe(pipe, *, mode, trigger, runner, on_progress)  # wrap a single pipe run
svc.run_pipes(pipes, *, runner, full_mode=False,            # batch run with FTS optimization
              on_pipe_start=None, on_pipe_end=None,
              on_progress=None, pipe_hook=None) -> list[dict]
svc.history(pipe, limit=20) -> list[dict]                  # recent ingest records
```

`IngestService` holds a connection and manages ingest lifecycle state. The optional `get_db` callable enables FTS trigger optimization during batch runs.

---

## Python API

### 1. List files by project

```python
from footprinter.cli._common import connect_db
from footprinter.paths import get_db_path
from footprinter.services import file_service
from footprinter.services.roles import Role

conn = connect_db(get_db_path())
result = file_service.list_(conn, role=Role.ADMIN, project_id=42, limit=20)

print(f"Found {result['total']} files")
for f in result["files"]:
    print(f"  {f['name']}  ({f['content_type']})")

conn.close()
```

### 2. Semantic search across chats and files

```python
from footprinter.cli._common import connect_db
from footprinter.paths import get_db_path
from footprinter.services import semantic_service
from footprinter.services.roles import Role

conn = connect_db(get_db_path())
result = semantic_service.semantic_search(
    conn, "quarterly review", role=Role.VIEWER, source="all", limit=5,
)

print(result["summary"])
for chat in result.get("chats", []):
    print(f"  Chat: {chat.get('chat_title', '(restricted)')}")
for f in result.get("files", []):
    print(f"  File: {f.get('name', '(restricted)')}")

conn.close()
```

### 3. Build a report: all files across active projects

```python
from footprinter.cli._common import connect_db
from footprinter.paths import get_db_path
from footprinter.services import project_service, file_service
from footprinter.services.roles import Role

conn = connect_db(get_db_path())

projects = project_service.list_(conn, role=Role.ADMIN, status="active")
for proj in projects["projects"]:
    files = file_service.list_(conn, role=Role.ADMIN, project_id=proj["id"])
    print(f"\n{proj['name']} — {files['total']} files")
    for f in files["files"][:5]:
        print(f"  {f['name']}")

conn.close()
```

### 4. Keyword search with filters

```python
from footprinter.cli._common import connect_db
from footprinter.paths import get_db_path
from footprinter.services import search_service
from footprinter.services.roles import Role

conn = connect_db(get_db_path())
result = search_service.search(
    conn,
    role=Role.ADMIN,
    query="invoice",
    sources=["files", "emails"],
    date_from="2026-01-01",
    client="acme",
)

for f in result.get("files", []):
    print(f"  File: {f['name']}")
for e in result.get("emails", []):
    print(f"  Email: {e['subject']}")

conn.close()
```

---

## Connection Setup

All service functions take a `sqlite3.Connection` as their first argument. Use `connect_db()` to get a properly configured connection:

```python
from footprinter.cli._common import connect_db
from footprinter.paths import get_db_path

conn = connect_db(get_db_path())
# ... use services ...
conn.close()
```

`connect_db()` returns a connection with:
- `row_factory = sqlite3.Row` (dict-like row access)
- `PRAGMA busy_timeout=5000` (wait up to 5s for locks)
- `PRAGMA foreign_keys=ON`

Returns `None` if the database file does not exist. Call `fp setup` and `fp ingest` to initialize.

---

## Related Documentation

- [mcp-access-control.md](mcp-access-control.md) — MCP security model and access control
- [data-model.md](data-model.md) — Database schema reference
- [pipeline.md](pipeline.md) — Data pipeline stages and configuration
