# Permission Policies and Access Control

Reference for the permission-policy and access-control system that governs Footprinter data. Covers visibility (existence / metadata / content), read access, and the `fp permission` command across both interfaces (CLI and MCP), with **roles** determining which path a caller takes.

---

## Roles

Footprinter uses a `Role` enum (`footprinter/services/roles.py`) to distinguish callers:

| Role | Interface | `can_write` | `sees_all` | Description |
|------|-----------|-------------|------------|-------------|
| `ADMIN` | CLI (`fp` commands) | Yes | Yes | Full access. Bypasses visibility and permission checks. |
| `VIEWER` | MCP (AI assistants) | No | No | Read-only. Subject to visibility filtering and permission gating. |

**ADMIN bypasses everything.** When the CLI fetches data, it skips visibility and permission checks entirely — the local user owns the data. The access control model below applies only to VIEWER callers (MCP requests from AI assistants).

Interface layers assign the role at the entry point:
- CLI commands pass `Role.ADMIN`
- The MCP server passes `Role.VIEWER`

---

## Three-Layer Model

VIEWER requests pass through three layers before content is returned. Layer 0 is a data lifecycle filter; Layers 1–2 are security controls.

```
┌─────────────────────────────────────────────────────────────────┐
│                         MCP Request                              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 0: STATUS FILTERING                     │
│                                                                  │
│  Status           │ Effect (VIEWER)                              │
│  ─────────────────────────────────────────────────────────────  │
│  listed           │ Pass through to Layer 1                      │
│  unlisted         │ Excluded (item doesn't exist)                │
│  removed          │ Excluded (item doesn't exist)                │
│                                                                  │
│  ADMIN bypasses this layer — status rides along in metadata.    │
│  ADMIN callers can opt in to non-listed items via                │
│  include_unlisted / include_removed params on discovery tools.  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (only if listed)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: VISIBILITY                          │
│                                                                  │
│  Outcome         │ Effect                                        │
│  ─────────────────────────────────────────────────────────────  │
│  hidden          │ Item excluded from results (doesn't exist)    │
│  opaque          │ Minimal metadata: id, content_type, source    │
│  full            │ Full metadata returned                        │
│                                                                  │
│  Semantics: MOST-RESTRICTIVE-WINS                               │
│  If ANY matching policy is hidden → hidden                      │
│  If ANY matching policy is opaque → opaque                      │
│  Otherwise → full                                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ (only if visible)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 2: PERMISSIONS                         │
│                                                                  │
│  Outcome         │ Effect                                        │
│  ─────────────────────────────────────────────────────────────  │
│  allow           │ Content readable                              │
│  deny            │ Content blocked (metadata still visible)      │
│                                                                  │
│  Semantics: DENY-WINS                                           │
│  If ANY matching policy is deny → denied                        │
│  If NO deny and at least one allow → allowed                    │
│  If nothing set → baseline (allow)                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Scoping (Layer 0)

Status filtering is a **data lifecycle** layer, not a security layer. It controls which records are considered "current" — the equivalent of a recycle bin, not an access control list.

### Status Trichotomy

Every entity table uses a uniform `status` column with three values:

| Status | Meaning | Default filter |
|--------|---------|----------------|
| `listed` | Current, active record | Included |
| `unlisted` | Cataloged but de-emphasized (e.g., dotfiles, paused projects) | Excluded by default |
| `removed` | Soft-deleted — record preserved for audit trail | Excluded |

All 8 entity tables (files, folders, visits, projects, chats, messages, emails, clients) share this same CHECK constraint: `CHECK (status IN ('listed', 'unlisted', 'removed'))` with default `'listed'`.

See `reference/data-model.md` — [Status & Exclusion Model](data-model.md#status--exclusion-model) for `status_reason` values and how status is set, and [Entity Architecture](data-model.md#entity-architecture-super-entities--content-entities) for the super entity / content entity distinction.

### Role-Based Filtering

| Role | Default behavior | Override |
|------|-----------------|----------|
| **VIEWER** | Sees `listed` items only | No override — always filtered to `listed` |
| **ADMIN** | Sees `listed` items by default | `include_unlisted=true` and/or `include_removed=true` on discovery tools |

ADMIN filtering logic (implemented in `services/includes.py`):

| Params | Status filter |
|--------|--------------|
| Neither set | `listed` only (default) |
| `include_unlisted=true` | `listed` + `unlisted` |
| `include_removed=true` | `listed` + `removed` |
| Both `true` | No status filter (all records) |

### Layer 0 vs Layers 1–2

Status filtering and visibility/permissions serve different purposes:

| Aspect | Layer 0 (Status) | Layers 1–2 (Visibility + Permissions) |
|--------|-----------------|--------------------------------------|
| **Purpose** | Data lifecycle management | Security and access control |
| **Who sets it** | Pipeline (`_determine_file_status`, `mark_removed_files`) or user (`fp update --status`) | User via policy commands (`fp permission set`) |
| **Storage** | `status` column on entity tables | `visibility_policies` and `permission_policies` tables, cached in `visibility`/`access` columns |
| **Semantics** | Exact match (listed/unlisted/removed) | Most-restrictive-wins (visibility), deny-wins (permissions) |
| **ADMIN bypass** | ADMIN filters by default but can opt in to non-listed items | ADMIN bypasses entirely |

---

## Access Gating (Layers 1–2)

Layer 0 (status filtering) runs first — items that don't pass status filtering never reach these layers. The visibility and permissions layers below apply only to items with `status='listed'` (for VIEWER) or items the ADMIN has opted in to.

**Key insight**: Visibility is a precondition for permissions. Hidden items cannot be read (they don't exist). Opaque items cannot be read (content not exposed). Only visible items proceed to permission checks.

---

## Gating Pipeline

The access service (`footprinter/services/access_service.py`) implements a 4-stage pipeline for VIEWER callers via `gate_access()`:

1. **Existence** — item must exist in the database
2. **Status** — `status` must be `'listed'` (VIEWER only; ADMIN passes through with status in metadata)
3. **Visibility** — `visibility` must not be `hidden` or `opaque`
4. **Permission** — `access` must not be `deny`

ADMIN callers bypass stages 2–4 (checked via `role.sees_all`). Stage 1 always applies.

Return statuses from `gate_access()`:

| Status | Meaning |
|--------|---------|
| `ok` | Access granted — includes metadata and content |
| `removed` | Item has `status='removed'` (VIEWER only) |
| `unlisted` | Item has `status='unlisted'` (VIEWER only) |
| `hidden` | Item hidden from this role |
| `opaque` | Minimal metadata only |
| `denied` | Permission denied — metadata visible, content blocked |
| `not_found` | Item doesn't exist |
| `invalid_type` | Unrecognised item type |

---

## Policy Tables

Policies are the source of truth for access control. Two tables store them:

### visibility_policies

```sql
CREATE TABLE visibility_policies (
    scope TEXT PRIMARY KEY,
    setting TEXT NOT NULL CHECK (setting IN ('hidden', 'opaque', 'full')),
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### permission_policies

```sql
CREATE TABLE permission_policies (
    scope TEXT PRIMARY KEY,
    setting TEXT NOT NULL CHECK (setting IN ('allow', 'deny')),
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Scope Patterns

Policies are keyed by scope — a string that identifies what the policy applies to. The resolution engine checks all matching scopes for an entity and applies the layer semantics (most-restrictive-wins for visibility, deny-wins for permissions).

| Scope pattern | Applies to | Example |
|---------------|-----------|---------|
| `global` | All entities of all types | `global` |
| `source:{type}` | All entities of a source type | `source:files`, `source:emails`, `source:chats`, `source:folders`, `source:browser` |
| `account:{name}` | Entities from a specific account | `account:personal`, `account:work-org` |
| `folder:{path}` | Files and folders with matching path prefix | `folder:~/Work/clients/`, `folder:~/Personal/` |
| `project:{id}` | A project and all its children (files, emails, chats, folders) | `project:3` |
| `client:{id}` | A client, its projects, and all their children | `client:1` |
| `file:{id}` | A single file | `file:42` |
| `email:{id}` | A single email | `email:10` |
| `chat:{id}` | A single chat | `chat:5` |

**Note on visits (browser history):** There is no `visit:{id}` scope. Visits can be checked (`fp permission check visit:<id>`) but not individually set — they have no per-entity policy level. Visit access inherits from `source:browser → global → baseline`. To control visit access, set policy at the `source:browser` level.

### Folder Prefix Matching

Folder scope rules use path prefix matching with tilde expansion:

```sql
-- Example rows
scope                       | setting
----------------------------|--------
folder:~/Personal/          | hidden
folder:~/Work/clients/      | opaque
```

**Matching rules:**
- The scope must start with `folder:`
- Tilde is expanded to the full home path at resolution time
- Longest matching prefix wins when multiple rules apply
- Prefix matching is case-sensitive

**Example:** For path `~/Work/clients/acme/report.pdf`:
- `folder:~/Work/` matches
- `folder:~/Work/clients/` matches (longer, wins)
- `folder:~/Work/clients/acme/` would match and win if it existed

---

## Resolution Semantics

### Visibility Resolution

The resolution engine checks policies at each scope in the hierarchy for the entity type. All matching policies are collected, then most-restrictive-wins applies:

```
hidden > opaque > full
```

If no policies match at any scope, the hardcoded baseline applies: `BASELINE_VISIBILITY = 'opaque'` (in `footprinter/visibility.py`).

**Scope hierarchy by entity type:**

| Entity type | Scopes checked (in order) |
|------------|--------------------------|
| **Files** | `file:{id}` → folder prefix (longest match) → folder FK → `project:{id}` → `client:{id}` → `source:files` → `global` |
| **Emails** | `email:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:emails` → `global` |
| **Chats** | `chat:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:chats` → `global` |
| **Folders** | folder prefix (longest match) → parent folder → `project:{id}` → `source:folders` → `global` |
| **Browser** | `source:browser` → `global` |
| **Projects** | `project:{id}` → `client:{id}` → `global` |
| **Clients** | `client:{id}` → `global` |

Browser history uses source-level policy only — there is no item-level or folder hierarchy for browser history entries.

### Permission Resolution

Same hierarchy structure, deny-wins semantics. All matching policies are collected:

```
deny (any scope) → denied
all allow/no match → allowed
nothing set → baseline (allow)
```

If no policies match at any scope, the hardcoded baseline applies: `BASELINE_PERMISSION = True` (allow) (in `footprinter/permissions.py`).

**Scope hierarchy by entity type:**

| Entity type | Scopes checked (in order) |
|------------|--------------------------|
| **Files** | `file:{id}` → folder prefix (longest match) → `project:{id}` → `client:{id}` → `source:files` → `global` |
| **Emails** | `email:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:emails` → `global` |
| **Chats** | `chat:{id}` → `project:{id}` → `client:{id}` → `account:{name}` → `source:chats` → `global` |
| **Browser** | `source:browser` → `global` |

---

## Entity Columns

All 8 entity tables carry `visibility` and `access` columns. These store **cached resolved values** written by the recalculation engine. They are not direct settings — use `visibility_policies` and `permission_policies` to manage access.

| Table | `visibility` | `access` | Recalculated | Notes |
|-------|-----------|-----------|--------------|-------|
| `files` | ✓ | ✓ | Both | Full hierarchy resolution |
| `folders` | ✓ | Column exists | Visibility only | Permission not resolved; stays at `inherit` |
| `visits` | ✓ | ✓ | Both | Stamped from the browser-source or global policy |
| `projects` | ✓ | ✓ | Both | Scoped by project/client |
| `chats` | ✓ | ✓ | Both | Full hierarchy resolution |
| `messages` | ✓ | ✓ | Neither | Inherits from parent chat at query time |
| `emails` | ✓ | ✓ | Both | Full hierarchy resolution |
| `clients` | ✓ | ✓ | Both | Top-level scope |

Default value for all columns: `'inherit'`.

### Column Value Semantics

| Column value | Meaning |
|--------------|---------|
| `'inherit'` | No entity-specific policy — resolve from the global policy at query time. If no global policy exists, falls back to the hardcoded baseline (`opaque` for visibility, `allow` for permissions). |
| `NULL` / missing | Truly missing data — fails closed to `opaque` / `deny` regardless of global policy. |
| `'hidden'`, `'opaque'`, `'full'` | Resolved visibility from a specific (non-global) policy. |
| `'allow'`, `'deny'` | Resolved permission from a specific (non-global) policy. |

The recalculation engine writes `'inherit'` when the only matching policies are `global` or the hardcoded baseline. It writes the resolved value when a specific policy (source, account, folder, project, client, or entity-level) determines the outcome. This means:

- Changing a global policy takes effect immediately for all `inherit` entities — no recalculation needed.
- Specific policies still require recalculation to update cached values.
- The MCP server loads the global policy once per request (two PK lookups) and resolves `inherit` values on the fly.

---

## Recalculation Engine

The recalculation engine (`footprinter/access_stamper.py`) resolves policies and writes cached values to entity columns. It does not run at query time — it pre-computes values so that query-time lookups are fast column reads.

### ENTITY_META

The engine maintains metadata for 7 entity types that participate in batch recalculation:

| Entity | Table | Visibility | Permissions | Path column |
|--------|-------|-----------|-------------|-------------|
| `file` | `files` | ✓ | ✓ | `path` |
| `email` | `emails` | ✓ | ✓ | — |
| `chat` | `chats` | ✓ | ✓ | — |
| `folder` | `folders` | ✓ | — | `path` |
| `project` | `projects` | ✓ | ✓ | — |
| `client` | `clients` | ✓ | ✓ | — |
| `visit` | `visits` | ✓ | ✓ | — |

`messages` is the only entity not in ENTITY_META — message rows inherit visibility and access from their parent chat at query time rather than carrying pre-computed values.

### Triggers

| Trigger | Mechanism |
|---------|-----------|
| **Policy CRUD** | CLI commands (`fp permission set`, `fp permission reset`, etc.) call `recalculate_access(scope)` after modifying a policy |
| **Relationship edits** | Changing a file's project, a project's client, or similar FK changes triggers recalculation for affected entities |
| **Pipeline stage** | The `access_resolution` stage runs as the last step in every pipeline, stamping all newly ingested entities |

### Batch Behavior

- `recalculate_access(conn, scope)` — resolves all entities affected by a scope in a single transaction
- `recalculate_access_batched(conn, scope, batch_size=5000)` — same but commits per batch with progress callback, for large scopes
- `recalculate_entity(conn, entity_type, entity_id)` — resolves a single entity
- `stamp_entities(conn, ids_by_type)` — resolves and writes visibility + permissions for given entity IDs; used by both `recalculate_access` and the incremental pipeline path

### Incremental vs Full

- **Incremental** (default `fp ingest`): The `access_resolution` stage only stamps entities added since the last run
- **Full** (`fp ingest --full`): Recalculates everything — useful after bulk policy changes or schema migrations

### Inherit Logic

Entities whose resolution traces back to only the `global` policy or the hardcoded baseline are stored as `'inherit'` rather than the resolved value. This is determined by `_is_inherit_source()`, which checks both direct sources (`"global"`, `"baseline"`) and cascade paths (`"project:3 (via global)"`).

---

## MCP Tool Enforcement

MCP tools apply Layer 0 status filtering via `build_status_filter()` at the db query layer, then read the cached `visibility` and `access` columns for Layers 1–2. For most visibility/permission values, no live policy resolution happens. The exception is `inherit`: the MCP server loads the global visibility and permission policies once per request via `load_globals()`, and `inherit` values are resolved to the global policy on the fly by `resolve_inherit_visibility()` and `resolve_inherit_permission()` (in `footprinter/services/access_service.py`).

For single-item reads, `gate_access()` enforces all three layers in sequence — status (stage 2), visibility (stage 3), permission (stage 4). Both `removed` and `unlisted` statuses map to `NOT_FOUND` for VIEWER callers.

### Error Codes

| Code | Meaning | When Returned |
|------|---------|---------------|
| `NOT_FOUND` | Item is hidden, removed, or unlisted | `visibility = 'hidden'`, or `status` is `'removed'`/`'unlisted'` (VIEWER) |
| `VISIBILITY_RESTRICTED` | Item is opaque | `visibility = 'opaque'` (returns minimal metadata) |
| `PERMISSION_DENIED` | Read access denied | Item is visible but `access = 'deny'` |

### Tool Behavior by Status and Visibility

For VIEWER callers, items must be `listed` AND pass visibility checks. ADMIN callers bypass both status and visibility (and can use `include_unlisted`/`include_removed` on discovery tools).

| Tool | removed / unlisted (VIEWER) | hidden | opaque | full + listed |
|------|----------------------------|--------|--------|-----------------|
| `footprinter_status` | Excluded from counts | N/A (aggregates) | N/A (aggregates) | Aggregate counts |
| `footprinter_search` | Excluded | Excluded | Excluded (FTS), minimal fields (list) | Full metadata |
| `footprinter_project` | NOT_FOUND | NOT_FOUND | Minimal fields | Full metadata |
| `footprinter_client` | NOT_FOUND | NOT_FOUND | Minimal fields | Full metadata |
| `footprinter_folder` | NOT_FOUND | NOT_FOUND | Minimal fields | Full metadata |
| `footprinter_semantic` | Excluded | Excluded | Excluded | Requires `access = 'allow'` |
| `footprinter_read` | NOT_FOUND | NOT_FOUND | VISIBILITY_RESTRICTED | Check permissions |

Semantic search tools are stricter than metadata tools: opaque and denied items are excluded entirely (not metadata-limited), because match relevance itself is content-derived.

### Opaque Field Sets

When an item is opaque, only a minimal set of fields is returned. The allowed fields per entity type are defined in `access_service.py`:

| Entity type | Opaque fields |
|-------------|--------------|
| File | `id`, `content_type`, `source`, `project_id` |
| Email | `id`, `account`, `project_id`, `client_id` |
| Chat | `id`, `account`, `project_id`, `client_id` |
| Folder | `id`, `direct_files`, `direct_file_count`, `source`, `project_id`, `unlisted_file_count`, `unlisted_recursive_file_count` |
| Browser | `id`, `browser`, `project_id` |
| Project | `id`, `status`, `client_id` |
| Client | `id`, `client_type`, `status` |

---

## Vector Store / Semantic Search

Semantic search uses vector embeddings (ChromaDB) to find files and chats by meaning. Vectors are a content-derived index — access control is enforced at query time, not at storage time.

### Vector Persistence Model

Vectors are created at ingest time and persist with the entity. They are deleted only when the entity is removed (`status = 'removed'`). Permission and visibility changes have **no effect** on vector storage.

| Event | Vector action |
|-------|--------------|
| Entity vectorized at ingest | Vectors created |
| Entity marked `status = 'removed'` | Vectors deleted |
| Entity deleted via CLI | Vectors deleted (coupled operation) |
| Permission changes (`access`) | No effect |
| Visibility changes (`visibility`) | No effect |

**Rationale:** Vectors are an index over content, like FTS5. You don't rebuild the FTS5 index when permissions change — you check permissions at query time. Same principle.

### Query-Time Access Control

Semantic search requires both `visibility = 'full'` **and** `access = 'allow'`. This is stricter than metadata search:

- Both opaque and denied items are **excluded entirely** from semantic results
- Rationale: semantic matches are content-derived — appearing in results for a query reveals information about the content
- The same `full + allow` filter applies when the FTS5 keyword fallback is active (ML dependencies unavailable)
- Unlike metadata search tools, semantic search tools do not report suppression counts — excluded items are silently omitted

### Metadata Search vs Semantic Search

| `visibility` | `access` | Metadata search | Semantic search |
|---------------------|-------------------|-----------------|-----------------|
| `hidden`            | (any)             | Excluded        | Excluded        |
| `opaque`            | (not evaluated)   | Minimal metadata | Excluded       |
| `full`              | `deny`            | Full metadata, no content | Excluded |
| `full`              | `allow`           | Full metadata + excerpt | Full result + excerpt |

---

## CLI Management

The `fp permission` command is the primary surface for managing visibility and permission policies. Policy changes automatically trigger recalculation for affected entities.

### List all policies (`fp permission list`)

```bash
fp permission list                   # Show all policies (unified table)
fp permission list --json            # JSON output
```

### Check access resolution (`fp permission check`)

```bash
fp permission check ~/Work/file.py       # Check a file path (bare path)
fp permission check file:~/Work/file.py  # Same, with explicit prefix
fp permission check file:42              # Check a file by numeric ID
fp permission check folder:~/Work        # Folder aggregate check
fp permission check folder:42            # Folder aggregate by numeric ID
fp permission check project:3            # Project-level check
fp permission check client:7             # Client-level check
fp permission check email:10             # Check an email
fp permission check chat:5              # Check a chat
fp permission check visit:3             # Check a visit (browser history)
fp permission check folder:~/Work --verbose  # Show per-file details
```

### Set policies (`fp permission set`)

```bash
fp permission set <scope> --visibility <val> --access <val>  # Set both
fp permission set <scope> --visibility <val>                  # Set visibility only
fp permission set <scope> --access <val>                      # Set access only
```

### Bulk record policies via CSV (`fp permission set source:<type> <csv>`)

```bash
fp view emails --csv --all > emails.csv
# Edit emails.csv: set visibility and/or access columns per record
fp permission set source:emails emails.csv
fp permission set source:files  records.csv
```

CSV format:
- Required column: `id` (from the export CSV)
- At least one of: `visibility` (`full`/`opaque`/`hidden`), `access` (`allow`/`deny`)
- Empty cells leave the setting to inheritance (only non-empty values create policies)
- Extra columns (name, path, subject, etc.) are ignored — safe to use an export CSV directly

Validation is atomic: all rows are checked before any policies are written.
On first invalid row, the operation aborts with a line-numbered error and nothing is changed.

Supported source types: `files`, `emails`, `chats`, `folders`, `projects`, `clients`.
Not supported: `browser` (no per-record scope prefix for visits).

A sample CSV is at `reference/records-policy-template.csv`.

### Reset policies (`fp permission reset`)

```bash
fp permission reset <scope>          # Remove policy for a scope (fall back to inheritance)
fp permission reset --all            # Clear all policies and re-seed defaults
```

### Recalculate cached values (`fp permission recalculate`)

```bash
fp permission recalculate            # Re-resolve access stamps from the policy chain
fp permission recalculate <scope>    # Recalculate for a specific scope
```

### Scope Syntax

All `set`, `reset`, and `check` commands accept scope strings:

```bash
# Global
fp permission set global --visibility full --access allow

# Source type
fp permission set source:emails --access deny

# Account
fp permission set account:personal --visibility hidden

# Folder prefix (tilde expanded)
fp permission set "folder:~/Personal/" --visibility hidden
fp permission set "folder:~/Work/clients/" --access deny

# Entity-specific
fp permission set file:42 --visibility hidden
fp permission set email:10 --access deny
```

### Valid Settings

| Setting | Table | Meaning |
|---------|-------|---------|
| `allow` | `permission_policies` | Grant read access |
| `deny` | `permission_policies` | Block read access |
| `full` | `visibility_policies` | Full metadata in results |
| `opaque` | `visibility_policies` | Minimal metadata only |
| `hidden` | `visibility_policies` | Excluded from results |

### Seed Defaults (Open Access)

On fresh install, `fp setup` wizard automatically seeds:
- `visibility_policies`: `global` = `full`
- `permission_policies`: `global` = `allow`

All indexed data is visible and readable by AI assistants by default. Use `fp permission set global --access deny` to restrict access.

Seeding uses `INSERT OR IGNORE`, so it never overwrites existing policies. Running `fp permission reset --all` clears policies and re-applies defaults.

### Security Posture

Footprinter uses a **fail-open** read-permission posture by design. As a personal tool managing local data, the default is that everything indexed is readable by AI assistants. Visibility defaults are more conservative — metadata is restricted to minimal fields (opaque) until `fp setup` seeds explicit policies.

Two layers control this:

| Layer | Constant | Default | Effect |
|-------|----------|---------|--------|
| **Hardcoded baseline** | `BASELINE_PERMISSION = True` | Allow | When zero policy rows exist, all reads are permitted |
| **Hardcoded baseline** | `BASELINE_VISIBILITY = 'opaque'` | Opaque | When zero policy rows exist, metadata is restricted to minimal fields (conservative) |
| **Seeded policies** | *(created by `fp setup`)* | Allow + Full | Explicit `global` rows that override baselines |

The distinction matters:

- **Hardcoded baselines** are fallback constants in `permissions.py` and `visibility.py`. They apply only when the policy tables are completely empty (e.g., before running `fp setup`). The permission baseline is permissive (allow reads); the visibility baseline is conservative (opaque — minimal fields only).
- **Seeded policies** are database rows created by `fp setup`. They make the open-access posture explicit and manageable — you can narrow them with `fp permission set` commands.

To switch to deny-by-default (metadata-only — metadata visible, content denied):

```bash
fp permission set global --access deny
```

To verify current policies:

```bash
fp permission list
```

---

## Common Patterns

### Hide Personal Files

```bash
fp permission set "folder:~/Personal/identity/" --visibility hidden
```

### Allow Work Files, Deny Personal

```bash
# Everything full visibility, deny reads by default, allow work files
fp permission set global --visibility full --access deny
fp permission set "folder:~/Work/" --access allow
```

### Make Client Data Opaque

```bash
fp permission set "folder:~/Work/clients/" --visibility opaque
```

### Block Specific Email Account

```bash
fp permission set account:personal --visibility hidden
```

---

## Debugging Access Control

### Check Effective Access for a Path

```bash
# Quick check — shows resolved visibility + access
fp permission check ~/Work/clients/acme/report.pdf
```

### Query Matching Policies

```sql
-- What visibility policies might affect a path?
SELECT scope, setting
FROM visibility_policies
WHERE '~/Work/clients/acme/report.pdf' LIKE REPLACE(scope, 'folder:', '') || '%'
   OR scope IN ('global', 'source:files')
ORDER BY LENGTH(scope) DESC;

-- What permission policies might affect a path?
SELECT scope, setting
FROM permission_policies
WHERE '~/Work/clients/acme/report.pdf' LIKE REPLACE(scope, 'folder:', '') || '%'
   OR scope IN ('global', 'source:files')
ORDER BY LENGTH(scope) DESC;
```

### Check Cached Values for a File

```sql
-- See the resolved (cached) values on a file
SELECT id, path, visibility, access
FROM files
WHERE id = ?;
```

These cached values were written by the recalculation engine. If they seem wrong, check the matching policies above and re-run recalculation:

```bash
fp permission check <path>   # Re-resolves and shows the result
```

---

## Related Documentation

- `reference/data-model.md` — Full schema for policy tables and entity columns
- `reference/pipeline.md` — `access_resolution` pipeline stage reference
