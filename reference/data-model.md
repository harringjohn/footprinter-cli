# Data Model Reference

Current database schema for Footprinter. The database is SQLite, stored at `data/footprinter.db`.

---

## Data Privacy & Storage Model

Footprinter's database is a **metadata catalog by default**, not a content store. File content lives at its source — on the local filesystem, or in a remote store accessed via a connector plugin — and is not copied into the database unless you opt in. Two opt-in features (`content_snippets` and the Gmail connector's `body_preview`) store short previews; vector embeddings live in a separate ChromaDB store. See `content-storage.md` for the three-tier model. The database otherwise records *what exists and where*, not *what it says*.

### What's stored

| Data | Stored? | Details |
|------|---------|---------|
| File paths, names, sizes, timestamps | Yes | Core catalog metadata |
| Full file content | **No** | Content stays at its source (disk or remote store) |
| Content preview | Opt-in | First ~1000 characters of text-readable files, when `indexing.content_snippets: true` (default `false`). Used for keyword-search snippets. See `content-storage.md` for the three-tier storage model. |
| Content hashes | Yes | Fixed-length fingerprints (see below) — not reversible to content |
| Email subjects, senders, labels | Yes | Metadata from email connector plugins |
| Email body | Partial | `body_preview` — first portion only |
| Chat messages | Yes | Imported conversation content (messages table) |
| Browser URLs and titles | Yes | No page content |

### Content hashes are fingerprints, not encodings

The `files` table has two hash columns. Both are **one-way cryptographic digests** — fixed-length outputs that identify a file but contain zero recoverable content. You cannot reconstruct a file from its hash.

| Column | Algorithm | Output | Purpose |
|--------|-----------|--------|---------|
| `sha256_hash` | SHA-256 | 64 hex characters | Duplicate detection across local files |
| `md5_hash` | MD5 | 32 hex characters | Local↔remote file matching (e.g. the Google Drive API returns MD5) |

Both are computed by reading the file in chunks and feeding each chunk through the hash algorithm (`footprinter/utils/hash_utils.py`). A 10-byte file and a 10 GB file both produce the same fixed-length output. Two files with identical content produce identical hashes; any single-byte difference produces a completely different hash.

**How they're used:**
- **`sha256_hash`** answers: "Have I already indexed a file with identical content?" — deduplication within the catalog.
- **`md5_hash`** answers: "Does this local file match a remote file?" — remote APIs commonly return an MD5 for each file (the Google Drive API's `md5Checksum` is one example), so computing the same hash locally enables hash-based linking without downloading or comparing content.

### Local-only architecture

The database never leaves the local machine:

- **SQLite on disk** — a single file at `data/footprinter.db`. No network server, no cloud sync, no remote database.
- **No telemetry** — Footprinter does not phone home or transmit catalog data.
- **Connector plugins are read-in** — a connector (e.g. `footprinter-google` for Drive/Gmail) reads metadata from its source API to populate the catalog. The tool never writes catalog data back out to the source.

### AI access control

When Footprinter exposes data to AI assistants via MCP (Model Context Protocol), a three-layer access control model governs what the AI can see:

0. **Status filtering** — data lifecycle layer (not security). Only `listed` items pass through for VIEWER callers. `unlisted` and `removed` items are excluded. ADMIN callers bypass this layer but can opt in to non-listed items via `include_unlisted`/`include_removed` params.

1. **Visibility** — controls whether an item appears in results at all:
   - `hidden` — item doesn't exist to the AI (excluded from all results)
   - `opaque` — minimal metadata only (id, type, source)
   - `visible` — full metadata returned

2. **Permissions** — controls whether content can be read (only evaluated for visible items):
   - `allow` — content readable (e.g., `content_preview`, search snippets)
   - `deny` — metadata visible but content blocked

Layers 1–2 use **most-restrictive-wins** / **deny-wins** semantics. Policies are set via `fp mcp view set` and `fp mcp read set` at any granularity: global, per-source, per-account, per-folder path, per-project, per-client, or per-item.

See `reference/mcp-access-control.md` for the full reference: the three-layer model, policy tables, scope patterns, resolution semantics, CLI management, and common patterns.

---

## Schema Overview

Super entities are marked with `[S]`, content entities with `[C]`.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CORE TABLES                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐         ┌──────────────────┐       ┌─────────────┐   │
│  │ projects [S] │◄────────│    files [C]     │──────►│ folders [S] │   │
│  │              │         │                  │       │             │   │
│  │  id          │         │  id              │       │             │   │
│  │  project_name│         │  source          │       │  id         │   │
│  │  root_path   │         │  name, path      │       │  path       │   │
│  │  client      │         │  project_id ─────┼──────►│  project_id │   │
│  │  ...         │         │  folder_id ──────┼──────►│  parent_    │   │
│  └──────────────┘         │                  │       │  folder_id  │   │
│                           │  ...              │       │  ...        │   │
│                           └──────────────────┘       └─────────────┘   │
├─────────────────────────────────────────────────────────────────────────┤
│                        CONTENT TABLES                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────┐  ┌──────────────┐  ┌────────────────────────┐   │
│  │  visits [C]     │  │ emails [C]  │  │      chats [C]         │   │
│  │                  │  │              │  │                        │   │
│  │  url, title      │  │  message_id  │  │  external_id           │   │
│  │  visit_time      │  │  subject     │  │  title                  │   │
│  │  browser         │  │  from_addr   │  │  source (claude/gpt)   │   │
│  └──────────────────┘  │  account     │  │                        │   │
│                        └──────────────┘  │        ▲               │   │
│                                          │        │               │   │
│                                          │  ┌─────┴──────────┐   │   │
│                                          │  │ messages [C]   │   │   │
│                                          │  │                │   │   │
│                                          │  │ chat_id        │   │   │
│                                          │  │ role, content  │   │   │
│                                          │  └────────────────┘   │   │
│                                          └────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────────┤
│                           SUPPORT TABLES                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  clients [S]              (client grouping, FK target for projects)     │
│  sources                  (runtime source registry)                     │
│  ingests                  (per-pipe run history & watermarks)           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Entity Architecture: Super Entities & Content Entities

Footprinter's entity tables fall into two tiers based on how they are created, managed, and how they relate to each other.

### Super Entities

**Tables:** `projects`, `clients`, `folders`

Organizational containers that group and influence content entities.

| Aspect | Detail |
|--------|--------|
| **Created by** | User via `fp upsert` (projects, clients) or pipeline-discovered (folders) |
| **Status management** | User-controlled via `fp upsert --status` (soft-delete) or `fp delete` (hard delete) |
| **Influence on children** | Scope-based policy propagation (`project:{id}`, `client:{id}`, `folder:{path}`) affects children's visibility and permissions |
| **Status cascade** | Status does **not** cascade to children — only policies propagate |
| **Child references** | Children reference super entities via FK columns (`project_id`, `client_id`, `folder_id`) |

### Content Entities

**Tables:** `files`, `emails`, `chats`, `visits`, `messages`

Data items discovered by the ingest pipeline and categorized by the user.

| Aspect | Detail |
|--------|--------|
| **Created by** | Pipeline-discovered during `fp ingest` |
| **Status management** | Pipeline-managed (`_determine_file_status`, `mark_removed_files`) — not directly by user |
| **User interaction** | Categorized via `fp upsert <entity> <id> --project-id <n>` (sets `project_id`/`client_id` FK) |
| **Visibility resolution** | Resolved through the scope hierarchy of their parent super entities |

### Folders: A Special Super Entity

Folders occupy a unique position — they are both structural (filesystem hierarchy) and organizational (content grouping):

| Aspect | Detail |
|--------|--------|
| **Structural role** | Tree hierarchy via `parent_folder_id` — represents the filesystem or remote folder structure |
| **Organizational role** | Groups files via `folder_id` FK on the `files` table |
| **Cascade operations** | `cascade_project_id()` and `cascade_client_id()` propagate assignments to descendant folders + their files |
| **Policy scoping** | Supports path-scoped policies (`folder:/path` prefix matching) — unique among super entities |
| **Status behavior** | Status does **not** cascade to children (same as projects/clients) |

### CLI Verbs

A handful of CLI verbs cover the lifecycle of every entity. Mirrors the data scoping operations table in [interfaces.md](interfaces.md#data-scoping-operations).

| Verb | Applies to | Effect |
|------|-----------|--------|
| `fp upsert` | Super entities (projects, clients) | Create or edit the entity itself. `--status removed` for soft-delete. Example: `fp upsert client --name Acme --type external`. |
| `fp upsert` | Content entities (files, emails, chats, visits) and folders | Set `project_id`/`client_id` FK — categorizes without changing status. Example: `fp upsert file 42 --project-id 3`. Bulk path form: `fp upsert files --folder /path --project-id 3`. Folders accept assignment despite being super entities (see [Folders: A Special Super Entity](#folders-a-special-super-entity)). |
| `fp delete` | Super entities only | Hard `DELETE FROM` — permanently removes the record. Refuses when dependent rows exist. |

The CLI `fp upsert` for content entities delegates to service-layer `assign()` methods (`file_service.assign()`, `folder_service.assign()`, etc.) — useful to know when navigating the codebase, but `assign` is not itself a CLI subcommand.

---

## Schema Layers

The database schema is organized into four layers, each with a distinct owner and initialization path.

| Layer | Owner | Ships in Tool? | Purpose |
|-------|-------|----------------|---------|
| **Standard** | Tool v1.0 | Yes | All entity tables, infrastructure tables, FTS indexes, policy tables |
| **Migration** | Dev repo | No | Upgrades databases created under older schema versions |
| **App** | Future extension | No | Additional columns and tables for future features |
| **Connector** | Individual connectors | No (installed separately) | Connector-specific columns on standard tables |

**Standard** is the foundational schema. A fresh `pip install footprinter-cli && fp setup && fp ingest` produces a working database using only this layer. Initialized by `init_db()` in `schema.py`.

**Migration** upgrades databases created under older schema versions to the current standard. Not needed for the initial public release.

**App** adds columns and tables for future features via `ALTER TABLE` after standard schema initialization.

**Connector** adds columns for connector-specific data that doesn't fit standard entity columns. See [Connector Schema Extensions](#connector-schema-extensions) below.

---

## Standard Entity Column Set

All 8 entity tables (files, folders, visits, projects, chats, messages, emails, clients) share a standard baseline:

| Column | Type | Default | Constraint | Purpose |
|--------|------|---------|------------|---------|
| `id` | INTEGER | — | PRIMARY KEY AUTOINCREMENT | Row ID |
| `status` | TEXT | `'listed'` | CHECK (status IN ('listed', 'unlisted', 'removed')) | Lifecycle state |
| `created_at` | DATETIME | CURRENT_TIMESTAMP | — | Record creation time |
| `display_name` | TEXT | — | — | Uniform display label (auto-populated via trigger) |
| `mcp_read` | TEXT | `'inherit'` | CHECK (mcp_read IN ('allow', 'deny', 'inherit')) | MCP read access |
| `mcp_view` | TEXT | `'inherit'` | CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')) | MCP visibility |

All entity tables share this same status CHECK constraint — there are no per-table extensions.

Data-source entities (files, folders, emails, chats, visits, messages) also carry:

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `indexed_at` | DATETIME | CURRENT_TIMESTAMP | When first ingested (immutable) |
| `updated_at` | DATETIME | CURRENT_TIMESTAMP | When last re-processed |
| `project_id` | INTEGER | — | FK to `projects` |
| `client_id` | INTEGER | — | FK to `clients` |

**Note:** `messages` inherits project/client from its parent `chats` record — it does not carry `project_id` or `client_id` directly.

### App-scope columns in standard schema

Three columns are defined in standard `CREATE TABLE` statements but only populated by app-scope code. Tool-only installs leave them NULL:

---

## display_name Convention

Every entity table has a `display_name` column auto-populated by an `AFTER INSERT` trigger. This provides a uniform label for display across entity types without requiring callers to know which source column to use.

| Table | Source expression | Example |
|-------|-------------------|---------|
| files | `name` | `report.pdf` |
| folders | `name` | `documents` |
| visits | `title` | `GitHub - anthropics/claude-code` |
| projects | `project_name` | `footprinter` |
| chats | `title` | `Database schema discussion` |
| messages | `SUBSTR(content, 1, 100)` | `Can you help me understand...` |
| emails | `subject` | `Re: Project update` |
| clients | `name` | `Acme Corp` |

The trigger fires only when `display_name IS NULL` on insert, so explicit values are preserved.

---

## Timestamp Contract

All indexed entity tables follow a two-axis timestamp convention:

### Origin timestamps

When the thing happened in the real world. These are domain-specific and may use domain names.

| Table | Column | Meaning |
|-------|--------|---------|
| files | `created_at`, `modified_at`, `accessed_at` | Filesystem timestamps from the source |
| folders | `created_at` | When the folder was first seen |
| visits | `visit_time` | When the page was visited in the browser |
| emails | `received_at` | When the email arrived in the mailbox |
| chats | `created_at`, `modified_at` | When the chat was created/last updated in the source |
| messages | `created_at` | When the message was sent |

### Audit timestamps

When Footprinter indexed or processed the record. Consistent naming across all tables.

| Column | Meaning | Behavior |
|--------|---------|----------|
| `indexed_at` | First time Footprinter ingested this record | Immutable — set once on INSERT, never updated |
| `updated_at` | Last time Footprinter re-processed this record | Refreshed on every UPDATE/upsert |

All 6 data-source entity tables (files, folders, visits, emails, chats, messages) have both `indexed_at` and `updated_at`.

### Format

All timestamps are stored in UTC, ISO 8601 format. Python code uses `utc_now_iso()` from `footprinter/utils/time.py` to generate consistent `YYYY-MM-DDTHH:MM:SS+00:00` strings. SQL-generated timestamps use `CURRENT_TIMESTAMP` (produces `YYYY-MM-DD HH:MM:SS`).

---

## Connector Schema Extensions

Connectors can declare additional columns on core tables via `ConnectorSpec.schema_extensions`. These columns are added at startup using idempotent `ALTER TABLE ADD COLUMN`.

### When to use columns vs. metadata JSON

| Use a dedicated column when | Use `metadata` JSON when |
|-----------------------------|--------------------------|
| The value is queried in WHERE/JOIN clauses | The value is opaque or rarely accessed |
| The value needs an index for performance | The value varies per-record in unpredictable ways |
| The value is a scalar (TEXT, INTEGER, etc.) | The value is structured or nested |

### How connectors declare extensions

In `ConnectorSpec`, set `schema_extensions` to a dict mapping table names to column definitions:

```python
ConnectorSpec(
    ...
    schema_extensions={
        "folders": [("web_link", "TEXT")],
    },
)
```

On startup, `init_connector_schemas(conn)` iterates installed connectors and calls `register_connector_schema()` for each one with extensions. The helper uses `ALTER TABLE ... ADD COLUMN` with a try/except guard — if the column already exists (from a previous run or an older schema), the exception is caught and ignored.

### Current extensions

| Connector | Table | Column | Type | Purpose |
|-----------|-------|--------|------|---------|
| Google | folders | web_link | TEXT | Drive web view URL for remote folders |

---

## Core Tables

### files

The primary table for all indexed files. Stores both local files and remote files from connector plugins, unified by the `source` field.

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `source` | TEXT | `'local'` for filesystem files; connector plugins set their own source string (e.g. the Google connector uses `'workdrive'` and `'personaldrive'`) |
| `external_id` | TEXT | Source-system ID for remote files (e.g. Drive file ID) |
| `account` | TEXT | Account name on the source system (connector-provided) |
| `name` | TEXT | File name |
| `path` | TEXT | Full path (local) or source-system path (remote) |
| `content_type` | TEXT | File extension |
| `mime_type` | TEXT | MIME type (e.g., `text/plain`, `image/png`) |
| `size_bytes` | INTEGER | File size |
| `created_at` | DATETIME | File creation time |
| `modified_at` | DATETIME | Last modification |
| `accessed_at` | DATETIME | Last access time |
| `indexed_at` | DATETIME | When indexed (default: CURRENT_TIMESTAMP, immutable) |
| `updated_at` | DATETIME | When last re-processed (default: CURRENT_TIMESTAMP) |
| `content_preview` | TEXT | First ~1000 chars of content. Populated only when `indexing.content_snippets: true` is set in config (default `false`); otherwise NULL. Used for keyword-search snippets. |
| `sha256_hash` | TEXT | SHA-256 fingerprint for duplicate detection (not reversible to content) |
| `md5_hash` | TEXT | MD5 fingerprint for local↔remote file matching (e.g. matches Google Drive's `md5Checksum`) |
| `project_id` | INTEGER | FK to `projects` |
| `client_id` | INTEGER | FK to `clients` |
| `assignment_source` | TEXT | How client association was determined (app-scope) |
| `folder_id` | INTEGER | FK to `folders` |
| `metadata` | TEXT | JSON: additional file metadata |

**Vectorization columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `vectorized_at` | DATETIME | When embeddings were generated |
| `vectorized_chunks` | INTEGER | Number of embedding chunks (default: 0) |

**Status tracking columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |
| `status_reason` | TEXT | Why file has this status |
| `status_changed_at` | DATETIME | When status last changed |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |

**AI-generated summaries** (standard column, populated by future scope — always NULL in tool-only installs):

| Column | Type | Purpose |
|--------|------|---------|

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from `name` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_files_source` | `(source)` | Filter by source |
| `idx_files_path` | `(path)` | Path lookups |
| `idx_files_project` | `(project_id)` | Project queries |
| `idx_files_folder` | `(folder_id)` | Folder queries |
| `idx_files_hash` | `(sha256_hash)` | Duplicate detection |
| `idx_files_md5` | `(md5_hash)` | Local↔remote hash matching |
| `idx_files_status` | `(status)` | Filter by status |
| `idx_files_local_unique` | `(source, path)` | Unique local paths |
| `idx_files_drive_unique` | `(source, external_id, account)` | Uniqueness for remote files across (source, external_id, account). Index name reflects Drive-first history; applies to any connector's remote files. |
| `idx_files_account` | `(account)` | Filter by source account |
| `idx_files_visibility` | `(mcp_view)` | Filter by visibility |
| `idx_files_client` | `(client_id)` | Filter by client |
| `idx_files_modified` | `(modified_at)` | Sort by modification time |
| `idx_files_type` | `(content_type)` | Filter by file type |

---

### folders

Folder hierarchy for both local filesystem and remote sources (connector-provided).

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `path` | TEXT | Full path (NOT NULL) |
| `relative_path` | TEXT | Path relative to home (NOT NULL) |
| `name` | TEXT | Folder name (NOT NULL) |
| `parent_path` | TEXT | Parent folder path |
| `parent_folder_id` | INTEGER | FK to parent folder (tree traversal) |
| `source` | TEXT | `'local'` by default; connectors set their own source string (e.g. `'workdrive'`, `'personaldrive'` from the Google connector) |
| `external_id` | TEXT | Source-system folder ID (e.g. Drive folder ID) |
| `account` | TEXT | Source account (connector-provided) |
| `project_id` | INTEGER | FK to `projects` |
| `client_id` | INTEGER | FK to `clients` |
| `assignment_source` | TEXT | How assignment was determined (app-scope) |
| `created_at` | DATETIME | When folder was indexed (default: CURRENT_TIMESTAMP) |
| `scanned_at` | DATETIME | When folder was last scanned for files |
| `indexed_at` | DATETIME | When first ingested (default: CURRENT_TIMESTAMP, immutable) |
| `updated_at` | DATETIME | When last re-processed (default: CURRENT_TIMESTAMP) |

**Pre-computed counts:**

| Column | Type | Purpose |
|--------|------|---------|
| `file_count` | INTEGER | Direct file count (default: 0) |
| `direct_file_count` | INTEGER | Files directly in this folder (default: 0) |
| `total_file_count` | INTEGER | Files in this folder + all descendants (default: 0) |
| `total_size_bytes` | INTEGER | Total size recursive (default: 0) |

**Status tracking:**

| Column | Type | Purpose |
|--------|------|---------|
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from `name` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_folders_path` | `(path)` | Path lookups |
| `idx_folders_project` | `(project_id)` | Project queries |
| `idx_folders_source` | `(source)` | Filter by source |
| `idx_folders_unique_path` | `(path)` | Unique local paths (WHERE source = 'local') |
| `idx_folders_visibility` | `(mcp_view)` | Filter by visibility |
| `idx_folders_status` | `(status)` | Filter by status |
| `idx_folders_client` | `(client_id)` | Filter by client |

---

### projects

Project metadata for detected code projects and work projects.

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `project_name` | TEXT | Display name (NOT NULL) |
| `description` | TEXT | Project description |
| `root_path` | TEXT | Filesystem path to project root (UNIQUE) |
| `project_type` | TEXT | `'code'`, `'data'`, `'docs'`, etc. |
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |
| `status_reason` | TEXT | Why project has this status (e.g., `'cli:delete'`) |
| `created_at` | DATETIME | When project was created (default: CURRENT_TIMESTAMP) |
| `updated_at` | DATETIME | Last update (default: CURRENT_TIMESTAMP) |
| `metadata` | TEXT | JSON: additional metadata |

**Client association columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `client_id` | INTEGER | FK to `clients` |
| `client` | TEXT | Client name (legacy, prefer `client_id` FK) |
| `github_url` | TEXT | GitHub repository URL |
| `root_folder_id` | INTEGER | FK to `folders` |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from `project_name` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_projects_root` | `(root_path)` | Unique index for project root |
| `idx_projects_client` | `(client_id)` | Filter by client |
| `idx_projects_visibility` | `(mcp_view)` | Filter by visibility |

---

### clients

Client/project grouping table. Projects can optionally reference a client via `client_id` FK.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `name` | TEXT | Client display name (NOT NULL, UNIQUE) |
| `slug` | TEXT | URL-friendly identifier (NOT NULL, UNIQUE) |
| `client_type` | TEXT | Client type classification (NOT NULL) |
| `path_pattern` | TEXT | Folder path pattern for matching |
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |
| `status_reason` | TEXT | Why client has this status (e.g., `'cli:delete'`) |
| `created_at` | DATETIME | When created (default: CURRENT_TIMESTAMP) |
| `metadata` | TEXT | JSON: additional metadata |
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |
| `display_name` | TEXT | Uniform display label (auto-populated from `name` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_clients_slug` | `(slug)` | Slug lookups |
| `idx_clients_type` | `(client_type)` | Filter by client type |
| `idx_clients_visibility` | `(mcp_view)` | Filter by visibility |

**Usage:** Clients are created manually — there is no auto-seeding or auto-detection. The `path_pattern` column (e.g., `/Work/clients/acme/`) is used to assign `client_id` on projects whose `root_path` falls under that pattern.

---

## Source Tables

These tables store non-file data sources. They are **not** unified into files.

### visits

Web browsing history from Safari and Chrome.

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `url` | TEXT | Page URL (NOT NULL) |
| `title` | TEXT | Page title |
| `visit_time` | DATETIME | When visited (NOT NULL) |
| `browser` | TEXT | `'safari'` or `'chrome'` (NOT NULL) |
| `visit_count` | INTEGER | Number of visits (default: 1) |
| `indexed_at` | DATETIME | When first ingested (default: CURRENT_TIMESTAMP, immutable) |
| `updated_at` | DATETIME | When last re-processed (default: CURRENT_TIMESTAMP) |
| `created_at` | DATETIME | Record creation time (default: CURRENT_TIMESTAMP) |

**Status tracking:**

| Column | Type | Purpose |
|--------|------|---------|
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |

**Client/project association:**

| Column | Type | Purpose |
|--------|------|---------|
| `client_id` | INTEGER | FK to `clients` |
| `project_id` | INTEGER | FK to `projects` |

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from `title` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_visits_time` | `(visit_time)` | Sort by visit time |
| `idx_visits_browser` | `(browser)` | Filter by browser |
| `idx_visits_project` | `(project_id)` | Filter by project |
| `idx_visits_unique` | `(url, visit_time, browser)` | Unique constraint |
| `idx_visits_client` | `(client_id)` | Filter by client |
| `idx_visits_status` | `(status)` | Filter by status |
| `idx_visits_visibility` | `(mcp_view)` | Filter by visibility |

---

### emails

Email messages populated by email connector plugins (e.g. `footprinter-google` for Gmail).

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `message_id` | TEXT | Provider message ID (NOT NULL, e.g. Gmail message ID) |
| `thread_id` | TEXT | Provider thread ID (NOT NULL) |
| `account` | TEXT | Connector-provided account name (NOT NULL) |
| `from_address` | TEXT | Sender email |
| `from_name` | TEXT | Sender name |
| `to_addresses` | TEXT | Recipients (JSON) |
| `cc_addresses` | TEXT | CC recipients (JSON) |
| `subject` | TEXT | Email subject |
| `body_preview` | TEXT | First portion of body |
| `received_at` | DATETIME | When received (NOT NULL) |
| `labels` | TEXT | Provider-specific labels (JSON, e.g. Gmail labels) |
| `has_attachments` | BOOLEAN | Has attachments (default: 0) |
| `is_read` | BOOLEAN | Read status (default: 1) |
| `indexed_at` | DATETIME | When first ingested (default: CURRENT_TIMESTAMP, immutable) |
| `updated_at` | DATETIME | When last re-processed (default: CURRENT_TIMESTAMP) |
| `metadata` | TEXT | JSON: additional email metadata |
| `created_at` | DATETIME | Record creation time (default: CURRENT_TIMESTAMP) |

**Status tracking:**

| Column | Type | Purpose |
|--------|------|---------|
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |

**AI-generated summaries** (standard column, populated by future scope — always NULL in tool-only installs):

| Column | Type | Purpose |
|--------|------|---------|

**Client/project association:**

| Column | Type | Purpose |
|--------|------|---------|
| `client_id` | INTEGER | FK to `clients` |
| `project_id` | INTEGER | FK to `projects` |

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from `subject` via trigger) |

**Unique constraint:** `(message_id, account)`

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_email_account` | `(account)` | Filter by account |
| `idx_email_received` | `(received_at)` | Sort by received date |
| `idx_email_from` | `(from_address)` | Filter by sender |
| `idx_email_thread` | `(thread_id)` | Thread queries |
| `idx_emails_client` | `(client_id)` | Filter by client |
| `idx_emails_project` | `(project_id)` | Filter by project |
| `idx_emails_visibility` | `(mcp_view)` | Filter by visibility |

---

### chats

Claude and ChatGPT conversation exports.

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `external_id` | TEXT | Unique ID (from export platform, UNIQUE NOT NULL) |
| `account` | TEXT | `'claude'` or `'chatgpt'` (NOT NULL) |
| `title` | TEXT | Conversation title |
| `created_at` | DATETIME | When started (origin) |
| `modified_at` | DATETIME | Last message time (origin) |
| `message_count` | INTEGER | Number of messages (default: 0) |
| `indexed_at` | DATETIME | When first indexed (audit, immutable; default: CURRENT_TIMESTAMP) |
| `updated_at` | DATETIME | When last re-processed (audit; default: CURRENT_TIMESTAMP) |
| `metadata` | TEXT | JSON: additional fields |

**Vectorization:**

| Column | Type | Purpose |
|--------|------|---------|
| `metadata_vectorized_at` | DATETIME | When metadata embeddings were generated |

**Status tracking:**

| Column | Type | Purpose |
|--------|------|---------|
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |

**Client/project association:**

| Column | Type | Purpose |
|--------|------|---------|
| `client_id` | INTEGER | FK to `clients` |
| `project_id` | INTEGER | FK to `projects` |

**Merge tracking:**

| Column | Type | Purpose |
|--------|------|---------|
| `merged_into_id` | INTEGER | Historical — column preserved, merge functionality removed. Previously pointed to the surviving record when duplicate chats were merged. |

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from `title` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_chat_conv_created` | `(created_at)` | Sort by creation date |
| `idx_chat_conv_account` | `(account)` | Filter by account |
| `idx_chat_conv_status` | `(status)` | Filter by status |
| `idx_chats_client` | `(client_id)` | Filter by client |
| `idx_chats_project` | `(project_id)` | Filter by project |
| `idx_chats_visibility` | `(mcp_view)` | Filter by visibility |

---

### messages

Individual messages within conversations.

**Core columns:**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `chat_id` | INTEGER | FK to `chats` (NOT NULL) |
| `message_id` | TEXT | Platform-specific message ID |
| `role` | TEXT | `'user'` or `'assistant'` (NOT NULL) |
| `content` | TEXT | Message content |
| `created_at` | DATETIME | Message timestamp (origin) |
| `metadata` | TEXT | JSON: model, tokens, etc. |
| `vectorized_at` | DATETIME | When embeddings were generated |
| `vectorized_chunks` | INTEGER | Number of embedding chunks (default: 0) |
| `indexed_at` | DATETIME | When first indexed (audit, immutable; default: CURRENT_TIMESTAMP) |
| `updated_at` | DATETIME | When last re-processed (audit; default: CURRENT_TIMESTAMP) |

**Status tracking:**

| Column | Type | Purpose |
|--------|------|---------|
| `status` | TEXT | CHECK: `'listed'`, `'unlisted'`, `'removed'` (default: `'listed'`) |

**Access control columns** (cached resolved values — written by the `access_resolution` pipeline stage, not direct settings):

| Column | Type | Purpose |
|--------|------|---------|
| `mcp_read` | TEXT | CHECK: `'allow'`, `'deny'`, `'inherit'` (default: `'inherit'`) |
| `mcp_view` | TEXT | CHECK: `'hidden'`, `'opaque'`, `'visible'`, `'inherit'` (default: `'inherit'`) |

**Display:**

| Column | Type | Purpose |
|--------|------|---------|
| `display_name` | TEXT | Uniform display label (auto-populated from first 100 chars of `content` via trigger) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_chat_msg_conv` | `(chat_id)` | Chat lookup |
| `idx_chat_msg_created` | `(created_at)` | Sort by creation date |
| `idx_messages_visibility` | `(mcp_view)` | Filter by visibility |
| `idx_messages_status` | `(status)` | Filter by status |

---

## Support Tables

### sources

Runtime source registry. Tracks which data sources are configured and their connection details. Seeded from `config/config.yaml` on startup.

| Column | Type | Purpose |
|--------|------|---------|
| `name` | TEXT | Source name (PRIMARY KEY) |
| `source_type` | TEXT | Source type (NOT NULL) |
| `adapter` | TEXT | Adapter module name |
| `account` | TEXT | Account identifier |
| `label` | TEXT | Display label |
| `icon` | TEXT | UI icon identifier |
| `enabled` | INTEGER | Whether source is active (default: 1) |
| `config` | TEXT | JSON: source-specific configuration |
| `created_at` | DATETIME | When registered (default: CURRENT_TIMESTAMP) |
| `updated_at` | DATETIME | Last update (default: CURRENT_TIMESTAMP) |

**Indexes:**

| Index | Columns | Notes |
|-------|---------|-------|
| `idx_sources_type` | `(source_type)` | Filter by source type |
| `idx_sources_enabled` | `(enabled)` | Filter active sources |

---

### ingests

Per-pipe run history. Records each pipe execution for auditing, status display, and incremental watermark tracking. Replaces the former `runs` and `pipeline_watermarks` tables.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `pipe` | TEXT | Pipe name (NOT NULL) |
| `started_at` | DATETIME | When pipe started (NOT NULL) |
| `completed_at` | DATETIME | When pipe finished |
| `status` | TEXT | `'running'`, `'completed'`, `'failed'`, `'interrupted'` (NOT NULL) |
| `mode` | TEXT | Run mode (e.g. `'incremental'`, `'full'`) |
| `trigger` | TEXT | What triggered the run |
| `items_processed` | INTEGER | Total items processed (default: 0) |
| `items_new` | INTEGER | New items added (default: 0) |
| `items_updated` | INTEGER | Updated items (default: 0) |
| `items_skipped` | INTEGER | Skipped items (default: 0) |
| `errors` | INTEGER | Error count (default: 0) |
| `elapsed_seconds` | REAL | Wall-clock duration |
| `metadata` | TEXT | JSON metadata blob |

**Indexes:** `idx_ingests_pipe_status` on `(pipe, status)`.

---

### uploads

Generic upload log for tracking data imports (chat exports, etc.) with deduplication.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Primary key |
| `filename` | TEXT | Original filename (NOT NULL) |
| `file_hash` | TEXT | SHA-256 hash for deduplication (UNIQUE) |
| `file_size` | INTEGER | File size in bytes |
| `type` | TEXT | Upload type (e.g., `'chat'`) (NOT NULL) |
| `source` | TEXT | Source system (e.g., `'claude'`, `'chatgpt'`) |
| `items_added` | INTEGER | New items imported (default: 0) |
| `items_updated` | INTEGER | Existing items updated (default: 0) |
| `items_total` | INTEGER | Total items processed (default: 0) |
| `status` | TEXT | `'pending'`, `'processing'`, `'completed'`, `'failed'` |
| `error_message` | TEXT | Error details if failed |
| `uploaded_at` | DATETIME | When upload started (default: CURRENT_TIMESTAMP) |
| `completed_at` | DATETIME | When processing finished |
| `metadata` | TEXT | JSON: additional info |

**Indexes:**
- `idx_uploads_type` on `(type)`
- `idx_uploads_hash` on `(file_hash)`

---

### permission_policies

Current active table for permission rules controlling Claude read access.

| Column | Type | Purpose |
|--------|------|---------|
| `scope` | TEXT | Permission scope (PRIMARY KEY) |
| `setting` | TEXT | `'allow'` or `'deny'` (NOT NULL, CHECK constraint) |
| `updated_at` | DATETIME | Last update (default: CURRENT_TIMESTAMP) |

**Scope Values:**
- `global` — Default for all items
- `source:files` — Default for all files
- `source:emails` — Default for all emails
- `source:chats` — Default for all chat conversations
- `source:browser` — Default for all browser history entries
- `account:{name}` — Default for items from specific account (e.g., `account:personal`, `account:work-org`)
- `folder:/path/prefix` — Applies to files with matching path prefix (supports tilde expansion: `folder:~/Personal/` expands to full path)
- `project:{id}` — Applies to a project and all its children (files, emails, chats, folders)
- `client:{id}` — Applies to a client, its projects, and all their children
- `file:{id}` — Applies to a single file
- `email:{id}` — Applies to a single email
- `chat:{id}` — Applies to a single chat

**Example rows:**
```
scope                                  | setting
---------------------------------------|--------
global                                 | deny
source:files                           | allow
account:personal                       | deny
folder:~/Personal/identity/ | deny
```

---

### visibility_policies

Current active table for visibility rules controlling MCP metadata access.

| Column | Type | Purpose |
|--------|------|---------|
| `scope` | TEXT | Visibility scope (PRIMARY KEY) |
| `setting` | TEXT | `'hidden'`, `'opaque'`, or `'visible'` (NOT NULL, CHECK constraint) |
| `updated_at` | DATETIME | Last update (default: CURRENT_TIMESTAMP) |

**Scope Values:**
- `global` — Default for all items
- `source:files` — Default for all files
- `source:emails` — Default for all emails
- `source:folders` — Default for all folders
- `source:chats` — Default for all chat conversations
- `source:browser` — Default for all browser history entries
- `account:{name}` — Default for items from specific account (e.g., `account:personal`, `account:work-org`)
- `folder:/path/prefix` — Applies to items with matching path prefix (supports tilde expansion: `folder:~/Personal/` expands to full path)
- `project:{id}` — Applies to a project and all its children (files, emails, chats, folders)
- `client:{id}` — Applies to a client, its projects, and all their children
- `file:{id}` — Applies to a single file
- `email:{id}` — Applies to a single email
- `chat:{id}` — Applies to a single chat

**Example rows:**
```
scope                                  | setting
---------------------------------------|--------
global                                 | visible
source:files                           | visible
account:personal                       | hidden
folder:~/Personal/identity/ | hidden
folder:~/Work/clients/acme/ | opaque
```

---

## Relationship Semantics

The schema tables above document columns and types. This section explains what the relationships between tables *mean* — how foreign keys are populated, what re-indexing preserves, and how manual overrides interact with the ingest pipeline. See [Entity Architecture](#entity-architecture-super-entities--content-entities) for the super entity / content entity distinction that shapes these relationships.

### Structural vs Stamped Foreign Keys

Foreign keys in Footprinter follow one of two patterns:

**Structural FKs** reflect current physical state and are always updated on re-index:

- `files.folder_id` — which folder contains this file. Always overwritten to match the current disk or Drive location.
- `folders.parent_folder_id` — parent in the folder tree. Set from filesystem structure (local) or Drive API parent (remote).
- `messages.chat_id` — which conversation owns this message. Immutable after insert.

**Stamped FKs** are assigned at index time and preserved on re-index so that manual overrides survive:

- `files.project_id` — assigned via path-prefix match or inherited from the file's folder. On re-index, only set if currently NULL (`CASE WHEN project_id IS NULL THEN ? ELSE project_id END`).
- `files.client_id` — set via CLI only (direct assignment or folder cascade). Not set during ingest, not overwritten on re-index.
- `folders.project_id` — set by user via CLI (`fp folder edit --project`), then cascaded to descendants via `cascade_project_id()`.
- `folders.client_id` — same pattern as `folders.project_id`, cascaded via `cascade_client_id()`.

**Note:** Cascade operations (`cascade_project_id()`, `cascade_client_id()`) are unconditional — they overwrite existing values on all descendant files, including per-file manual overrides. The "preserved on re-index" guarantee applies only to the ingest pipeline, not to cascade.

### Re-indexing Behavior

What happens to each FK column when a file or folder is re-indexed (via `fp ingest`):

| Table.Column | Type | Re-index behavior | Manual override survives re-index? |
|---|---|---|---|
| `files.folder_id` | Structural | Always overwritten to current parent folder | No |
| `files.project_id` | Stamped | Only set if currently NULL | Yes |
| `files.client_id` | — | Not touched by ingest | Yes |
| `files.status` | — | Preserved unless `'removed'` or NULL | Yes |
| `folders.parent_folder_id` | Structural | Set from filesystem/Drive hierarchy | No |
| `folders.project_id` | Stamped | Not touched by ingest (set via CLI only) | Yes |
| `folders.client_id` | — | Not touched by ingest (set via CLI only) | Yes |
| `messages.chat_id` | Structural | Immutable (set on insert) | N/A |

Cascade operations (`fp folder edit --project`, `fp folder edit --client`) are **not** re-indexing — they unconditionally overwrite `project_id` or `client_id` on all descendant folders and their files, including any per-file manual overrides.

### Project Resolution Chain

How `project_id` is determined for a new file during ingest:

1. **Direct path match** — the file's path is prefix-matched against `projects.root_path` (longest match wins).
2. **Folder inheritance** — if no direct match, inherits `project_id` from the file's folder record.
3. **Manual assignment** — a user assigns a project to a folder via CLI; `cascade_project_id()` propagates to all descendant folders and their files.

Step 3 is an explicit user action, not part of the ingest pipeline. Once a file's `project_id` is set (by any path), it is preserved on subsequent re-indexes. However, a later cascade operation on the parent folder will overwrite it unconditionally.

### Cross-table Relationship Map

Every FK relationship and how it is populated. Content entities [C] reference super entities [S]; super entities reference each other for hierarchy:

| From | Tier | To | Tier | FK Column | How populated |
|---|---|---|---|---|---|
| `files` | C | `folders` | S | `folder_id` | Auto-linked by parent directory path during ingest |
| `files` | C | `projects` | S | `project_id` | Path-prefix match or folder inheritance at ingest; preserved on re-index |
| `files` | C | `clients` | S | `client_id` | Follows project assignment or direct CLI assignment (`fp upsert`) |
| `folders` | S | `folders` | S | `parent_folder_id` | Filesystem hierarchy (local) or Drive API parent (remote) |
| `folders` | S | `projects` | S | `project_id` | User assignment via CLI (`fp upsert`), cascaded to descendants |
| `folders` | S | `clients` | S | `client_id` | User assignment via CLI (`fp upsert`), cascaded to descendants |
| `projects` | S | `clients` | S | `client_id` | User assignment via CLI (`fp upsert`) |
| `projects` | S | `folders` | S | `root_folder_id` | Links project to its root folder entry |
| `messages` | C | `chats` | C | `chat_id` | Set on insert from chat export data |
| `chats` | C | `projects` | S | `project_id` | User assignment (`fp upsert`) |
| `chats` | C | `clients` | S | `client_id` | Follows project or direct assignment |
| `chats` | C | `chats` | C | `merged_into_id` | Historical — column preserved, merge functionality removed |
| `emails` | C | `projects` | S | `project_id` | User assignment (`fp upsert`) |
| `emails` | C | `clients` | S | `client_id` | Follows project or direct assignment |
| `visits` | C | `projects` | S | `project_id` | User assignment (`fp upsert`) |
| `visits` | C | `clients` | S | `client_id` | Follows project or direct assignment |

### Local↔Remote File Matching

Local files and remote files (from connector plugins) are correlated using hash-based matching:

- **MD5 hash match (high confidence):** A local file's `md5_hash` is compared against the remote record's `md5_hash`. Most remote APIs expose MD5 per file (the Google Drive API's `md5Checksum` is one example). Identical hashes mean identical content.
- **Name+size fallback (medium confidence):** When hashes are unavailable, files are matched by identical filename and `size_bytes`.

The `md5_hash` column on the `files` table serves this purpose — it enables matching without downloading or comparing actual content. Local and remote records share the same `files` table, distinguished by the `source` column (`'local'` vs whatever value the connector sets, e.g. `'workdrive'` / `'personaldrive'` from the Google connector).

Matching results persist even when files are removed — they form the historical audit trail for what was backed up and when.

In app-scope databases, explicit link columns provide direct cross-references between local and remote records:

- `local_file_id` on a remote record points to the corresponding local file
- `remote_file_id` on a local record points to the corresponding remote file

These columns are populated by a connector-specific linking stage (e.g. `drive_links` in the Google connector) after hash matching. They are not present in tool-scope databases, where linking is done purely by hash correlation.

---

## Behavioral Concepts

The schema tables above document column names and types. This section explains the behavioral contracts behind key columns — what they mean, how they interact, and what guarantees they provide.

### Assignment Provenance

The `assignment_source` column appears on `projects` and all entity tables (`files`, `folders`, `chats`, `emails`, `visits`) in app-scope databases. It tracks whether an association (project, client) was set manually or detected automatically:

| Value | Meaning | Set by |
|-------|---------|--------|
| `'user'` | Manually assigned via CLI (`fp folder edit`, `fp file edit`, `fp project link`) | Service layer `assign()` functions |
| `'auto'` | Detected by the ingest pipeline | `project_detection.py`, connector adapters |

**Protection semantics:** During re-ingest, the pipeline uses a `CASE WHEN` guard in its UPSERT statements:

```sql
assignment_source = CASE WHEN projects.assignment_source = 'user'
    THEN 'user' ELSE excluded.assignment_source END
```

This means user assignments survive automated re-detection. If a user manually assigns a file to a project, subsequent pipeline runs will not overwrite that assignment — even if auto-detection would classify it differently.

The same pattern applies to `name_source` on the `projects` table: user-set project names are preserved when auto-detection would suggest a different name.

### Status Model Lifecycle

See [Status & Exclusion Model](#status--exclusion-model) for the full behavioral contract: valid values per table, soft-delete semantics (`fp upsert --status removed`), default query filtering via `build_status_filter()` with `default_exclude=["removed"]`, re-indexing implications, and `status_reason` codes.

The three exclusion mechanisms operate at different pipeline stages — config exclusions prevent scanning, hidden-file detection marks records at index time, and manual status changes update records after the fact. All three converge on the `status` column as the single source of truth for record visibility.

### Supported Content Extraction Types

Footprinter has two extraction paths that operate on different extension sets:

**Preview extraction** populates the `content_preview` column (first ~1000 characters) during file indexing. This feeds FTS search. Supported extensions:

`.txt`, `.md`, `.py`, `.js`, `.json`, `.yaml`, `.yml`, `.pdf`, `.docx`

**Full extraction** reads entire file content with intelligent chunking for semantic vectorization. Supported extensions by category:

| Category | Extensions |
|----------|------------|
| **Text / code** | `.txt`, `.md`, `.py`, `.js`, `.json`, `.yaml`, `.yml`, `.html`, `.css`, `.jsx`, `.tsx` |
| **Documents** | `.pdf`, `.docx`, `.doc` |
| **Data** | `.csv` |
| **Other text-like** | `.xml`, `.svg`, `.rst`, `.toml`, `.ini`, `.cfg`, `.conf`, `.sh`, `.bash`, `.zsh`, `.fish`, `.sql`, `.graphql`, `.proto`, `.ts`, `.vue`, `.svelte`, `.astro`, `.java`, `.kt`, `.scala`, `.go`, `.rs`, `.rb`, `.php`, `.c`, `.h`, `.cpp`, `.hpp`, `.cs`, `.swift`, `.m`, `.r`, `.jl`, `.lua`, `.pl`, `.pm`, `.tf`, `.hcl`, `.dockerfile`, `.log`, `.env`, `.gitignore`, `.editorconfig`, `.tex`, `.bib`, `.org` |
| **Skipped** | Binary files (images, video, audio, archives) — returns no content |

Files with unsupported extensions are silently skipped — no error, no placeholder content.

---

## FTS5 Virtual Tables

Created by `init_db()`. These are external-content FTS5 tables backed by their respective base tables, providing full-text search over key columns. Maintained automatically by SQLite triggers (insert/update/delete). Use `--rebuild-vectors` to rebuild from scratch.

| Virtual Table | Base Table | Indexed Columns |
|---------------|------------|-----------------|
| `files_fts` | `files` | `name`, `content_preview` |
| `emails_fts` | `emails` | `subject`, `from_name`, `from_address`, `body_preview` |
| `chats_fts` | `chats` | `title` |

Content columns (`content_preview`, `body_preview`) are NULLed in the FTS index when `mcp_view` is `'opaque'` or `'hidden'`, preventing sensitive content from appearing in search results.

---

## Terminology Note: `status` Columns

The column name `status` appears in multiple tables. All 8 entity tables now share the same trichotomy; support tables use independent value sets:

| Table group | `status` Values | Purpose |
|-------------|----------------|---------|
| **All entity tables** (files, folders, visits, projects, chats, messages, emails, clients) | **`listed`**, `unlisted`, `removed` | Uniform data lifecycle state — see [Status & Exclusion Model](#status--exclusion-model) |
| `uploads` | **`pending`**, `processing`, `completed`, `failed` | Upload pipeline state |
| `ingests` | **`running`**, `completed`, `failed`, `interrupted` | Per-pipe run state (CHECK constraint enforced) |

Entity table status values are uniform by design — the trichotomy enables consistent filtering across all entity types via `build_status_filter()`. Support tables (`uploads`, `ingests`) track pipeline execution state and are unrelated to the data lifecycle model.

---

## Status & Exclusion Model

Footprinter uses three mechanisms to control which files appear in queries and results. They operate at different stages of the pipeline. For how status filtering integrates with MCP access control (Layer 0), see `reference/mcp-access-control.md` — [Data Scoping (Layer 0)](mcp-access-control.md#data-scoping-layer-0).

### 1. Config exclusions — never scanned

Regex patterns in `config.yaml` under `exclusions.always` and `exclusions.sensitive`. Files matching these patterns are skipped during scanning — they never enter the database.

**Use case:** Regeneratable dependencies (node_modules, venv, .git internals), system noise (.DS_Store, Library/), and credentials (.aws, .ssh).

**Effect:** Invisible. No database record exists. No way to know the file was skipped without comparing disk to database.

### 2. Dot-file detection — scanned, marked unlisted

Files and directories starting with `.` (dot-files, dot-directories) are indexed into the database with `status='unlisted'`. This logic is hardcoded in the file scanner's `_determine_file_status()` function — it is not config-driven.

**Use case:** IDE configuration (.vscode, .idea), version control metadata (.gitignore), and tool state (.claude/) that should be cataloged but excluded from connector-driven sync and some views.

**Effect:** File is in the database with full metadata. The `status_reason` column records why: `dot_file` or `in_dot_folder`. Unlisted files **are included** in the standard query filter (default excludes only `removed`) — they appear alongside `listed` records by default.

### 3. Manual status changes — post-hoc updates

Direct database updates or CLI commands that set `status` and `status_reason` on existing records. Soft-delete is performed by `fp upsert <noun> <id> --status removed` (records stay in the database with `status='removed'`).

**Use case:** Retroactively excluding content that was already indexed, soft-deleting entities via CLI.

**Effect:** Same as mechanism 2 — the record stays in the database, status controls visibility.

`fp delete`, by contrast, performs a hard `DELETE FROM` and refuses to run when dependent rows exist; see [Data scoping operations](interfaces.md#data-scoping-operations) for the full lifecycle.

### Default query filter

The standard query filter excludes `removed` only and is built via `build_status_filter()` (see `footprinter/db/sql_utils.py`). This means:

- `listed` records — **included** in queries
- `unlisted` records — **included** in queries (they appear alongside listed)
- `removed` records — **excluded** from queries
- Any new status values added in the future — **included** by default unless explicitly excluded (the exclude pattern is forward-compatible)

MCP tools, CLI commands, and `fp ingest status` all use this filter. To query only listed records, pass `status="listed"`; to bypass filtering, pass `status="all"`.

### `status_reason` column

Records why an entity has its current status. Present on `files`, `clients`, and `projects` tables.

| Value | Meaning | Set by |
|-------|---------|--------|
| `dot_file` | File name starts with `.` | `_determine_file_status()` |
| `in_dot_folder` | File is inside a dot-directory | `_determine_file_status()` |
| `cli:delete` | Soft-deleted via `fp upsert --status removed` | CLI / service `upsert()` |
| `regeneratable_cache` | node_modules, venv, build artifacts | Manual |
| `system_excluded` | Downloads, app caches, system dotfiles | Manual |
| `removed_from_disk` | File no longer exists locally | Manual |
| `removed_from_drive` | Previously in Drive, now deleted | Manual |
| `NULL` | Active files — no reason needed | Default |

---

## Key Query Patterns

### Listed files by source

```sql
SELECT * FROM files
WHERE source = 'local' AND status = 'listed';
```

In Python code, use `build_status_filter()` from `footprinter/db/sql_utils.py` instead of hand-writing status WHERE clauses.

### Folder hierarchy traversal

```sql
-- Get all descendants of a folder
WITH RECURSIVE descendants AS (
  SELECT id, path, name, parent_folder_id
  FROM folders
  WHERE id = :folder_id

  UNION ALL

  SELECT folder.id, folder.path, folder.name, folder.parent_folder_id
  FROM folders folder
  JOIN descendants descendant ON folder.parent_folder_id = descendant.id
)
SELECT * FROM descendants;
```

### Files in a folder (with descendants)

```sql
SELECT file.* FROM files file
JOIN folders folder ON file.folder_id = folder.id
WHERE folder.path LIKE '/Users/.../Work/clients/acme%'
  AND file.status = 'listed';
```

### Duplicate detection

```sql
SELECT sha256_hash, COUNT(*) as copies, SUM(size_bytes) as total_size
FROM files
WHERE status = 'listed' AND sha256_hash IS NOT NULL
GROUP BY sha256_hash
HAVING COUNT(*) > 1
ORDER BY total_size DESC;
```

### Status breakdown by reason

```sql
SELECT status, status_reason, COUNT(*), SUM(size_bytes) as bytes
FROM files
GROUP BY status, status_reason
ORDER BY COUNT(*) DESC;
```

---

## Row Counts

Run `fp ingest status` for current counts.
