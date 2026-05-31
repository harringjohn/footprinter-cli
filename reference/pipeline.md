# Ingest Pipeline Reference

Single source of truth for the `fp ingest` command and its data pipeline.

```bash
fp ingest
```

---

## Stages

The orchestrator runs stages in a fixed order. Each stage is independent and can be run individually.

### Core (always available)

| Stage | Description | Tables |
|-------|-------------|--------|
| `local_folders` | Scan ~/Work, ~/Personal folder structure | folders |
| `local_files` | Index local files → files | files |
| `browser` | Browser history (Safari, Chrome) | visits |
| `chat` | Chat history status (import is manual) | chats, messages |

### Connector (requires `fp connect install google`)

| Stage | Description | Tables |
|-------|-------------|--------|
| `drive_folders` | Scan Drive folder structure | folders |
| `drive_files` | Index Drive files → files | files |
| `gmail` | Gmail messages | emails |

### Post-processing (runs after all data-source stages)

| Stage | Description | Tables |
|-------|-------------|--------|
| `folder_stats` | Refresh `direct_file_count` and `total_size_bytes` on folders | folders |
| `access_resolution` | Stamp visibility + permissions on ingested entities | files, emails, chats, folders, projects, clients (visibility, access) |

Runs last in every pipeline (`local`, `all`, and connector pipelines). Incremental mode only stamps entities added since the last run. Full mode (`--full`) recalculates everything. First run acts as a backfill for existing databases.

---

## Pipelines (internal)

Pipelines are predefined stage groups used internally by the orchestrator. They are not exposed as CLI flags — use `fp ingest refresh <source>` to target a specific data source, or `fp ingest` with no flags to run everything.

Pipelines are resolved dynamically from the core source list and installed connectors.

| Pipeline | Pipes | How resolved |
|----------|-------|--------------|
| `local` | local_folders, local_files, browser, chat, access_resolution | Core sources + post-processing |
| `google` | drive_folders, drive_files, gmail, access_resolution | `ConnectorSpec("google").pipes` + post-processing (when installed) |
| `all` | core + all installed connector sources + access_resolution | Core + `get_connector_sources().keys()` + post-processing |

Connector pipeline names (e.g., `google`) only appear when the connector is installed. Pipeline resolution is internal — use `fp ingest refresh <source>` to target a specific data source.

---

## CLI Flags

| Flag | Short | Description |
|------|-------|-------------|
| `command` | (positional) | `status`, `import`, or `refresh` |
| `--pipe` | `-s` | Comma-separated stage names (power-user escape hatch) |
| `--full` | `-f` | Re-process everything (vs incremental) |
| `--quiet` | `-q` | Suppress output (for scripts) |
| `--preview` | — | Pre-scan summary (no ingest) |
| `--verbose` | `-v` | Verbose logging to file |

### Examples

```bash
# All sources, incremental (default)
fp ingest

# All sources, re-process everything
fp ingest --full

# Show current data counts
fp status

# Re-scan a specific data source (incremental by default)
fp ingest refresh google

# Run specific stages (power-user escape hatch)
fp ingest --pipe local_files,browser

# Rebuild vector store (incremental by default)
fp doctor semantic

# Rebuild vector store from scratch
fp doctor semantic full

# Repair corrupted FTS indexes
fp doctor search
```

---

## Refresh Sources

`fp ingest refresh <source>` re-scans a data source (incremental by default; use `--full` to re-process everything). Refresh sources are convenience aliases that map to one or more stages:

| Refresh source | Stages | Notes |
|----------------|--------|-------|
| `local` | local_folders, local_files | Core |
| `browser` | browser | Core |
| `chat` | chat | Core |
| `gmail` | gmail | Requires Google connector |
| `drive` | drive_folders, drive_files | Requires Google connector |
| `google` | drive_folders, drive_files, gmail | All Google connector stages |
| `all` | *(all installed stages)* | Equivalent to `fp ingest` (all sources) |

---

## Terminology

The ingest system has two user-facing concepts and one internal concept:

| Concept | CLI interface | What it accepts | Defined in |
|---------|--------------|-----------------|------------|
| **Refresh source** | `fp ingest refresh <source>` | Convenience aliases mapping to pipes (e.g., `local` → local_folders + local_files; connectors add their own, such as `drive` → drive_folders + drive_files) | `get_refresh_pipes()` in `registry.py` |
| **Pipe** | `--pipe` (power-user) | Individual processing units. Core pipes include `local_folders`, `local_files`, `browser`, `chat`; connectors add their own (e.g. `gmail` from the Google connector) | `CORE_PIPES` + connector-registered pipes in `registry.py` |
| **Pipeline** *(internal)* | — | Named pipe groups (e.g., `local`, `all`; connectors register their own, such as `google`) resolved by the orchestrator | `get_pipelines()` in `registry.py` |

**`fp ingest refresh <source>`** is the primary way to target a specific data source. **`--pipe`** is a power-user escape hatch for running individual processing units directly. **Pipelines** are an internal orchestrator concept — they determine which pipes run together but are not exposed as CLI flags.

---

## Run Modes

- **Incremental (default)**: Only process new/updated items since the last successful run.
- **Full (`--full`)**: Re-process everything. Useful after schema changes or when data needs a full refresh.

---

## Write Behavior

### Commit Strategy

Adapters use batch commits — multiple records are inserted within a transaction, with a single commit per batch (default: 1000 records). The shared `ingest_entries()` helper manages commit boundaries when `conn` is provided.

### FTS Trigger Management

In **full mode** (`--full`), the pipeline drops FTS sync triggers before running stages and rebuilds FTS indexes after all stages complete. This avoids per-row FTS shadow table writes during bulk ingest, which can cause corruption under sustained load.

In **incremental mode** (default), FTS triggers remain active — the volume is low enough that per-row updates are safe.

The pipeline runs an FTS health probe at startup. If corruption is detected, it logs a warning with the repair command: `fp doctor search`.

### FTS Repair

`fp doctor search` drops and rebuilds all FTS search indexes from base table data.

---

### Vectorization

Vectorization happens at ingest time — files are vectorized in `file_indexer.py` after `insert_file()`, and chat messages are vectorized in `chat_indexer.py` after import. Use `fp doctor semantic full` to rebuild the vector store from scratch.

### Chat Deduplication

Chat deduplication runs during chat ingest to detect and merge near-duplicate conversations. Detection uses three passes, each progressively more expensive:

1. **Exact title match** — normalized (lowercased, stripped) title comparison. Confidence: high.
2. **Fuzzy title match** — `SequenceMatcher` ratio >= 0.85. Confidence: high.
3. **Message content overlap** — SHA-256 hashes of message content; >= 50% hash intersection. Confidence: medium.

When duplicates are found and merged:

- Unique messages from the source chat are moved to the target chat. Messages already present (by content hash) are skipped.
- The source chat is marked `status='merged'` with `merged_into_id` pointing to the target.
- Vector embeddings for the source chat are deleted; the caller re-vectorizes moved messages.

Source: `footprinter/ingest/chat_dedup.py`.

