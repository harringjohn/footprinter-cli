# FPR as Infrastructure

How Footprinter serves as the data backbone for Claude Code across all projects. Covers what's built, what needs wiring, and the value proposition for launch.

**Status:** Draft — 2026-05-06  
**Cross-project:** FPR CLI (tools + MCP resources), ClaudeProdder (guidance + configuration), Launch Outreach (public articulation)

---

## What's Built

Footprinter provides three access modes to Claude Code, all functional today:

### MCP Tools (VIEWER role)

Seven tools served via the Footprinter MCP server. Available in every Claude Code session where the server is connected.

| Tool | What it does |
|------|-------------|
| `footprinter_search` | Keyword search across files, emails, chats, browser history |
| `footprinter_semantic` | Meaning-based search across chats and file content |
| `footprinter_folder` | Browse folder hierarchy and contents |
| `footprinter_read` | Read file content, email body, or chat conversation by ID |
| `footprinter_status` | Database composition — counts, sources, freshness |
| `footprinter_project` | Project metadata, file counts, folder structure |
| `footprinter_client` | Client overview with all projects and stats |

VIEWER role: filtered by status (listed only), visibility policies, and permission policies. The agent sees what the user has configured it to see.

### CLI Commands (ADMIN role)

Claude Code can run `fp` commands via Bash. Full catalog access, no filtering.

| Command area | Examples |
|---|---|
| Search | `fp search`, `fp project list`, `fp folder list` |
| Ingest | `fp ingest`, `fp ingest status`, `fp ingest --full` |
| Management | `fp upsert`, `fp assign`, `fp delete` |
| Access control | `fp mcp view set`, `fp mcp read set`, `fp mcp check` |
| Status | `fp status`, `fp doctor` |

ADMIN role: bypasses all access control. Used for data management, diagnostics, and deeper exploration when MCP tools are too restrictive.

### Indexed Data

The catalog covers the user's digital context across sources:

- **Files** — local filesystem + remote (Google Drive via connector)
- **Chats** — Claude and ChatGPT conversation exports
- **Emails** — Gmail via connector
- **Browser history** — Safari and Chrome
- **Projects** — detected code projects and manual groupings
- **Clients** — client/org groupings with project associations
- **Folders** — filesystem hierarchy with relationships

Organized by projects and clients, with full-text and semantic search.

---

## Two Modes of Use

### Working WITH the data

Using Footprinter's indexed context to make work in other projects better. This is the ambient value — Footprinter as infrastructure.

**Examples:**
- Working on a client project → search for related emails and past conversations about that client
- Debugging an auth issue → semantic search for "discussions about authentication" across past chats
- Writing a proposal → find related proposals and SOWs in the file index
- Investigating a dependency → check browser history for research visits
- Understanding a codebase → find related files across other projects

**Access mode:** MCP tools (VIEWER). Cross-project context without leaving the current working directory.

### Working ON the data

Managing the catalog itself — ingesting, organizing, configuring access.

**Examples:**
- Running `fp ingest` to refresh the index after adding new files
- Assigning projects to folders with `fp folder edit --project`
- Configuring what agents can see with `fp mcp view set`
- Checking ingest status with `fp ingest status`
- Investigating data quality with `fp status`

**Access mode:** CLI (ADMIN). Operational tasks that change the catalog state.

---

## What Needs Wiring

The tools are available but Claude Code doesn't know when to use them. Three levels of guidance are needed:

### 1. User-level guidance

Tells Claude Code that Footprinter exists and what it provides. Always active, every project.

**Must communicate:**
- Footprinter MCP tools provide access to the user's indexed files, chats, emails, and browser history
- When the user references something outside the current working directory, search for it
- When cross-project context would help, reach for it before asking the user to provide it
- MCP tools = VIEWER (filtered view), CLI = ADMIN (full access)
- Data is organized by projects and clients — use those as entry points

**Must NOT do:**
- Be a full tool reference (too verbose for ambient guidance)
- Encourage MCP usage for every query (most work is local)
- Replace project-specific guidance

### 2. Project-level guidance

Tells Claude Code how Footprinter specifically helps in this project. Per-project, in `.claude/CLAUDE.md`.

**fpr-cli example:**
- Use MCP tools to validate MCP behavior from the consumer side
- Code reading shows intent, MCP tools show reality — use both
- CLI commands available for ADMIN-level data exploration

**Client project example:**
- This project is indexed as "[project name]" — use `footprinter_search` with project filter
- Client correspondence searchable via email source
- Past conversations about this client findable via semantic search

**Generic project example:**
- Related files in other projects findable via `footprinter_search`
- Past discussions about similar problems findable via `footprinter_semantic`

### 3. MCP resources (future)

Dynamic, context-aware guidance served by the Footprinter MCP server itself. Not yet implemented — the server currently exposes zero resources.

**Potential:** Resources could provide real-time project context ("this project has 2,341 indexed files, last ingested 3 hours ago") that static CLAUDE.md can't. Could work hand-in-hand with static guidance — CLAUDE.md says "Footprinter is available," resources say "here's what it knows right now."

---

## Proof of Concept

The retrospective and sprint planning skills already implement this pattern:

- **`retrospective-mcp`** — gathers activity data across all sources (files, chats, emails, browser) via MCP tools, synthesizes into a narrative
- **`sprint-plan-mcp`** — uses Footprinter data + Linear data to plan sprints
- Both have a detailed `references/mcp-tools.md` that teaches Claude Code how to use each tool
- Hard rule: "ALL data gathering uses Footprinter MCP tools. Do NOT use sqlite3 or direct database access."

These skills prove the pattern works. They also show the limitation: the guidance only activates when the skill loads. The goal is to make this ambient — always available, not skill-dependent.

---

## Value Proposition (for launch)

### Core insight

The physical location of entities' content matters less than the ability to reliably find and reference them. A file on disk, an email in Gmail, a conversation in Claude, a page visited in Safari — these live in different systems, at different paths, behind different interfaces. Footprinter doesn't move them or copy them. It makes them all findable and referenceable from one place.

This is what "infrastructure" means here. Footprinter is not a file browser or a search engine. It's a reference layer — the thing that lets an AI agent (or a skill, or a workflow) say "find me everything related to X" and get answers across sources, projects, and time, regardless of where the content physically lives.

### The before/after

**Without Footprinter wired in:** Claude Code knows about the current working directory. To reference anything else, you have to tell it where to look, paste in context, or switch projects.

**With Footprinter wired in:** Claude Code can find past conversations about the problem you're debugging, look up emails from the client whose project you're working on, discover related files in other repositories, and understand how your work is organized — all without leaving the current session.

### The activation layer

The CLAUDE.md configuration is what turns passive tools into active infrastructure. The tools exist. The data is indexed. But without guidance telling Claude Code *when* to reach for cross-project context, the tools sit idle. The wiring — user-level rules, project-level guidance, and eventually MCP resources — is what activates the infrastructure.

---

## Issue Map

| Issue | Team | Project | Scope |
|-------|------|---------|-------|
| FPR-1681 | FootPrinter | FPR CLI (v1.0.2) | Project-level CLAUDE.md guidance for fpr-cli, MCP resources feature |
| CPR-51 | ClaudeProdder | CPR Formalization | User-level guidance design, project-level template convention |
| FPR-1682 | FootPrinter | FPR CLI (Launch Outreach) | Public-facing articulation of the infrastructure value proposition |
