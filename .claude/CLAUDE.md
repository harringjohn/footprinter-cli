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

## Project-Specific Rules

This is a **public repo**. Additional constraints beyond global rules:
- No direct pushes to `main` — all changes go through PRs
- No credentials, API keys, or internal references in any committed file
- No references to internal tooling, private repos, or org-specific infrastructure

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
