"""Search tool: query across data sources."""

from pathlib import Path
from typing import Optional

from footprinter.mcp.db import get_db, handle_db_errors
from footprinter.services import search_service
from footprinter.services.roles import Role

HOME = str(Path.home())

# Per-source result cap enforced at the MCP layer to stay under the
# 1MB tool-result protocol limit. The service layer remains uncapped
# so CLI/API callers can request larger result sets directly.
MCP_SEARCH_LIMIT_CAP = 200

# Display names for source keys in summary text
_SOURCE_LABELS = {
    "files": ("file", "files"),
    "emails": ("email", "emails"),
    "chats": ("chat", "chats"),
    "browser": ("browser result", "browser results"),
}


def _build_search_summary(
    results: dict, query: str, sources: list[str], was_capped: bool = False
) -> str:
    """Build a human-readable summary of search results."""
    found_parts = []
    empty_parts = []

    for source in sources:
        items = results.get(source, [])
        singular, plural = _SOURCE_LABELS.get(source, (source, source))
        count = len(items)
        if count > 0:
            label = singular if count == 1 else plural
            found_parts.append(f"{count} {label}")
        else:
            empty_parts.append(plural)

    total_suppressed = results.get("suppressed", 0)

    if found_parts:
        query_part = f" matching '{query}'" if query and query.strip() else ""
        summary = f"Found {', '.join(found_parts)}{query_part}."
        if empty_parts:
            summary += f" No {' or '.join(empty_parts)} matched."
    else:
        query_part = f" for '{query}'" if query and query.strip() else ""
        summary = (
            f"No results{query_part}. "
            f"Tips: try single keywords, use footprinter_semantic "
            f"for semantic matching, or browse recent items with date_from/date_to "
            f"and no query."
        )

    if total_suppressed > 0:
        item_word = "item" if total_suppressed == 1 else "items"
        summary += f" ({total_suppressed} {item_word} hidden by visibility policy)"

    if was_capped:
        summary += (
            f" Showing up to {MCP_SEARCH_LIMIT_CAP} results per source (limit capped). "
            "Narrow with folder, date_from, or query keywords."
        )

    return summary


def _shorten_path(path: str) -> str:
    if path and path.startswith(HOME):
        return "~" + path[len(HOME) :]
    return path or ""


@handle_db_errors
def footprinter_search(
    query: str = "",
    sources: Optional[list[str]] = None,
    project: Optional[str] = None,
    client: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    account: Optional[str] = None,
    sender: Optional[str] = None,
    days_back: Optional[int] = None,
    folder: Optional[str] = None,
    mime_type: Optional[str] = None,
    include_unlisted: bool = False,
    include_removed: bool = False,
) -> dict:
    """Search across indexed sources by keyword. Returns metadata only, no file content.

    SEARCH BEHAVIOR:
    - Matches against file names, email subjects/senders, chat titles, and
      browser page titles/URLs depending on which sources are included.
    - Multi-word queries use AND logic: every term must appear. "project report" only returns
      items containing both "project" AND "report".
    - Terms shorter than 2 characters are ignored.
    - When query is empty, returns the most recent items (sorted by date descending).
      Combine with date_from/date_to to browse a specific time range.

    QUERY TIPS:
    - Use 1-3 short, specific keywords. Each additional word narrows results further.
    - Avoid long natural-language phrases — they produce too many AND terms and return nothing.
    - Good: "salesforce proposal" — Bad: "nonprofit technology consulting Salesforce partner"
    - To search by time period, leave query empty and use date_from/date_to.

    WHEN TO USE THIS vs footprinter_semantic:
    - Use THIS tool for keyword/metadata lookups: finding files by name, emails by subject,
      chats by title, or browsing recent activity across all sources.
    - Use footprinter_semantic when you want meaning-based search across
      chat or file content (e.g., "discussions about authentication architecture").

    SOURCE-SPECIFIC FILTERS:
    - account: Filter by account name. Applies to emails and files.
    - sender: Partial match on email sender name or address. Applies to emails only.
    - days_back: Only include emails from the last N days. Applies to emails only.
    - folder: Filter files by path prefix (e.g. "~/Work/projects"). Applies to files only.
    - mime_type: Exact MIME type match (e.g. "application/pdf"). Applies to files only.
    Source-specific filters are silently ignored when the relevant source is not being searched.

    Args:
        query: Keyword(s) matched against names, titles, subjects. Empty = list recent.
        sources: Which sources to search. Default: all.
            Options: "files", "emails", "chats", "browser".
        project: Filter to a project name (exact match, applies to files, emails, chats).
        client: Filter to a client name (exact match, applies to files, emails, chats).
        date_from: ISO date string lower bound (e.g. "2026-02-01").
        date_to: ISO date string upper bound (e.g. "2026-02-14").
        limit: Max results per source (default 50). MCP callers are capped
            at 200 per source to stay within the 1MB tool-result protocol
            limit; the response summary notes when this cap has been applied.
        account: Filter by account (e.g. "personal", "work"). Applies to emails and files.
        sender: Partial match on sender name or address (e.g. "alice"). Emails only.
        days_back: Only include emails from the last N days. Emails only.
        folder: Filter files under this path prefix (e.g. "~/Work/projects"). Files only.
        mime_type: Exact MIME type filter (e.g. "application/pdf"). Files only.
        include_unlisted: ADMIN-only. Include items with status='unlisted'. VIEWER
            (the default for MCP) accepts this but always sees listed-only.
        include_removed: ADMIN-only. Include items with status='removed'. VIEWER
            (the default for MCP) accepts this but always sees listed-only.

    Returns:
        Dict with keys per source (e.g. "files", "emails", "chats", "browser"),
        each containing a list of result dicts. Includes a "summary" key with
        a human-readable overview of what was found.
    """
    if not sources:
        sources = ["files", "emails", "chats", "browser"]

    effective_limit = min(limit, MCP_SEARCH_LIMIT_CAP)
    was_capped = limit > MCP_SEARCH_LIMIT_CAP

    with get_db() as conn:
        results = search_service.search(
            conn,
            role=Role.VIEWER,
            query=query,
            sources=sources,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            limit=effective_limit,
            account=account,
            sender=sender,
            days_back=days_back,
            folder=folder,
            mime_type=mime_type,
            include_unlisted=include_unlisted,
            include_removed=include_removed,
        )

    # Shorten file paths for MCP display
    for f in results.get("files", []):
        if "path" in f:
            f["path"] = _shorten_path(f["path"])

    results["summary"] = _build_search_summary(results, query, sources, was_capped=was_capped)
    return results
