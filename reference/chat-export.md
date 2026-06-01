# Chat export — Claude, ChatGPT, and Claude Code

Footprinter indexes chat history from three sources:

- **Claude Code sessions** are indexed **automatically** from `~/.claude/projects/` on every `fp ingest` — no export step (see [Claude Code sessions](#claude-code-sessions) below).
- **Claude.ai** and **ChatGPT** conversations are imported from a `.zip` you download from your account settings, as described below.

Both web providers ship the data as a `.zip` you download from your account settings.

## Claude.ai

1. Open Claude.ai and go to **Settings → Privacy → Export Data**.
2. Click **Export Data**. Anthropic emails you a download link when the archive is ready (usually within minutes; can take longer).
3. Download the `.zip` from the email.

The archive contains a `conversations.json` file at the root with every conversation and its messages.

## ChatGPT

1. Open ChatGPT and go to **Settings → Data Controls → Export data**.
2. Confirm the export. OpenAI emails you a download link when the archive is ready.
3. Download the `.zip` from the email.

The archive contains a `conversations.json` file at the root with every conversation and its messages.

## Importing into Footprinter

You have two options:

- **During `fp setup`** — when the wizard prompts for a chat-export path, paste the path to the downloaded `.zip` (or to a directory you extracted it into).
- **Anytime after setup**:

  ```bash
  fp add chats ~/Downloads/claude-export.zip
  fp add chats ~/Downloads/chatgpt-export.zip
  ```

`fp add chats` accepts either a `.zip` file or an extracted directory. Format (Claude vs. ChatGPT) is auto-detected from the archive contents. Re-importing the same archive is safe — duplicate conversations are skipped by UUID.

See [`reference/interfaces.md`](interfaces.md) for full `fp add` command details.

## Claude Code sessions

Claude Code writes a JSONL transcript for every session under `~/.claude/projects/`. Footprinter picks these up **automatically** — the `chat` stage of `fp ingest` scans that directory on each run and indexes any new sessions. There is no export or `.zip` step.

- No configuration required: if `~/.claude/projects/` exists, its sessions are indexed on the next `fp ingest`.
- Sessions are stored as chats with the account label `claude_code`, so you can filter them with `fp view chats` and the access-control scope `account:claude_code`.
- Indexing is incremental and deduplicated by session UUID: an unchanged session is skipped, and a session that grew since last run is re-indexed. `fp ingest --full` re-processes every session.
