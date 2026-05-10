# Layer Map — Curated History Rebuild for `swellcitygroup/footprinter`

**Status:** working artifact — does not enter the public repo.
**Issue:** FPR-1665 (parent FPR-1648).
**Source snapshots:**
- `v1.0.0` = `1f06634` (orphan snapshot, 292 files) — defines the v1.0.0 tag-tree.
- `v1.0.1` = `84908ba` (orphan snapshot, 305 files) — defines the v1.0.1 tag-tree.
- `HEAD` (current main) — defines the v1.0.2 tree. The build script copies from
  HEAD at execution time, so it automatically includes any commits merged after
  this map was written.
**Delta:** 33 files differ between v1.0.0 and v1.0.1 (8 added, 25 modified).

**Layer count:** 23 (17 pre-v1.0.0 + 3 post-v1.0.0 delta + 3 v1.0.2 standalone).

## Conventions

- Each layer uses a conventional commit subject (`feat:` / `fix:` / `chore:` /
  `docs:`). All subjects are ≤72 characters.
- Tests are co-located with the layer that introduces the production code
  they exercise — never lumped at the end.
- Each layer is independently importable (no forward references). Only modules
  introduced in this or an earlier layer may be imported.
- **Modified-file dual listing:** the 25 files modified between v1.0.0 and
  v1.0.1 appear **twice** — once in their architectural pre-v1.0.0 base layer
  with their **v1.0.0 content**, and once in a v1.0.1 delta layer with their
  **v1.0.1 content**. Files added in v1.0.1 (8 of them) appear only in delta
  layers. The build script (sibling issue) is responsible for checking out
  the right blob for each layer.
- **v1.0.2 layers** do not list individual files. The build script copies the
  complete working tree from `HEAD` and stages changes thematically. The final
  v1.0.2 layer uses `git add -A` as a catch-all, ensuring the tip tree matches
  HEAD exactly.
- Tag markers (`→ tag v1.0.0`, `→ tag v1.0.1`, `→ tag v1.0.2`) are placed on
  their own line immediately after the final commit at that boundary.

---

## Pre-v1.0.0 base (layers 1–17)

### Layer 1 — `chore: project scaffolding`

Top-level packaging, license, and package init. Zero dependencies on
anything else; everything builds on top.

Files (6):
- `pyproject.toml`
- `LICENSE`
- `README.md`
- `.gitignore`
- `.env.example`
- `footprinter/__init__.py`

### Layer 2 — `feat: utility primitives and path resolution`

Pure Python helpers: time/timestamp normalization, mime sniffing, hashing,
text utils, logging config, and the `paths` module that resolves on-disk
locations. Imports only stdlib + `footprinter/__init__.py`.

Files (14):
- `footprinter/paths.py`
- `footprinter/utils/__init__.py`
- `footprinter/utils/hash_utils.py`
- `footprinter/utils/logging_config.py`
- `footprinter/utils/mime.py`
- `footprinter/utils/text.py`
- `footprinter/utils/time.py`
- `tests/test_utils/__init__.py`
- `tests/test_utils/test_mime_utils.py`
- `tests/test_utils/test_paths.py`
- `tests/test_utils/test_timestamp_standardization.py`
- `tests/test_utils/test_utc_now.py`
- `tests/test_paths_no_test_marker.py`
- `tests/test_logging.py`

### Layer 3 — `feat: bundled config and pattern catalogs`

YAML pattern catalogs and the example config shipped with the package.
Loaded by paths/utils only.

Files (9):
- `footprinter/bundled/__init__.py`
- `footprinter/bundled/config.example.yaml`
- `footprinter/bundled/patterns/context_patterns.yaml`
- `footprinter/bundled/patterns/extensions.yaml`
- `footprinter/bundled/patterns/filename_patterns.yaml`
- `footprinter/bundled/patterns/mime_mappings.yaml`
- `footprinter/bundled/patterns/salesforce_rules.yaml`
- `footprinter/bundled/patterns/security_patterns.yaml`
- `tests/test_bundled.py`

### Layer 4 — `feat: database schema and entity data model`

SQLite schema, migrations, connection helpers, and the core entity tables
(files, folders, projects, clients, uploads). Source registry lives here
because schema definitions reference source kinds. Depends on layers 1–3.

Files (25):
- `footprinter/db/__init__.py`
- `footprinter/db/sql_utils.py`
- `footprinter/db/files.py`
- `footprinter/db/folders.py`
- `footprinter/db/projects.py`
- `footprinter/db/clients.py`
- `footprinter/db/uploads.py`
- `footprinter/source_registry.py`
- `footprinter/ingest/db/__init__.py`
- `footprinter/ingest/db/connector_schema.py`
- `footprinter/ingest/db/migration.py`
- `footprinter/ingest/db/schema.py`
- `footprinter/ingest/db/security.py`
- `footprinter/ingest/database.py`
- `tests/test_db/__init__.py`
- `tests/test_db/conftest.py`
- `tests/test_db/test_db_schema.py`
- `tests/test_db/test_db_module.py`
- `tests/test_db/test_db_files.py`
- `tests/test_db/test_db_folders.py`
- `tests/test_db/test_db_folders_cascade.py`
- `tests/test_db/test_db_uploads.py`
- `tests/test_db/test_db_sources.py`
- `tests/test_files_rename.py`
- `tests/test_files_surface.py`

### Layer 5 — `feat: visibility, permissions, and access model`

Access policies, visibility computation, role-based permission checks, and
the policy table. Depends on entity tables (layer 4) — every access decision
joins against files/folders/projects.

Files (11):
- `footprinter/access.py`
- `footprinter/visibility.py`
- `footprinter/permissions.py`
- `footprinter/db/policies.py`
- `tests/test_db/test_db_policies.py`
- `tests/test_access_control_bypasses.py`
- `tests/test_access_control_docs.py`
- `tests/test_security_layer.py`
- `tests/test_security_permissions.py`
- `tests/test_inherit_resolution.py`
- `tests/test_resolver.py`

### Layer 6 — `feat: file indexing pipeline`

Filesystem scanner, file/folder indexers, content extractors, and the
local-files/local-folders adapters. The adapter `protocol` lives here
because the file adapters are the first concrete implementations.
Depends on layers 1–5.

Files (17):
- `footprinter/ingest/file_scanner.py`
- `footprinter/ingest/file_indexer.py`
- `footprinter/ingest/folder_indexer.py`
- `footprinter/ingest/content_extractors.py`
- `footprinter/ingest/full_content_extractor.py`
- `footprinter/ingest/adapters/__init__.py`
- `footprinter/ingest/adapters/protocol.py`
- `footprinter/ingest/adapters/ingest.py`
- `footprinter/ingest/adapters/local_files.py`
- `footprinter/ingest/adapters/local_folders.py`
- `tests/test_ingest/__init__.py`
- `tests/test_ingest/test_adapter_protocol.py`
- `tests/test_ingest/test_local_adapters.py`
- `tests/test_ingest/test_file_indexer.py`
- `tests/test_ingest/test_file_indexing.py`
- `tests/test_ingest/test_file_scanner_exceptions.py`
- `tests/test_ingest/test_content_extractors.py`

### Layer 7 — `feat: ingest orchestrator and pipe runner`

Orchestrator, processing pipeline, registry, run-record tracking, and ingest
status. Wraps the file pipeline (layer 6) into named pipes and runs them.
Status table joins ingest activity with entity tables (layer 4). Tests for
project-root resolution, pipe rename, and access resolution during ingest
land here.

Files (27):
- `footprinter/ingest/__init__.py`
- `footprinter/ingest/orchestrator.py`
- `footprinter/ingest/pipe_runner.py`
- `footprinter/ingest/processing.py`
- `footprinter/ingest/registry.py`
- `footprinter/ingest/run_record.py`
- `footprinter/ingest/status.py`
- `footprinter/ingest/cli.py`
- `footprinter/db/status.py`
- `tests/test_db/test_db_status.py`
- `tests/test_ingest/test_orchestrator.py`
- `tests/test_ingest/test_orchestrator_ux.py`
- `tests/test_ingest/test_pipeline_runner.py`
- `tests/test_ingest/test_processing_pipeline.py`
- `tests/test_ingest/test_pipe_rename_smoke.py`
- `tests/test_ingest/test_registry.py`
- `tests/test_ingest/test_run_logging.py`
- `tests/test_ingest/test_run_record.py`
- `tests/test_ingest/test_source_registry.py`
- `tests/test_ingest/test_ingest_helper.py`
- `tests/test_ingest/test_ingest_rename.py`
- `tests/test_ingest/test_pipeline_access_resolution.py`
- `tests/test_ingest/test_no_google_in_ingest.py`
- `tests/test_ingest/test_file_project_inheritance.py`
- `tests/test_no_project_root.py`
- `tests/test_build_status_filter.py`
- `tests/test_edit_recalculate.py`

### Layer 8 — `feat: browser history indexing (Safari, Chrome)`

Browser-specific db table, indexer, and adapter. Plugs into the orchestrator
from layer 7.

Files (5):
- `footprinter/db/browser.py`
- `footprinter/ingest/browser_indexer.py`
- `footprinter/ingest/adapters/browser.py`
- `tests/test_db/test_db_browser.py`
- `tests/test_ingest/test_browser_platform.py`

### Layer 9 — `feat: chat conversation import (ChatGPT, Claude)`

Chats/messages/emails db tables, deduplication, parsers for ChatGPT and
Claude exports, and the chat adapter. Depends on layers 4 and 7.

Files (16):
- `footprinter/db/chats.py`
- `footprinter/db/messages.py`
- `footprinter/db/emails.py`
- `footprinter/ingest/chat_dedup.py`
- `footprinter/ingest/chat_indexer.py`
- `footprinter/ingest/chat_parsers/__init__.py`
- `footprinter/ingest/chat_parsers/chatgpt_parser.py`
- `footprinter/ingest/chat_parsers/claude_parser.py`
- `footprinter/ingest/adapters/chat.py`
- `tests/test_db/test_db_chats.py`
- `tests/test_db/test_db_chats_dedup.py`
- `tests/test_db/test_db_messages.py`
- `tests/test_ingest/test_chat_dedup.py`
- `tests/test_ingest/test_chat_indexer.py`
- `tests/test_ingest/test_chat_indexer_import.py`
- `tests/test_ingest/test_chat_upload.py`

### Layer 10 — `feat: services layer (entity business logic)`

Service classes that wrap db tables with business logic, role checks, and
cross-entity composition. Search-DB module lives here because the search
service is its only caller. Includes ingest-service tests because the
service depends on layer 7's orchestrator.

Files (18):
- `footprinter/services/__init__.py`
- `footprinter/services/access_service.py`
- `footprinter/services/chat_service.py`
- `footprinter/services/client_service.py`
- `footprinter/services/content_service.py`
- `footprinter/services/email_service.py`
- `footprinter/services/file_service.py`
- `footprinter/services/folder_service.py`
- `footprinter/services/includes.py`
- `footprinter/services/ingest_service.py`
- `footprinter/services/project_service.py`
- `footprinter/services/roles.py`
- `footprinter/services/search_service.py`
- `footprinter/services/semantic_service.py`
- `footprinter/services/status_service.py`
- `footprinter/services/visit_service.py`
- `footprinter/db/search.py`
- `tests/test_db/test_db_search.py`
- `tests/test_services/__init__.py`
- `tests/test_services/conftest.py`
- `tests/test_services/test_access_service.py`
- `tests/test_services/test_content_service.py`
- `tests/test_services/test_entity_services.py`
- `tests/test_services/test_roles.py`
- `tests/test_services/test_search_service.py`
- `tests/test_services/test_semantic_service.py`
- `tests/test_services/test_status_service.py`
- `tests/test_ingest/test_ingest_service.py`
- `tests/test_access_recalculate.py`

### Layer 11 — `feat: CLI framework and entry point`

`fp` entry point, common option parsing, prompt helpers, policy helpers, and
the bare-args router. No subcommands yet. Depends on services (layer 10).

Files (13):
- `footprinter/cli/__init__.py`
- `footprinter/cli/__main__.py`
- `footprinter/cli/_common.py`
- `footprinter/cli/_prompt.py`
- `footprinter/cli/_policy_helpers.py`
- `tests/test_cli/__init__.py`
- `tests/test_cli/test_cli.py`
- `tests/test_cli/test_cli_bare_args.py`
- `tests/test_cli/test_cli_common.py`
- `tests/test_cli/test_cli_conventions.py`
- `tests/test_cli/test_cli_ux.py`
- `tests/test_cli/test_progress_indicators.py`
- `tests/test_cli/test_fp_router.py`

### Layer 12 — `feat: CLI subcommands — setup, status, view, data, ingest, search`

Per-domain subcommand modules and their tests. Each imports from services
(layer 10) and the CLI framework (layer 11). The MCP, semantic, API, and
connector subcommands land in their own dedicated layers (13/14/15/16).

Files (32):
- `footprinter/cli/setup.py`
- `footprinter/cli/status.py`
- `footprinter/cli/status_cmd.py`
- `footprinter/cli/view.py`
- `footprinter/cli/data.py`
- `footprinter/cli/ingest.py`
- `footprinter/cli/upsert.py`
- `footprinter/cli/delete.py`
- `footprinter/cli/search.py`
- `footprinter/cli/search_cmd.py`
- `footprinter/cli/connect.py`
- `tests/test_cli/test_cli_chat.py`
- `tests/test_cli/test_cli_connect.py`
- `tests/test_cli/test_cli_ingest.py`
- `tests/test_cli/test_cli_search.py`
- `tests/test_cli/test_fp_data.py`
- `tests/test_cli/test_fp_delete.py`
- `tests/test_cli/test_fp_ingest.py`
- `tests/test_cli/test_fp_search.py`
- `tests/test_cli/test_fp_setup.py`
- `tests/test_cli/test_fp_status.py`
- `tests/test_cli/test_fp_upsert.py`
- `tests/test_cli/test_setup_access_subcommand.py`
- `tests/test_cli/test_setup_check.py`
- `tests/test_cli/test_setup_counts.py`
- `tests/test_cli/test_setup_folders_subcommand.py`
- `tests/test_cli/test_setup_mcp_subcommand.py`
- `tests/test_cli/test_setup_wizard.py`
- `tests/test_cli/test_status.py`
- `tests/test_cli/test_status_extract.py`
- `tests/test_cli/test_status_filter_pattern.py`
- `tests/test_cli/test_status_last_run.py`

### Layer 13 — `feat: MCP server for AI agent access`

MCP server (stdio + tools), the `fp mcp` and `fp setup mcp` subcommands,
and prompt-safety guardrails. Depends on services (layer 10) and CLI
framework (layer 11).

Files (26):
- `footprinter/mcp/README.md`
- `footprinter/mcp/__init__.py`
- `footprinter/mcp/__main__.py`
- `footprinter/mcp/db.py`
- `footprinter/mcp/errors.py`
- `footprinter/mcp/extraction.py`
- `footprinter/mcp/server.py`
- `footprinter/mcp/tools/__init__.py`
- `footprinter/mcp/tools/navigation.py`
- `footprinter/mcp/tools/read.py`
- `footprinter/mcp/tools/search.py`
- `footprinter/mcp/tools/semantic.py`
- `footprinter/mcp/tools/status.py`
- `footprinter/cli/mcp_cmd.py`
- `footprinter/cli/mcp_setup.py`
- `tests/test_mcp/__init__.py`
- `tests/test_mcp/test_mcp_cmd.py`
- `tests/test_mcp/test_mcp_cmd_recalculate.py`
- `tests/test_mcp/test_mcp_db_guard.py`
- `tests/test_mcp/test_mcp_errors.py`
- `tests/test_mcp/test_mcp_extraction.py`
- `tests/test_mcp/test_mcp_server.py`
- `tests/test_mcp/test_mcp_setup.py`
- `tests/test_mcp/test_mcp_tools.py`
- `tests/test_cli/test_fp_mcp.py`
- `tests/test_prompt_safety.py`

### Layer 14 — `feat: semantic search with ChromaDB`

ChromaDB-backed vector store, embeddings, chunking, hybrid retrieval, and
the `fp vectorize` subcommand. Depends on services (layer 10).

Files (14):
- `footprinter/semantic/__init__.py`
- `footprinter/semantic/chunking.py`
- `footprinter/semantic/embeddings.py`
- `footprinter/semantic/hybrid_search.py`
- `footprinter/semantic/vector_store.py`
- `footprinter/cli/vectorize_cmd.py`
- `tests/test_semantic/__init__.py`
- `tests/test_semantic/test_chunking.py`
- `tests/test_semantic/test_fts_health.py`
- `tests/test_semantic/test_hybrid_search.py`
- `tests/test_semantic/test_rebuild_vectors.py`
- `tests/test_semantic/test_vector_store.py`
- `tests/test_semantic/test_vectorize_flag.py`
- `tests/test_cli/test_cli_vectorize.py`

### Layer 15 — `feat: HTTP API (FastAPI)`

FastAPI server exposing entity, search, and semantic endpoints, plus the
`fp api` subcommand that launches it. Depends on services (layer 10) and
semantic (layer 14, for the semantic endpoints).

Files (18):
- `footprinter/api/__init__.py`
- `footprinter/api/db.py`
- `footprinter/api/entities.py`
- `footprinter/api/search.py`
- `footprinter/api/semantic.py`
- `footprinter/api/server.py`
- `footprinter/api/status.py`
- `footprinter/cli/api_cmd.py`
- `tests/test_api/__init__.py`
- `tests/test_api/conftest.py`
- `tests/test_api/test_cli.py`
- `tests/test_api/test_db.py`
- `tests/test_api/test_entities.py`
- `tests/test_api/test_search.py`
- `tests/test_api/test_semantic.py`
- `tests/test_api/test_server.py`
- `tests/test_api/test_status.py`
- `tests/test_cli/test_fp_api.py`

### Layer 16 — `feat: connector framework and OAuth token storage`

Connector plugin framework and OS-keyring-backed OAuth token storage. Tiny
layer at v1.0.0 — concrete connectors (Google, etc.) ship later.

Files (5):
- `footprinter/connectors/__init__.py`
- `footprinter/connectors/config_utils.py`
- `tests/test_connectors/__init__.py`
- `tests/test_connectors/test_connectors.py`
- `tests/test_connectors/test_token_storage.py`

### Layer 17 — `chore: reference docs, examples, CI workflows, community files`

Everything that isn't shipped as Python source: reference markdown docs,
example scripts, GitHub Actions, community files (CoC, security policy,
issue/PR templates), maintenance scripts, and end-to-end install/pipeline
tests. Lands last in the v1.0.0 base so the codebase is fully bootable
before docs and CI describe it.

Files (25):
- `reference/data-model.md`
- `reference/interfaces.md`
- `reference/mcp-access-control.md`
- `reference/pipeline.md`
- `examples/README.md`
- `examples/export_chat_history.py`
- `examples/list_recent_files.py`
- `examples/search_across_sources.py`
- `tests/test_examples.py`
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/ISSUE_TEMPLATE/feature_request.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/publish.yml`
- `.github/workflows/test.yml`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `scripts/README.md`
- `scripts/migrate/backfill_md5_hashes.py`
- `scripts/snapshot-qa/smoke.sh`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_e2e_install.py`
- `tests/test_e2e_pipeline.py`
- `tests/test_pip_install_e2e.py`
- `tests/test_package_init.py`

→ tag `v1.0.0`

---

## Post-v1.0.0 delta (layers 18–20)

### Layer 18 — `feat: install scripts, doctor and uninstall commands`

The biggest user-facing addition between v1.0.0 and v1.0.1: `fp doctor`,
`fp uninstall`, polished `fp setup`, and the install shell scripts that
ship as the recommended install path. README install section and version
bump in pyproject ride along.

Files (16) — files marked `(M)` use their **v1.0.1 content** here, having
already appeared with their v1.0.0 content in earlier layers:
- `footprinter/cli/doctor.py` (added)
- `footprinter/cli/uninstall.py` (added)
- `footprinter/cli/__init__.py` (M — registers the new subcommands)
- `footprinter/cli/setup.py` (M — install/setup polish)
- `footprinter/cli/mcp_setup.py` (M)
- `scripts/release/_install_common.sh` (added)
- `scripts/release/install.sh` (added)
- `scripts/release/install-full.sh` (added)
- `README.md` (M — install section)
- `pyproject.toml` (M — version 1.0.1)
- `tests/test_cli/test_cli_doctor.py` (added)
- `tests/test_cli/test_fp_uninstall.py` (added)
- `tests/test_cli/test_setup_wizard.py` (M)
- `tests/test_cli/test_cli_conventions.py` (M)
- `tests/test_cli/test_fp_router.py` (M)
- `tests/test_package_init.py` (M)

### Layer 19 — `feat: ingest pipeline robustness improvements`

Targeted hardening of the ingest path: browser indexer fixes, file scanner
edge cases, orchestrator/processing/registry refinements, and the new
folder-stats pipeline test.

Files (11):
- `footprinter/ingest/browser_indexer.py` (M)
- `footprinter/ingest/file_scanner.py` (M)
- `footprinter/ingest/orchestrator.py` (M)
- `footprinter/ingest/processing.py` (M)
- `footprinter/ingest/registry.py` (M)
- `tests/test_ingest/test_browser_platform.py` (M)
- `tests/test_ingest/test_file_indexing.py` (M)
- `tests/test_ingest/test_orchestrator.py` (M)
- `tests/test_ingest/test_pipeline_access_resolution.py` (M)
- `tests/test_ingest/test_pipeline_folder_stats.py` (added)
- `tests/test_ingest/test_registry.py` (M)

### Layer 20 — `docs: extended reference (chat export, content storage, CSV import)`

Reference doc additions and CSV import templates. Pure docs — no code.

Files (6):
- `reference/chat-export.md` (added)
- `reference/content-storage.md` (added)
- `reference/csv-import.md` (added)
- `reference/data-model.md` (M)
- `reference/clients-template.csv` (added)
- `reference/projects-template.csv` (added)

→ tag `v1.0.1`

---

## v1.0.2 standalone development (layers 21–23)

The first standalone development cycle on the public repo — 30 merged PRs
of bug fixes, a data model refactor, and infrastructure improvements.
Unlike the architectural layers above, these layers do not list individual
files. The build script copies the complete working tree from `HEAD` and
stages changes by theme. The final layer catch-all ensures the tip tree
matches HEAD exactly.

### Layer 21 — `fix: folder/file ingest correctness and performance`

Targeted fixes to the file/folder ingest pipeline: fast-path skip on
unchanged folders, scoped indexing for `fp setup folders add`, config
exclusion pattern enforcement, phantom-folder cleanup, removed-folder
exclusion, folder reactivation on re-scan, and chats FTS repair.

### Layer 22 — `refactor: data scoping — listed/unlisted/removed trichotomy`

Introduces the three-state data disposition model across the entire stack:
database status columns, service-layer filters, access gates, MCP tool
parameters, CLI commands, API endpoints, and reference documentation.
Strips merge functionality and converts `fp delete` to hard-delete.

### Layer 23 — `chore: CLI polish, install scripts, and infrastructure`

Remaining improvements: install script noise suppression and PATH guidance,
`fp doctor` alignment with pyproject extras, `fp uninstall` UX polish,
`fp setup` wizard refinements, API pagination caps, non-loopback bind
security gate, configuration defaults, CONTRIBUTING.md, and test cleanup.

→ tag `v1.0.2`

---

## Cross-checks (run before handoff to build script)

1. **File-count parity (v1.0.1).** Distinct path count across layers 1–20
   = 305. Run:
   ```
   awk '/^### Layer /{ok=1} ok && /^- `[^`]+`/ { gsub(/\(.*\)/,""); gsub(/^[^`]*`|`.*$/,""); print }' \
     .context/plans/history-rebuild/layer-map.md | sort -u | wc -l
   ```
   Expect `305`. (Modified files count once because the `(M)` annotation is
   stripped before sort. v1.0.2 layers have no file listings.)
2. **No duplicate paths in v1.0.0 base.** Layers 1–17 list each path
   exactly once (no `(M)` annotations there):
   ```
   awk '/^### Layer ([1-9]|1[0-7]) /{ok=1} /^### Layer 1[89]|^### Layer 20 /{ok=0} ok && /^- `[^`]+`/ { gsub(/^[^`]*`|`.*$/,""); print }' \
     .context/plans/history-rebuild/layer-map.md | sort | uniq -d | wc -l
   ```
   Expect `0`.
3. **Pre-v1.0.0 layers cover the v1.0.0 tree.** Set-equal to
   `git ls-tree -r --name-only v1.0.0` (292 paths).
4. **Delta layers cover the v1.0.0→v1.0.1 diff.** Set-equal to
   `git diff v1.0.0..v1.0.1 --name-only` (33 paths).
5. **No forward import references.** For each layer, every
   `from footprinter.X import …` resolves to a module in this or an earlier
   layer. Spot-check once the build script reaches each layer.
6. **Conventional commit subjects.** Each `### Layer N — \`<subject>\``
   matches `^(feat|fix|chore|docs|refactor|test|build|ci):` and subject
   length ≤72.
7. **Tag boundaries.** Exactly three `→ tag` markers, in order: `v1.0.0`
   (after layer 17), `v1.0.1` (after layer 20), `v1.0.2` (after layer 23).
8. **Layer count.** 23 layers (20 architectural + 3 v1.0.2 standalone).
9. **v1.0.2 tip matches HEAD.** The tree at v1.0.2 in the rebuilt repo
   must be byte-equal to `HEAD` in the source repo:
   `git rev-parse 'v1.0.2^{tree}'` matches `git rev-parse 'HEAD^{tree}'`
   (run in the source repo).
