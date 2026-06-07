"""Context resources: live status snapshot + static tool-selection guidance.

Resources are MCP's "ambient context" surface — clients can read them without
an explicit tool call. ``context_summary`` re-skins ``footprinter_status`` so
agents can orient themselves before reaching for a tool. ``context_guidance``
is a static cheat-sheet describing when to use which tool.

For parameterized resources (e.g. ``footprinter://context/project/{name}``),
add a sibling module — keep this one focused on the always-on surface.
"""

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.services import status_service
from footprinter.services.roles import Role

GUIDANCE = """\
# Footprinter MCP — tool-selection guide

Footprinter indexes local files, browser history, chats, and email and exposes
them through MCP. Use these tools to ground answers in the user's actual
context rather than guessing.

## When to reach for which tool

- `footprinter_status` — orient first. Shows what's indexed (files, emails,
  chats, messages, browser, projects, clients) with counts and last-sync
  timestamps. Cheap. Run it before any other tool when starting a task.
- `footprinter_search` — keyword search across all sources. Multi-word queries
  are AND-combined. Returns metadata + summaries, no full content. Use when
  you have specific terms to match.
- `footprinter_semantic` — embedding-based search across chats and files for
  conceptual / fuzzy queries where the user's words may not appear verbatim.
  Slower than keyword search; use when keyword search returns nothing useful.
- `footprinter_project`, `footprinter_client`, `footprinter_folder` —
  navigation. Resolve a name/path to its metadata and aggregate stats.
- `footprinter_read` — fetch full content for a file, email, or chat the user
  has granted access to. Use after search/navigation has identified the item.

## Cross-checking behavior

Footprinter's MCP tools run under a VIEWER role with hidden-client filtering.
The `fp` CLI runs under ADMIN and shows the unfiltered view. When debugging
discrepancies, compare both: code shows intent, MCP tools show what an agent
sees, the CLI shows ground truth.

## Resources

- `footprinter://context/summary` — live status snapshot (this is the same
  payload `footprinter_status` returns).
- `footprinter://context/guidance` — this document.
"""

SERVER_INSTRUCTIONS = """\
Footprinter indexes local files, emails, chats, and browser history.

Tools:
- footprinter_status — index counts and freshness; run first to orient.
- footprinter_search — keyword/metadata lookup. Matches name tokens, not path segments.
- footprinter_folder — inspect or resolve a path; list a folder's contents.
- footprinter_semantic — meaning-based content search (use when keywords miss).
- footprinter_read — fetch full content for a file, email, or chat by ID.

To find a file by path, use footprinter_folder, not footprinter_search.\
"""


@handle_db_errors
def context_summary() -> dict:
    """Live status snapshot: counts, last-sync timestamps, and breakdowns by source.

    Mirrors ``footprinter_status`` so agents can read the same data as ambient
    context without an explicit tool call.
    """
    with get_db() as conn:
        return status_service.get_status(conn, role=Role.VIEWER)


def context_guidance() -> str:
    """Static cheat-sheet describing when to use which Footprinter tool."""
    return GUIDANCE
