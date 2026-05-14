# Footprinter

[![Tests](https://github.com/harringjohn/footprinter-cli/actions/workflows/test.yml/badge.svg)](https://github.com/harringjohn/footprinter-cli/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/footprinter-cli)](https://pypi.org/project/footprinter-cli/)

**A local context layer for your files, browser history, chats, and email — searchable, user-owned, and served to AI agents through [MCP](https://modelcontextprotocol.io/).**

Your work lives across filesystems, browsers, inboxes, chat histories, and other tools. Footprinter indexes those sources into a single local store, organizes them into the projects and groupings you define, and serves the result to AI agents through a governed access layer. You control what the agent can see. Everything stays on your machine.

## Install

Requires **Python 3.11+** and **macOS 13+** or **Linux**. The install script checks your Python version and handles the rest:

```bash
curl -fsSL https://raw.githubusercontent.com/harringjohn/footprinter-cli/main/scripts/release/install.sh | bash
```

Or install with **pipx** directly:

```bash
pipx install footprinter-cli
```

Either method gives you the `fp` command with the indexing pipeline, CLI, MCP server, and HTTP API. Optional extras add more:

| Extra | What it adds |
|-------|-------------|
| `[semantic]` | Semantic search via ChromaDB + ONNX embeddings |
| `[parse]` | PDF, Word, Excel, PowerPoint content extraction |
| `[full]` | Both of the above |

To install with extras: use the [full install script](https://raw.githubusercontent.com/harringjohn/footprinter-cli/main/scripts/release/install-full.sh), or `pipx install 'footprinter-cli[full]'`.

<details>
<summary>Troubleshooting & alternative install methods</summary>

**Python version:** Stock macOS ships Python 3.9. Install 3.11+ from [python.org](https://www.python.org/downloads/) or `brew install python@3.11`.

**macOS caveats:**
- zsh treats `[...]` as a glob — quote extras specifiers: `'footprinter-cli[full]'`
- System/Homebrew Python blocks bare `pip install` (PEP 668) — use pipx or a venv instead

**Inside an existing venv:** `pip install footprinter-cli` works as expected.

**Full Disk Access:** Required for browser history indexing on macOS. `fp setup` will prompt you when needed.

**ChromaDB telemetry:** Footprinter sets `anonymized_telemetry=False`. ChromaDB also removed product telemetry in v1.5.4. See [Chroma OSS overview](https://docs.trychroma.com/docs/overview/oss).

</details>

### Uninstall

```bash
fp uninstall                        # remove MCP entry + user data
pipx uninstall footprinter-cli      # remove the package
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
| **Email** | Subject, sender, recipients, body, timestamps |
| **Documents** | PDF, Word, Excel, PowerPoint content (with `[parse]` extra) |
| **Semantic embeddings** | Conceptual similarity across all sources (with `[semantic]` extra) |

What lands in the database — and when — is controlled by the **content storage tier** you opt into. By default, Footprinter only indexes metadata; it does not read your file content until you explicitly enable it. See [Content Storage](https://github.com/harringjohn/footprinter-cli/blob/main/reference/content-storage.md) for the full breakdown.

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

## Architecture

Single-process CLI with optional MCP server. SQLite database. No containers, no cloud, no accounts.

Sources are scanned into SQLite with bidirectional links connecting local files to remote backups via content hash matching. Embeddings are generated at ingest time for semantic search. The MCP server exposes indexed data with two-layer access control (visibility + permissions) — you decide what agents can see.

## Documentation

- [Interfaces](https://github.com/harringjohn/footprinter-cli/blob/main/reference/interfaces.md) — CLI commands, MCP tools, Python API
- [Data Model](https://github.com/harringjohn/footprinter-cli/blob/main/reference/data-model.md) — database schema
- [Pipeline](https://github.com/harringjohn/footprinter-cli/blob/main/reference/pipeline.md) — indexing stages and configuration
- [Content Storage](https://github.com/harringjohn/footprinter-cli/blob/main/reference/content-storage.md) — metadata vs. snippet vs. full-content tiers
- [Access Control](https://github.com/harringjohn/footprinter-cli/blob/main/reference/mcp-access-control.md) — MCP security model

## Contributing

Bug fixes, documentation, and tests welcome. For new features or architectural changes, [open an issue](https://github.com/harringjohn/footprinter-cli/issues) first to discuss the approach.

### Development setup

```bash
git clone https://github.com/harringjohn/footprinter-cli.git
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

Never commit API keys, tokens, or credentials. Report security vulnerabilities privately — see [SECURITY.md](https://github.com/harringjohn/footprinter-cli/blob/main/SECURITY.md).

### Pull request expectations

- Tests must pass
- No breaking changes to existing CLI commands
- Fill out the PR template
- One logical change per PR

All PRs are reviewed by the maintainer. Expect reviews within one week. CI must pass before review begins.

No Contributor License Agreement required. By submitting a PR, you agree your contribution is licensed under the project's [MIT License](https://github.com/harringjohn/footprinter-cli/blob/main/LICENSE).

## Community

- [Code of Conduct](https://github.com/harringjohn/footprinter-cli/blob/main/CODE_OF_CONDUCT.md)
- [Security Policy](https://github.com/harringjohn/footprinter-cli/blob/main/SECURITY.md)

## License

MIT — see [LICENSE](https://github.com/harringjohn/footprinter-cli/blob/main/LICENSE).
