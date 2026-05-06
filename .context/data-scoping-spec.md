# Data Scoping Spec

Internal spec for the status-based filtering layer. Covers the trichotomy model, its relationship to the access gating model (visibility + permissions), and the v1.0.2 implementation plan.

**Status:** Approved — extracted from exploration session 2026-05-06.  
**Target:** v1.0.2 (fpr-cli patch release)  
**Future:** Super entity dispositions → fpr-dev (lookup table, user-configurable)

---

## Overview

Footprinter's access model has three layers. This spec covers Layer 0.

```
Layer 0: STATUS FILTER (data scoping — this spec)
  "What lifecycle state is this item in?"
  Drives default query filtering. Not security — noise reduction and data lifecycle.

Layer 1: VISIBILITY (access gating — mcp-access-control.md)
  "Should this agent see this item?"
  hidden / opaque / visible. Security: controls metadata exposure.

Layer 2: PERMISSION (access gating — mcp-access-control.md)
  "Can this agent read the content?"
  allow / deny. Security: controls content access.
```

Layers 1–2 are documented in `reference/mcp-access-control.md`. This spec adds Layer 0.

---

## The Trichotomy

Every entity table uses a single `status` column with exactly three values:

| Status | Meaning | Default query behavior |
|--------|---------|----------------------|
| `listed` | Current, tracked, in the active catalog | Included in results |
| `unlisted` | Tracked but deprioritized — exists, low signal | Excluded from default results |
| `removed` | No longer in the catalog | Excluded from all default results |

**CHECK constraint** (identical on all 8 entity tables):

```sql
status TEXT NOT NULL DEFAULT 'listed' CHECK (status IN ('listed', 'unlisted', 'removed'))
```

The trichotomy is hard-coded. Users cannot add, rename, or reassign these values. They are the filtering primitive that all tool queries key off.

### What each status means

**`listed`** — The item is current and should appear in default results. This is the normal state for actively tracked files, emails, chats, folders, projects, clients, visits, and messages.

**`unlisted`** — The item is in the database but deprioritized. It exists, it's tracked, but it shouldn't clutter default results. Examples:

- Dot-files (`.gitignore`, `.env.example`) — auto-classified at index time
- Files inside dot-directories (`.vscode/settings.json`) — auto-classified at index time
- Dot-folders themselves — auto-classified at index time
- Items a user has manually deprioritized

**`removed`** — The item's record is retained (soft-delete) but it is no longer part of the active catalog. The real-world thing may or may not still exist. Examples:

- File deleted from disk (detected by `mark_removed_files()` during ingest)
- File deleted from remote source (detected by connector sync)
- User soft-deleted via CLI (`fp upsert --status removed`)

The `status_reason` column (where present) records the *event* that caused the status change.

### Entity types

The trichotomy applies uniformly:

| Entity tier | Tables | Status values |
|-------------|--------|---------------|
| **Super entities** | `projects`, `clients`, `folders` | `listed`, `unlisted`, `removed` |
| **Content entities** | `files`, `emails`, `chats`, `visits`, `messages` | `listed`, `unlisted`, `removed` |

All entities use the same three values. No entity type gets extended values in v1.0.2.

**Future (fpr-dev):** Super entities will gain a disposition system — a user-configurable lookup table mapping labels like `active`, `paused`, `completed`, `archived`, `merged`, `abandoned` to one of the three trichotomy statuses. This adds descriptive granularity without changing the filtering primitive. See [Future: Super Entity Dispositions](#future-super-entity-dispositions).

---

## Filtering Behavior

### Role-based defaults

| Role | Default behavior | `include_unlisted` | `include_removed` |
|------|-----------------|-------------------|-------------------|
| VIEWER (MCP) | `listed` only | Ignored — VIEWER always gets `listed` only | Ignored — VIEWER always gets `listed` only |
| ADMIN (CLI) | `listed` only | `listed` + `unlisted` | `listed` + `removed` |

- `include_unlisted` and `include_removed` are **ADMIN-only parameters**
- VIEWER role is hard-gated to `listed` items through every tool — search, semantic, folder, read
- Both parameters default to `false` — ADMIN also sees only `listed` by default
- Setting both to `true` returns all items regardless of status

### SQL filtering patterns

Default query (both roles):

```sql
WHERE status = 'listed'
```

ADMIN with `include_unlisted=true`:

```sql
WHERE status IN ('listed', 'unlisted')
```

ADMIN with `include_removed=true`:

```sql
WHERE status IN ('listed', 'removed')
```

ADMIN with both:

```sql
-- No status filter (or: WHERE 1=1)
```

### Access gating pipeline

`gate_access()` in `access_service.py` becomes a 4-stage pipeline:

```
Stage 1: EXISTENCE
  Item must exist in DB.
  Returns: not_found

Stage 2: STATUS (new)
  VIEWER: if status != 'listed' → blocked
  ADMIN: passes through (status included in metadata)
  Returns: removed (for removed items), unlisted (for unlisted items)

Stage 3: VISIBILITY
  VIEWER: mcp_view check (hidden → excluded, opaque → minimal metadata)
  ADMIN: bypassed
  Returns: hidden, opaque

Stage 4: PERMISSION
  VIEWER: mcp_read check (deny → metadata only)
  ADMIN: bypassed
  Returns: denied
```

### Per-tool behavior

| Tool | VIEWER | ADMIN (default) | ADMIN (include_unlisted) | ADMIN (include_removed) |
|------|--------|----------------|--------------------------|-------------------------|
| `footprinter_search` | listed only | listed only | listed + unlisted | listed + removed |
| `footprinter_semantic` | listed only | listed only | listed + unlisted | listed + removed |
| `footprinter_folder` | listed only | listed only | listed + unlisted | listed + removed |
| `footprinter_read` | listed only | listed only | listed + unlisted | listed + removed |
| `footprinter_status` | Aggregate counts (all statuses) | Aggregate counts (all statuses) | N/A | N/A |
| `footprinter_project` | listed only | listed only | listed + unlisted | listed + removed |
| `footprinter_client` | listed only | listed only | listed + unlisted | listed + removed |

`footprinter_status` always shows aggregate breakdowns including removed counts — it's a dashboard, not a discovery tool.

---

## Status Reason

The `status_reason` column records the *event* that caused the current status. It is present on `files`, `projects`, and `clients` tables. It is free-text, set by the code that changes the status.

| status_reason | Typical status | Set by | Meaning |
|---------------|---------------|--------|---------|
| `dot_file` | `unlisted` | `_determine_file_status()` | File name starts with `.` |
| `in_dot_folder` | `unlisted` | `_determine_file_status()` | File is inside a dot-directory |
| `file_deleted` | `removed` | `mark_removed_files()` | File no longer exists at indexed path |
| `removed_from_drive` | `removed` | Connector sync | File deleted from remote source |
| `cli:upsert` | any | `fp upsert --status <value>` | Status changed via CLI upsert (covers soft-delete via `--status removed` and re-listing via `--status listed`) |
| `regeneratable_cache` | `removed` | Manual | Build artifacts, dependencies |
| `system_excluded` | `removed` | Manual | System noise excluded from catalog |
| `NULL` | `listed` | Default | No specific reason — normal state |

`status_reason` is not used for filtering. It is metadata for audit and diagnostics.

---

## Entity Architecture

### Super entities vs content entities

Footprinter has two tiers of entities. What makes a super entity "super" is how it relates to content entities through FK relationships, how it influences visibility and access control through scope-based policies, and how it participates in the status filtering layer.

**Super entities** — organizational containers:

| Entity | Managed by | Influences children via |
|--------|-----------|------------------------|
| Projects | `fp upsert` (user-created) | `project:{id}` visibility/permission scope; `project_id` FK on files, folders, emails, chats, visits |
| Clients | `fp upsert` (user-created) | `client:{id}` visibility/permission scope; `client_id` FK on files, folders, emails, chats, visits, projects |
| Folders | Pipeline-discovered + `fp assign` | `folder:{path}` visibility/permission scope (prefix matching); `folder_id` FK on files; cascade operations propagate project_id/client_id to descendants |

**Content entities** — data items:

| Entity | Discovered by | Categorized via | Status managed by |
|--------|--------------|----------------|-------------------|
| Files | Ingest pipeline | `fp assign` (project_id, client_id) | Pipeline (`_determine_file_status`, `mark_removed_files`) |
| Emails | Connector (e.g., Gmail) | `fp assign` | Pipeline |
| Chats | Import (Claude/ChatGPT exports) | `fp assign` | Pipeline |
| Visits | Browser history scan | `fp assign` | Pipeline |
| Messages | Chat import (child of chat) | Inherits from parent chat | Pipeline |

### Key architectural properties

**Status does NOT cascade from super entities to children.** When a project is `status='removed'`, its files remain `status='listed'`. All queries filter on the content entity's own status, never the parent's. To hide a project's children, set a visibility policy on the `project:{id}` scope — that propagates through the policy resolution hierarchy, not through status.

**Visibility/permission policies propagate through scope hierarchy.** Super entities are the *targets* of scope-based policies. A policy set at `project:{id}` affects all files, folders, emails, and chats with that `project_id` FK. A policy set at `folder:/path` affects all files under that path prefix. This is the access control mechanism — not status.

**Folders are special.** They're the only super entity that:
- Has cascade operations (`cascade_project_id`, `cascade_client_id`) that propagate FK assignments to descendant folders and their files
- Supports path-prefix policy matching (`folder:/path` scope)
- Is both structural (filesystem hierarchy via `parent_folder_id`) and organizational (groups files via FK)
- Could potentially exist without a filesystem path (conceptual folders — future feature)

### Service layer verbs

| Verb | Applies to | What it does |
|------|-----------|-------------|
| `upsert` | Super entities (projects, clients) | Create or edit the entity — name, description, status, etc. Soft-delete via `--status removed`. |
| `assign` | Content entities (files, folders, emails, chats, visits) | Set `project_id` and/or `client_id` FK — categorize the entity. Does not change status. |
| `delete` | Super entities (projects, clients) | Hard delete — actual record removal from the database. Irreversible. |

Content entities have no `upsert` — you can't create a file from the CLI. Super entities have no `assign` — they're the *target* of assignment, not the subject.

### CLI commands

| Command | Service verb | Entity tier | Notes |
|---------|-------------|-------------|-------|
| `fp upsert project` | `project_service.upsert()` | Super entity | Creates/edits project. `--status` accepts trichotomy values. |
| `fp upsert client` | `client_service.upsert()` | Super entity | Creates/edits client. `--status` accepts trichotomy values. |
| `fp assign file` | `file_service.assign()` | Content entity | Sets project_id/client_id FK. |
| `fp assign folder` | `folder_service.assign()` | Content entity (structural) | Sets project_id/client_id FK. Can cascade to descendants. |
| `fp assign email` | `email_service.assign()` | Content entity | Sets project_id/client_id FK. |
| `fp assign chat` | `chat_service.assign()` | Content entity | Sets project_id/client_id FK. |
| `fp assign visit` | `visit_service.assign()` | Content entity | Sets project_id/client_id FK. |
| `fp delete project` | Hard delete | Super entity | Removes record from database. Confirmation prompt unless `--yes`. |
| `fp delete client` | Hard delete | Super entity | Removes record from database. Confirmation prompt unless `--yes`. |

### `mark_removed_files` semantics

`mark_removed_files()` runs during ingest. It compares indexed paths against existing file records. Files whose paths are no longer in the indexed set get `status='removed'`, `status_reason='file_deleted'`.

This does NOT necessarily mean the file was deleted from disk. Possible causes:
- File actually deleted from disk
- File moved to a different path (re-indexed as a new record at the new path)
- Config exclusion pattern changed to exclude the file's path
- Ingest scope narrowed (folder removed from config)

The `sha256_hash` column enables content-based duplicate detection for the upsert fast-path (skip re-processing if hash+size match), but there is no cross-path dedup that links a moved file's old record to its new one.

### Merge functionality — stripped (FPR-1683)

The `merged` status and associated merge code are being removed from v1.0.2:

- **Removed:** `ChatDedup` class (`chat_dedup.py`), `mark_chat_merged()` (`db/chats.py`), `merge_projects()` (`db/projects.py`), `merged` from all CHECK constraints and VALID_STATUSES constants
- **Preserved:** `merged_into_id` column on chats table (harmless, data integrity), message-level dedup in `_import_with_dedup` (ingest dedup, separate from chat merge)

Merge returns as a future feature in fpr-dev with disposition support. The ingest-level message dedup is unaffected.

### Filter standardization (FPR-1684)

Current default filters are inconsistent:

| Entity | Current pattern | Standard |
|--------|----------------|----------|
| Files | `default_exclude=["removed"]` via `build_status_filter()` | Already standard |
| Chats | `default_exclude=["merged", "removed"]` via `build_status_filter()` | `default_exclude=["removed"]` |
| Projects | `default_exclude=["removed", "merged"]` via `build_status_filter()` | `default_exclude=["removed"]` |
| **Clients** | `default_include=["active"]` via `build_status_filter()` | `default_exclude=["removed"]` |
| **Emails** | Hardcoded `WHERE email.status != 'removed'` | `default_exclude=["removed"]` via `build_status_filter()` |
| **Visits** | Hardcoded `WHERE status != 'removed'` | `default_exclude=["removed"]` via `build_status_filter()` |
| **Folders** | No status filter | `default_exclude=["removed"]` via `build_status_filter()` |

Standard: `default_exclude=["removed"]` via `build_status_filter()` on everything. This is the db layer default. Role-based filtering (VIEWER locked to `listed` only) is applied at the service layer above.

---

## Changes from Current Implementation

### Schema changes

| Change | Details |
|--------|---------|
| CHECK constraint | All 8 entity tables: `CHECK (status IN ('listed', 'unlisted', 'removed'))` |
| Default value | `DEFAULT 'listed'` (was `DEFAULT 'active'`) |
| Extended values | `paused`, `completed`, `archived`, `abandoned`, `merged` all **dropped** from CHECK (FPR-1683) |

### Value mapping (fresh install — no migration needed)

| Old value | New value | Notes |
|-----------|-----------|-------|
| `active` | `listed` | Direct rename |
| `hidden` | `unlisted` | Direct rename — resolves naming collision with `mcp_view='hidden'` |
| `removed` | `removed` | Unchanged |
| `paused` | Dropped | Future: disposition in fpr-dev |
| `completed` | Dropped | Future: disposition in fpr-dev |
| `archived` | Dropped | Future: disposition in fpr-dev |
| `merged` | Dropped | Merge functionality stripped (FPR-1683). Returns in fpr-dev with disposition support. |
| `abandoned` | Dropped | Future: disposition in fpr-dev |

### Code changes

| Area | Change |
|------|--------|
| `_determine_file_status()` | Return `('unlisted', 'dot_file')` / `('unlisted', 'in_dot_folder')` instead of `('hidden', ...)` |
| `VALID_FILE_STATUSES` | `frozenset({'listed', 'unlisted', 'removed'})` |
| `VALID_STATUSES` (clients, projects, `_common.py`) | Update to trichotomy values |
| All WHERE clauses | `status != 'removed'` → `status = 'listed'` throughout |
| `gate_access()` | Add stage 2: status check. VIEWER blocked on `unlisted` and `removed`. |
| `enrich_chat_visibility()` | Add `AND status = 'listed'` to chat ID lookup |
| Search tools | Add `include_unlisted`, `include_removed` params (ADMIN-only) |
| Folder tool | Add `include_unlisted`, `include_removed` params (ADMIN-only) |
| Semantic tool | Add `include_unlisted`, `include_removed` params (ADMIN-only) |
| Read tool | Status gating handled by `gate_access()` — no tool-level params needed |
| Status tool | Update aggregate breakdowns to use `listed`/`unlisted`/`removed` labels |
| Filter standardization | All listing functions use `build_status_filter()` with `default_exclude` (FPR-1684) |
| Schema definition | Update `schema.py` CHECK constraints and defaults |
| Service layer defaults | `project_service.py` `status or "active"` → `status or "listed"` |

### CLI changes

| Area | Change |
|------|--------|
| `fp upsert --status` | Accept only `listed`, `unlisted`, `removed` for projects and clients |
| `fp upsert` help text | Update in `upsert.py` and `_common.py` |
| `VALID_STATUSES_BY_ENTITY` | Update mapping in `upsert.py` |
| `fp delete` | Becomes hard delete — actual `DELETE FROM` for projects and clients (FPR-1684) |
| `service.delete()` | Rewrite from soft-delete (`status='removed'`) to hard delete |

### Documentation changes

| Document | Change |
|----------|--------|
| `reference/mcp-access-control.md` | Add "Data Scoping" section documenting Layer 0 and the three-layer model |
| `reference/data-model.md` | Update "Status & Exclusion Model" section with trichotomy, update all status value tables |
| `reference/data-model.md` | Introduce super entity / content entity terminology throughout |
| `reference/data-model.md` | Document service layer verbs (upsert/assign/delete) |
| `reference/data-model.md` | Document folder's special role as a super entity |

---

## Future: Super Entity Dispositions

**Target:** fpr-dev (app layer, not core tool)

A lookup table mapping user-defined dispositions to the trichotomy:

```sql
CREATE TABLE dispositions (
    label       TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK (status IN ('listed', 'unlisted', 'removed')),
    entity_types TEXT,      -- JSON array: which entity types can use this label
    is_default  BOOLEAN DEFAULT FALSE,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Default seed:

| Label | Status | Entity types |
|-------|--------|-------------|
| `active` | `listed` | projects, clients |
| `paused` | `listed` | projects, clients |
| `completed` | `listed` | projects |
| `archived` | `unlisted` | projects, clients |
| `merged` | `unlisted` | projects, chats |
| `abandoned` | `unlisted` | projects |

Users can create new labels, assign them to a trichotomy status, and apply them to super entities. The filtering primitive remains unchanged — tools always filter by the three-value trichotomy, resolved via the lookup table.

This is out of scope for v1.0.2 and documented here for continuity only.
