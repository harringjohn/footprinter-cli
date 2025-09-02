# Footprinter

[![Tests](https://github.com/swellcitygroup/footprinter/actions/workflows/test.yml/badge.svg)](https://github.com/swellcitygroup/footprinter/actions/workflows/test.yml)

**A local context layer for your files, browser history, chats, and email — searchable, user-owned, and served to AI agents through [MCP](https://modelcontextprotocol.io/).**

Your work lives across a filesystem, a browser, an inbox, a chat history, and whatever other tools you reach for. Footprinter indexes those sources into a single local store, organizes them into the projects and groupings *you* define, and serves the result to AI agents through a governed access layer. You control what the agent can see. Everything stays on your machine.

## Install

```bash
pip install footprinter-cli
```

The base install includes the indexing pipeline, CLI, MCP server, HTTP API, and token encryption. Optional extras add more capabilities:

```bash
pip install footprinter-cli[full]       # All optional extras (semantic + parse)
pip install footprinter-cli[semantic]   # Semantic search (ChromaDB + ONNX embeddings)
pip install footprinter-cli[parse]      # PDF, Word, Excel, PowerPoint content extraction
```

## Quick Start

```bash
fp setup     # Configure sources (interactive wizard)
fp ingest    # Index your files
fp status    # See what's indexed
fp search "meeting notes"   # Find things
```

**macOS note:** Browser history indexing requires Full Disk Access for your terminal app (System Settings > Privacy & Security > Full Disk Access).

## Connect to Claude Desktop

Footprinter includes an MCP server that gives Claude Desktop (or any MCP client) structured access to your indexed data:

```bash
fp setup mcp    # Configure MCP for Claude Desktop
```

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
| `fp upsert` | Create or update records and assign relationships |
| `fp data` | Export data, generate templates, or import metadata corrections |
| `fp delete` | Soft-delete a record |
| `fp vectorize` | Manage per-record vectorization control |

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

## Optional Extras

| Extra | What it adds |
|-------|-------------|
| `[semantic]` | Semantic search via ChromaDB + ONNX embeddings |
| `[parse]` | PDF, Word, Excel, PowerPoint content extraction |
| `[full]` | All optional extras (semantic + parse) |

> **Privacy note:** The `[semantic]` extra installs ChromaDB, which bundles PostHog analytics.
> ChromaDB collects anonymous usage telemetry by default. Set `ANONYMIZED_TELEMETRY=False`
> in your environment to disable it. See
> [ChromaDB telemetry docs](https://docs.trychroma.com/docs/overview/telemetry) for details.

## Requirements

- Python 3.11+
- macOS or Linux
- Full Disk Access on macOS (for browser history)

## Documentation

- [Interfaces](https://github.com/swellcitygroup/footprinter/blob/main/reference/interfaces.md) — CLI commands, MCP tools, Python API
- [Data Model](https://github.com/swellcitygroup/footprinter/blob/main/reference/data-model.md) — database schema
- [Pipeline](https://github.com/swellcitygroup/footprinter/blob/main/reference/pipeline.md) — indexing stages and configuration
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
pytest tests/ -v --tb=short
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
