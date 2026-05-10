# Footprinter

[![Tests](https://github.com/harringjohn/footprinter-cli/actions/workflows/test.yml/badge.svg)](https://github.com/harringjohn/footprinter-cli/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/footprinter-cli)](https://pypi.org/project/footprinter-cli/)

**A local context layer for your files, browser history, chats, and email — searchable, user-owned, and served to AI agents through [MCP](https://modelcontextprotocol.io/).**

Your work lives across filesystems, browsers, inboxes, chat histories, and other tools. Footprinter indexes those sources into a single local store, organizes them into the projects and groupings you define, and serves the result to AI agents through a governed access layer. You control what the agent can see. Everything stays on your machine.

## Prerequisites

- **Python 3.11 or newer.** Stock macOS ships with Python 3.9, which won't work — install a newer Python from [python.org](https://www.python.org/downloads/) (recommended) or via `brew install python@3.11`.
- **macOS 13+** or **Linux**.
- **Full Disk Access on macOS** for browser history indexing. Grant it to your terminal app under *System Settings → Privacy & Security → Full Disk Access*. `fp setup` will guide you through this when needed.

## Install

The fastest path on a clean machine is the install script — it ensures Python 3.11+ is present and installs `footprinter-cli`:

```bash
# Base install (CLI + MCP + HTTP API)
curl -fsSL https://raw.githubusercontent.com/swellcitygroup/footprinter/main/scripts/release/install.sh | bash

# Full install (adds semantic search + document parsing)
curl -fsSL https://raw.githubusercontent.com/swellcitygroup/footprinter/main/scripts/release/install-full.sh | bash
```

If you prefer to manage the install yourself, use **pipx** (recommended) — it isolates Footprinter and sidesteps the macOS install caveats noted below:

```bash
brew install pipx
pipx ensurepath           # then restart your terminal
pipx install footprinter-cli
pipx install 'footprinter-cli[full]'   # with semantic + parse
```

> **macOS caveats for manual installs:**
> - **zsh** treats `[...]` as a glob, so keep the single quotes around any bracketed extras specifier (e.g. `'footprinter-cli[full]'`). Without quotes you'll see `zsh: no matches found`.
> - **System and Homebrew Python** ship with PEP 668 enabled, which blocks bare `pip install` outside a venv. Use pipx (above) instead.
> - **python.org-distributed Python** doesn't enforce PEP 668, so a bare `pip install footprinter-cli` outside pipx or a venv may succeed but place `fp` in `/Library/Frameworks/Python.framework/Versions/<x.y>/bin`, which isn't on `PATH` by default. Use pipx (above) or the install script.

Inside an existing venv, `pip` works as expected:

```bash
./venv/bin/pip install footprinter-cli
./venv/bin/pip install 'footprinter-cli[full]'
```

The base install includes the indexing pipeline, CLI, MCP server, HTTP API, and token encryption. Optional extras add more:

| Extra | What it adds |
|-------|-------------|
| `[semantic]` | Semantic search via ChromaDB + ONNX embeddings |
| `[parse]` | PDF, Word, Excel, PowerPoint content extraction |
| `[full]` | All optional extras (semantic + parse) |

> **Privacy note:** The `[semantic]` extra installs ChromaDB. Footprinter initializes
> the ChromaDB client with `anonymized_telemetry=False`, so no telemetry is sent
> regardless of which version pip resolves. ChromaDB also removed product telemetry
> entirely in version 1.5.4. See
> [Chroma OSS overview](https://docs.trychroma.com/docs/overview/oss) for details.

### Uninstall

`fp uninstall` cleans up the MCP entry and user data first, then run the appropriate package uninstall:

```bash
fp uninstall                                    # remove MCP entry + user data
pipx uninstall footprinter-cli                  # if you installed via pipx
./venv/bin/pip uninstall footprinter-cli        # if you installed inside a venv
```

## Quick Start

```bash
fp setup     # Configure sources (interactive wizard)
fp ingest    # Index your files
fp status    # See what's indexed
fp search "meeting notes"   # Find things
```

A few first-run notes:

- The first ingest is implicitly full; subsequent runs are incremental. If you change exclusions or add directories after the first run, re-run with `fp ingest --full` so previously skipped files get picked up.
- With `[semantic]` or `[full]`, the **first ingest downloads ~80MB** of ONNX embedding model weights. It's a one-time cost — subsequent ingests are fast.
- Keep the directories you want indexed **outside `~/Downloads`** — the default exclusion list skips it.

## Connect to Claude Desktop

Footprinter includes an MCP server that gives Claude Desktop (or any MCP client) structured access to your indexed data:

```bash
fp setup mcp --claude    # Configure MCP for Claude Desktop
```

After running this, **fully quit Claude Desktop (Cmd+Q) and relaunch** before the Footprinter tools appear in the conversation tools list. A simple window close isn't enough — the app keeps running in the menu bar.

Once configured, Claude can search your files, browse projects, and find related conversations — through natural language.

## What It Indexes

| Source | What's captured |
|--------|----------------|
| **Local files** | Path, type, size, timestamps, content hash |
| **Browser history** | Safari and Chrome — URLs, titles, visit times |
| **Chat exports** | Claude and ChatGPT conversation exports |
| **Email** | Subject, sender, recipients, body, timestamps — ingested via [connector plugins](#connectors) |
| **Documents** | PDF, Word, Excel, PowerPoint content (with `[parse]` extra) |
| **Semantic embeddings** | Conceptual similarity across all sources (with `[semantic]` extra) |

What lands in the database — and when — is controlled by the **content storage tier** you opt into. By default, Footprinter only indexes metadata; it does not read your file content until you explicitly enable it. See [Content Storage](https://github.com/swellcitygroup/footprinter/blob/main/reference/content-storage.md) for the full breakdown.

Additional sources are available through [connector plugins](#connectors).

## CLI Commands

All commands use the `fp` entry point.

| Command | Purpose |
|---------|---------|
| `fp setup` | Configure sources and integrations |
| `fp ingest` | Run the indexing pipeline |
| `fp status` | System health and data counts |
| `fp search` | Search across all indexed sources |
| `fp connect` | Manage optional integrations |
| `fp mcp` | MCP server and access policies |
| `fp api` | Start the HTTP API server |
| `fp view` | Browse indexed data (files, folders, projects, clients, chats, emails, visits) |
| `fp upsert` | Create/update records, assign relationships, or soft-delete via `--status removed` |
| `fp data` | Export data, generate templates, or import metadata corrections |
| `fp delete` | Hard-delete a super entity (irreversible) |
| `fp vectorize` | Manage per-record vectorization control |
| `fp doctor` | Post-install health check (Python version, install location, FDA, MCP wiring) |
| `fp uninstall` | Remove Footprinter — MCP entry, user data, package |

Run `fp <command> --help` for full usage.

## Connectors

Connector plugins add external data sources like email, cloud storage, and third-party services. They install alongside Footprinter and register automatically:

```bash
pip install footprinter-<name>
```

First-party and community connectors are in development — check the repository for updates.

Use `fp connect list` to see available connectors and their status.

## Architecture

Single-process CLI with optional MCP server. SQLite database. No containers, no cloud, no accounts.

Sources are scanned into SQLite with bidirectional links connecting local files to remote backups via content hash matching. Embeddings are generated at ingest time for semantic search. The MCP server exposes indexed data with two-layer access control (visibility + permissions) — you decide what agents can see.

## Requirements

- Python 3.11+
- macOS 13+ or Linux
- Full Disk Access on macOS (for browser history)

## Documentation

- [Interfaces](https://github.com/swellcitygroup/footprinter/blob/main/reference/interfaces.md) — CLI commands, MCP tools, Python API
- [Data Model](https://github.com/swellcitygroup/footprinter/blob/main/reference/data-model.md) — database schema
- [Pipeline](https://github.com/swellcitygroup/footprinter/blob/main/reference/pipeline.md) — indexing stages and configuration
- [Content Storage](https://github.com/swellcitygroup/footprinter/blob/main/reference/content-storage.md) — metadata vs. snippet vs. full-content tiers
- [Access Control](https://github.com/swellcitygroup/footprinter/blob/main/reference/mcp-access-control.md) — MCP security model

## Contributing

Bug fixes, documentation, and tests welcome. For new features or architectural changes, [open an issue](https://github.com/swellcitygroup/footprinter/issues) first to discuss the approach.

Connector plugins use an internal API that isn't stable yet — we're not accepting connector contributions at this time.

### Development setup

```bash
git clone https://github.com/swellcitygroup/footprinter.git
cd footprinter
python3 -m venv venv
./venv/bin/pip install -e ".[dev]"
```

### Running tests

```bash
./venv/bin/pytest tests/ -v --tb=short
```

### Code style

- PEP 8
- Type hints on function signatures
- `logging` over `print()` in library code

### Workflow

1. Fork the repository
2. Create a feature branch from `main`
3. Write tests (TDD preferred — tests before implementation)
4. Run the test suite
5. Submit a PR targeting `main`

Never commit API keys, tokens, or credentials. Report security vulnerabilities privately — see [SECURITY.md](https://github.com/swellcitygroup/footprinter/blob/main/SECURITY.md).

### Pull request expectations

- Tests must pass
- No breaking changes to existing CLI commands
- Fill out the PR template
- One logical change per PR

All PRs are reviewed by the maintainer. Expect reviews within one week. CI must pass before review begins.

No Contributor License Agreement required. By submitting a PR, you agree your contribution is licensed under the project's [MIT License](https://github.com/swellcitygroup/footprinter/blob/main/LICENSE).

## Community

- [Code of Conduct](https://github.com/swellcitygroup/footprinter/blob/main/CODE_OF_CONDUCT.md)
- [Security Policy](https://github.com/swellcitygroup/footprinter/blob/main/SECURITY.md)

## License

MIT — see [LICENSE](https://github.com/swellcitygroup/footprinter/blob/main/LICENSE).
