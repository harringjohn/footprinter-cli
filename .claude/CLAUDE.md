# fpr-cli — Claude Code Context

## Project Overview

A local context layer for files, browser history, chats, and email — searchable, user-owned, and served to AI agents through MCP. Published as `footprinter-cli`.

## Key Commands

```bash
./venv/bin/pip install -e ".[dev]"        # Dev install
./venv/bin/pytest tests/ -v --tb=short    # Run tests
./venv/bin/ruff check .                   # Lint
```

## Tech Stack

Python 3.11+, SQLite, MCP (Model Context Protocol), FastAPI/Uvicorn, Rich, PyYAML, cryptography. Optional extras: ChromaDB + ONNX (semantic search), pypdf/python-docx/openpyxl/python-pptx (document parsing).

## Footprinter Tooling

The Footprinter MCP tools and `fp` CLI are available in this workspace. Use them
to validate behavior from the consumer side — code shows intent, MCP tools show
reality. When investigating MCP behavior, do BOTH.

**MCP tools (VIEWER role):** `footprinter_status`, `footprinter_search`,
`footprinter_project`, `footprinter_client`, `footprinter_folder`,
`footprinter_semantic`, `footprinter_read`. See `reference/interfaces.md` for
parameter schemas and return shapes.

**`fp` CLI (ADMIN-level):** broader surface for ingest, indexing, and config.
Use when investigating data outside the VIEWER-filtered MCP view.

**Ambient context resources:** `footprinter://context/summary` (live status
snapshot — same payload as `footprinter_status`) and `footprinter://context/guidance`
(tool-selection reference). Read these when orienting on a fresh task.

## Project-Specific Rules

This is a **public repo**. Additional constraints beyond global rules:
- No direct pushes to `main` — all changes go through PRs
- No credentials, API keys, or internal references in any committed file
- No references to internal tooling, private repos, or org-specific infrastructure

## Documentation

**This repo contains only shipped artifacts.** Do not create documentation files, analysis
reports, architecture decisions, or research docs in this directory. The only markdown that
belongs here is: root-level project docs (README, CHANGELOG, SECURITY, CODE_OF_CONDUCT),
`reference/` shipped docs, `.github/` templates, `examples/` READMEs, and `.claude/CLAUDE.md`
plus `.context/plans/`.

All internal documentation lives in `~/Work/skunkworks/footprinter-docs/fpr-cli/`:
- `architecture/` — design docs, decisions
- `process/` — operational docs, release notes
- `findings/` — research, reviews, analysis

Historical docs from the monolith era are in `~/Work/skunkworks/footprinter-docs/fpr-poc/`.
When reading architecture decisions or design docs, check both `fpr-cli/` and `fpr-poc/`
— the POC-era docs remain the authoritative source for many cross-cutting decisions
(e.g. separation boundary, four-entry-point architecture).

## Linear Workflow

Issue lifecycle follows the global protocol (see ~/.claude/CLAUDE.md).
- Move issue to In Progress before writing code
- Move to In Review after PR, never to Done
- Always assign project and milestone when creating issues

### Linear Defaults

Single source of truth for all skills and commands that interact with Linear.
**Update this table when Linear workspace configuration changes** — skills reference it by name, not hardcoded values.

| Field | Value | Notes |
|-------|-------|-------|
| Team | FootPrinter | Linear team name (key: FPR) |
| Issue prefix | FPR | Derived from team key |
| Project | FPR CLI | Active project for issue assignment |
