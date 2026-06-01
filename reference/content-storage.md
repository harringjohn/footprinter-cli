# Content Storage Tiers

Footprinter has three distinct content storage tiers. This page explains what each tier stores, what it doesn't, and the single config flag (`content_snippets`) that marks the boundary where Footprinter starts storing copies of your file content.

For the schema columns referenced here, see `data-model.md`. For how stored content is gated when an AI assistant reads it, see `mcp-access-control.md`.

---

## Principle

**Footprinter does not read your content until you explicitly tell it to.**

You can point Footprinter at your entire home directory in Tier 0 and know that only the directory structure and file metadata are indexed. Content stays at its source — on disk or in a remote store — until you choose otherwise.

---

## The Three Tiers

| | Tier 0 — Metadata only | Tier 1 — Content snippets | Tier 2 — Full content features |
|---|---|---|---|
| **Switch** | Default — no action needed | `indexing.content_snippets: true` (set in `fp setup` or `config.yaml`) | `pipx install "footprinter-cli[semantic]"` and/or `[parse]` (or `[full]` for both) |
| **Package** | Base | Base | Base + extras |
| **What gets read from disk** | Names, paths, sizes, timestamps | Names, paths, sizes, timestamps **+ first ~1000 chars of text-readable files** | Tier 1 + full text from PDF / Word / Excel / PowerPoint via `[parse]`; chunked content embedded into vectors via `[semantic]` |
| **What lands in Footprinter's database** | Catalog metadata only — no file content | Catalog metadata + `files.content_preview` (and `emails.body_preview` for connectors) | Tier 1 storage + ChromaDB vectors (separate local store) |
| **Keyword search (FTS5) matches** | `name` only | `name`, `content_preview` | Same as Tier 1, plus semantic (vector) search by meaning |
| **Read-on-demand via MCP** | `footprinter_read` reads file bytes live from disk through the access-gated tool — never stored | Same as Tier 0 | Same as Tier 0 |

The MCP read path is the same in every tier: when an AI assistant calls `footprinter_read`, file content is read from disk at request time and returned through the permission-gated tool. It is never written into the database. Tiers describe what *ingest* stores, not what reads can access.

---

## Tier 0 — Metadata only (default)

**You get this without doing anything.** A fresh `pipx install footprinter-cli` followed by `fp setup` (with content snippets declined) sits at Tier 0.

- Ingest catalogs file names, paths, sizes, timestamps, hashes, and structure.
- FTS5 keyword search matches the `name` column. A search for "invoice" finds files literally named `invoice-2024.pdf` but not files whose contents mention "invoice".
- The MCP `footprinter_read` tool can still return file bytes on demand — those reads happen against the live filesystem, not a stored copy.
- No file content enters Footprinter's database.

This is the safe-by-default tier. Everything that exists at Tier 0 is information you've handed Footprinter implicitly by pointing it at a directory: that the file exists, where it lives, and how big it is.

---

## Tier 1 — Content snippets (`content_snippets: true`)

**This is the trust boundary.** Setting `indexing.content_snippets: true` (via the setup wizard prompt or directly in `config.yaml`) is the first place Footprinter stores actual file content in its database.

- During ingest, Footprinter reads each file and extracts up to ~1000 characters of preview text.
- Previews are stored in the `files.content_preview` column. The Gmail connector (when installed) writes equivalent previews to `emails.body_preview`.
- FTS5 keyword search now matches `name` and `content_preview`. A search for "invoice" finds files whose contents mention "invoice", not just files named that way.
- Search snippets in results show the matched preview, not just the filename.

**Why this is the boundary:** in Tier 0 there is no copy of file content anywhere in Footprinter's database. In Tier 1 there is a small but real copy — the preview. That preview lives in SQLite, follows the same access controls as the rest of the catalog (see `mcp-access-control.md`), and stays on your machine. But it is a copy, and it persists across reboots until ingest re-runs or the row is removed.

Tier 1 ships in the base package — no extras, no extra dependencies. The `content_preview` column exists in the schema regardless; the flag just controls whether ingest populates it.

---

## Tier 2 — Full content features (`[semantic]` / `[parse]` / `[full]` extras)

**Opt-in via `pip` extras.** These features extend Tier 1 with more storage and richer search, in exchange for additional dependencies and disk space.

- **`[semantic]`** — installs `chromadb` and a sentence-transformer model. Ingest chunks file and chat content into vector embeddings stored in a local ChromaDB collection. Semantic search becomes available via `fp search` and the MCP `footprinter_semantic` tool. Embeddings live alongside the SQLite database; vectors are derived from the same text that Tier 1 stores as previews (or from extracted parse output, if `[parse]` is also installed).
- **`[parse]`** — installs document parsers (`pypdf`, `python-docx`, `openpyxl`, etc.). Ingest can extract text from PDFs, Word, Excel, and PowerPoint files instead of skipping them or storing only filename metadata. Without `[parse]`, those file types contribute filename-only matches even at Tier 1.
- **`[full]`** — convenience alias that installs both `[semantic]` and `[parse]`.

Vectorization is independently switchable per content type (`semantic.file_vectorization`, `semantic.chat_vectorization`) so installing the extra doesn't force vectorization until you flip the flags. Per-row control is also available: each `files`, `chats`, and `messages` row carries a `vectorize` column (default on) that can exclude an individual item from embedding.

---

## How to enable each tier

| Tier | How |
|---|---|
| Tier 0 | Default. `pipx install footprinter-cli`, then `fp setup`, decline content snippets. |
| Tier 1 | In `fp setup` answer **yes** to "Enable file content snippets?". Or set `indexing.content_snippets: true` in `config.yaml` and re-run `fp ingest`. |
| Tier 2 | `pipx install "footprinter-cli[semantic]"`, `pipx install "footprinter-cli[parse]"`, or `pipx install "footprinter-cli[full]"`. Re-run `fp ingest --full` to populate. |

Tiers stack: Tier 1 includes Tier 0; Tier 2 includes Tier 1. You can disable Tier 1 again by flipping the config flag and re-running ingest, but existing previews persist until rows are re-processed or removed.

---

## See also

- `data-model.md` — schema columns (`content_preview`, `body_preview`, hash columns) and the local-only architecture.
- `mcp-access-control.md` — how visibility and read permissions gate stored content for AI assistants.
