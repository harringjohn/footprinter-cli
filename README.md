# Footprinter

> ## ⚠️ This repository is frozen — Footprinter is retired
>
> **Footprinter is no longer published, maintained, or installable.** Every released
> version of the `footprinter-cli` package has been yanked from PyPI, so the install
> commands that used to live in this README no longer resolve. They have been removed
> rather than repointed: this repository is a historical record, not a way in.
>
> **The active product is SWLL — [swll.app](https://swll.app/).**
>
> What remains here is the open-source origin record of that work, kept public and
> MIT-licensed on purpose. Read it, fork it, learn from it. Do not expect it to run.

Footprinter was a local context layer for files, browser history, chats, and email —
searchable, user-owned, and served to AI agents through
[MCP](https://modelcontextprotocol.io/).

The premise: your work lives scattered across filesystems, browsers, inboxes, chat
histories, and other tools. Footprinter indexed those sources into a single local
store, organized them into projects and groupings you defined, and served the result
to AI agents through a governed access layer. You controlled what the agent could
see. Everything stayed on your machine.

---

## What it became

Footprinter became **SWLL**.

| | Published | Licence |
|---|---|---|
| `footprinter-cli` 1.1.1 — the final release, in this repository | 2026-06-21 | MIT |
| `swll` 1.0.0 — the first release under the new name | 2026-06-25 | separate, non-MIT |

Four days apart, and substantially the same codebase. SWLL is where the work
continued; this repository is where it started.

SWLL is a separate product under its own licence — the MIT grant below covers this
repository and the `footprinter-cli` releases only. It does not extend to SWLL.

**Current product: [swll.app](https://swll.app/)**

---

## What it indexed

| Source | What was captured |
|--------|----------------|
| **Local files** | Path, type, size, timestamps, content hash |
| **Browser history** | Safari and Chrome — URLs, titles, visit times |
| **Chat exports** | Claude and ChatGPT conversation exports |
| **Email** | Subject, sender, recipients, body, timestamps |
| **Documents** | PDF, Word, Excel, PowerPoint content (via the `[parse]` extra) |
| **Semantic embeddings** | Conceptual similarity across all sources (via the `[semantic]` extra) |

What landed in the database — and when — was controlled by a **content storage tier**
the user opted into. By default Footprinter indexed metadata only; it did not read
file content until explicitly enabled. See
[Content Storage](reference/content-storage.md) for the full breakdown.

## Architecture

Single-process CLI with an optional MCP server. SQLite database. No containers, no
cloud, no accounts.

Sources were scanned into SQLite with bidirectional links connecting local files to
remote backups via content-hash matching. Embeddings were generated at ingest time
for semantic search. The MCP server exposed indexed data through two-layer access
control (visibility + access), so the user decided what agents could see.

The package shipped three entry points: `fp` (the CLI and indexing pipeline),
`fp-mcp` (the MCP server for AI agents), and `fp-api` (the HTTP API). Optional
extras added semantic search (`[semantic]`), document parsing (`[parse]`), or both
(`[full]`).

## The `fp` command surface

Recorded here as part of the design history. These commands ran against a local
install that is no longer obtainable.

| Command | Purpose |
|---------|---------|
| `fp setup` | Configure sources and integrations |
| `fp ingest` | Run the indexing pipeline |
| `fp status` | System health and data counts |
| `fp search` | Search across all indexed sources |
| `fp connect` | Manage optional integrations |
| `fp permission` | Manage access policies (visibility, permissions) |
| `fp view` | Browse indexed data (files, folders, projects, clients, chats, emails, visits) |
| `fp add` | Create new entity records or import from CSV |
| `fp update` | Update existing records by ID — status, assignments, metadata |
| `fp delete` | Hard-delete a super entity (irreversible) |
| `fp doctor` | Post-install health check (Python version, platform, FDA, MCP wiring) |
| `fp uninstall` | Remove Footprinter — MCP entry, user data, package |

A typical session was `fp setup` to configure sources, `fp ingest` to index, then
`fp search` to retrieve — with `fp setup mcp --claude` wiring the MCP server into
Claude Desktop so an agent could search files, browse projects, and find related
conversations in natural language.

## Documentation

The architecture notes are preserved as written, describing the system as it shipped.

- [Interfaces](reference/interfaces.md) — CLI commands, MCP tools, Python API
- [Data Model](reference/data-model.md) — database schema
- [Pipeline](reference/pipeline.md) — indexing stages and configuration
- [Content Storage](reference/content-storage.md) — metadata vs. snippet vs. full-content tiers
- [Permission Policies and Access Control](reference/permission-policies-and-access-control.md)

Also in the record: [Code of Conduct](CODE_OF_CONDUCT.md) and
[Security Policy](SECURITY.md).

## Contributing

Footprinter is not accepting contributions. The project is frozen and issues and pull
requests are not monitored. For anything relating to the current product, go to
[swll.app](https://swll.app/).

## License

MIT — see [LICENSE](LICENSE). The licence is deliberate and unchanged: this repository
is the open-source origin record, and what was published under MIT stays under MIT.
