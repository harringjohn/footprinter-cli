"""search_service — multi-source keyword search with visibility filtering.

Orchestrates per-source searches (files, emails, chats, browser) and applies
role-based visibility filtering + content stripping.
"""

import sqlite3
from typing import Optional

from footprinter.db.search import (
    search_browser_keyword,
    search_chats_keyword,
    search_emails_keyword,
    search_files_keyword,
)
from footprinter.db.sql_utils import split_query_terms
from footprinter.services.access_service import (
    filter_results_list,
    strip_content_for_denied,
)
from footprinter.services.roles import Role
from footprinter.visibility import get_source_visibility

DEFAULT_SOURCES = ["files", "emails", "chats", "browser"]


def search(
    conn: sqlite3.Connection,
    *,
    role: Role = Role.ADMIN,
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
) -> dict:
    """Search across indexed sources by keyword.

    Returns dict with per-source result lists and a ``suppressed`` count.
    VIEWER role: hidden items excluded, opaque items minimized, content
    stripped for permission-denied items.
    """
    if not sources:
        sources = list(DEFAULT_SOURCES)

    results: dict = {}
    total_suppressed = 0
    has_query = bool(query and query.strip())
    terms = split_query_terms(query) if has_query else []
    has_query = has_query and bool(terms)

    if "files" in sources:
        file_results = search_files_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            account=account,
            folder=folder,
            mime_type=mime_type,
            limit=limit,
            exclude_hidden=not role.sees_all,
        )
        if role.sees_all:
            results["files"] = file_results
        else:
            filtered, suppressed = filter_results_list("file", file_results)
            results["files"] = filtered
            total_suppressed += suppressed

    if "emails" in sources:
        email_results = search_emails_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            account=account,
            sender=sender,
            days_back=days_back,
            limit=limit,
            exclude_hidden=not role.sees_all,
        )
        if role.sees_all:
            results["emails"] = email_results
        else:
            filtered, suppressed = filter_results_list("email", email_results)
            strip_content_for_denied("email", filtered)
            results["emails"] = filtered
            total_suppressed += suppressed

    if "chats" in sources:
        chat_results = search_chats_keyword(
            conn,
            terms=terms,
            has_query=has_query,
            project=project,
            client=client,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            exclude_hidden=not role.sees_all,
        )
        if role.sees_all:
            results["chats"] = chat_results
        else:
            filtered, suppressed = filter_results_list("chat", chat_results)
            strip_content_for_denied("chat", filtered)
            results["chats"] = filtered
            total_suppressed += suppressed

    if "browser" in sources:
        browser_results = _search_browser_with_visibility(
            conn,
            terms=terms,
            has_query=has_query,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            role=role,
        )
        if browser_results is not None:
            results["browser"] = browser_results

    if total_suppressed > 0:
        results["suppressed"] = total_suppressed

    return results


def _search_browser_with_visibility(
    conn: sqlite3.Connection,
    *,
    terms: list[str],
    has_query: bool,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    role: Role = Role.ADMIN,
) -> Optional[list[dict]]:
    """Search browser visits with source-level visibility gating.

    Returns None if source is hidden. The visibility check is business logic
    that stays in the service; the SQL query is delegated to db.search.
    """
    browser_visibility = None
    if not role.sees_all:
        browser_visibility = get_source_visibility(conn, "source:browser")
        if browser_visibility == "hidden":
            return None

    raw_results = search_browser_keyword(
        conn,
        terms=terms,
        has_query=has_query,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )

    # Source-level opaque gating
    if browser_visibility == "opaque":
        return [{"id": r["id"], "browser": r["browser"]} for r in raw_results]

    return raw_results
