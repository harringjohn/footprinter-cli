# Chat export — Claude and ChatGPT

Footprinter can index your past Claude.ai and ChatGPT conversations. Both providers ship the data as a `.zip` you download from your account settings.

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
  fp ingest import ~/Downloads/claude-export.zip
  fp ingest import ~/Downloads/chatgpt-export.zip
  ```

`fp ingest import` accepts either a `.zip` file or an extracted directory. Format (Claude vs. ChatGPT) is auto-detected from the archive contents. Re-importing the same archive is safe — duplicate conversations are skipped by UUID.

See [`reference/cli-reference.md`](cli-reference.md) § `fp ingest import` for full command details.
