"""Status service — visibility-aware system status aggregates."""

import sqlite3
from datetime import datetime
from typing import Optional

from footprinter.connectors import discover_connectors, is_installed, resolve_hook
from footprinter.db import status as db_status
from footprinter.db.status import get_access_resolution, get_entity_status_breakdown
from footprinter.paths import get_chroma_path, get_config_path
from footprinter.services.roles import Role


def get_status(conn: sqlite3.Connection, *, role: Role = Role.ADMIN) -> dict:
    """Return system status, filtered by role.

    VIEWER gets MCP-oriented counts with hidden-client data excluded.
    ADMIN gets the full system status including config presence checks.
    """
    if role == Role.VIEWER:
        return db_status.get_mcp_status(conn)
    return db_status.get_system_status(conn, get_config_path())


# ---------------------------------------------------------------------------
# CLI-oriented status aggregation (extracted from cli/status.py)
# ---------------------------------------------------------------------------


def get_data_counts(conn: sqlite3.Connection) -> dict:
    """Query database for all data counts. Each query wrapped in try/except."""
    counts: dict = {}
    cursor = conn.cursor()
    return _query_all_counts(cursor, counts)


def _query_all_counts(cursor: sqlite3.Cursor, counts: dict) -> dict:
    """Run all count queries. Separated for testability."""
    # Files by source
    try:
        cursor.execute(
            """
            SELECT source, COUNT(*) as count, SUM(size_bytes) as size
            FROM files WHERE status = 'listed'
            GROUP BY source
            """
        )
        counts["files"] = {
            row["source"]: {
                "count": row["count"],
                "size_mb": round((row["size"] or 0) / 1024 / 1024, 1),
            }
            for row in cursor.fetchall()
        }
    except sqlite3.OperationalError:
        counts["files"] = {}

    # Total files
    try:
        cursor.execute("SELECT COUNT(*) FROM files WHERE status = 'listed'")
        counts["files_total"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["files_total"] = 0

    # Folders by source
    try:
        cursor.execute(
            """
            SELECT source, COUNT(*) as count
            FROM folders WHERE status = 'listed'
            GROUP BY source
            """
        )
        counts["folders"] = {row["source"] or "local": row["count"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        counts["folders"] = {}

    # Browser visits
    try:
        cursor.execute("SELECT COUNT(*) FROM visits")
        counts["visits"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["visits"] = 0

    # Emails
    try:
        cursor.execute("SELECT COUNT(*) FROM emails")
        counts["emails"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["emails"] = 0

    # Chats by account
    try:
        cursor.execute("SELECT account, COUNT(*) as count FROM chats GROUP BY account")
        counts["chats"] = {row["account"]: row["count"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        counts["chats"] = {}

    # Chat messages
    try:
        cursor.execute("SELECT COUNT(*) FROM messages")
        counts["messages"] = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        counts["messages"] = 0

    # Top chats by message count
    try:
        cursor.execute(
            """
            SELECT title, message_count, created_at
            FROM chats
            ORDER BY message_count DESC
            LIMIT 5
            """
        )
        counts["top_chats"] = [
            {
                "title": row["title"],
                "message_count": row["message_count"],
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        counts["top_chats"] = []

    # Chat date range
    try:
        cursor.execute("SELECT MIN(created_at) as earliest, MAX(created_at) as latest FROM chats")
        row = cursor.fetchone()
        counts["chat_date_range"] = {
            "earliest": row["earliest"] if row else None,
            "latest": row["latest"] if row else None,
        }
    except sqlite3.OperationalError:
        counts["chat_date_range"] = {"earliest": None, "latest": None}

    # Remote source accounts (for display labels in print_status)
    try:
        cursor.execute("SELECT name, account FROM sources WHERE source_type = 'remote'")
        counts["remote_source_accounts"] = {row["name"]: row["account"] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        counts["remote_source_accounts"] = {}

    # Recently modified files
    try:
        cursor.execute(
            """
            SELECT name, source, modified_at
            FROM files WHERE status = 'listed'
            ORDER BY modified_at DESC
            LIMIT 10
            """
        )
        counts["recent_files"] = [
            {
                "name": row["name"],
                "source": row["source"],
                "modified_at": row["modified_at"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        counts["recent_files"] = []

    # Recent uploads
    try:
        cursor.execute(
            """
            SELECT filename, type, status, items_added, uploaded_at
            FROM uploads
            ORDER BY uploaded_at DESC
            LIMIT 5
            """
        )
        counts["recent_uploads"] = [
            {
                "filename": row["filename"],
                "type": row["type"],
                "status": row["status"],
                "items_added": row["items_added"],
                "uploaded_at": row["uploaded_at"],
            }
            for row in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        counts["recent_uploads"] = []

    # Last ingest run — prefer the aggregate (pipe='all') row written by
    # IngestService.run_pipes; fall back to the most recent per-pipe row for
    # databases ingested before the aggregate was introduced.
    try:
        cursor.execute(
            """
            SELECT pipe, started_at, completed_at, mode,
                   items_processed, errors, status, elapsed_seconds
            FROM ingests
            WHERE pipe = 'all' AND status != 'running' AND mode IS NOT NULL
            ORDER BY completed_at DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                """
                SELECT pipe, started_at, completed_at, mode,
                       items_processed, errors, status, elapsed_seconds
                FROM ingests
                WHERE status != 'running' AND mode IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
        if row:
            elapsed = row["elapsed_seconds"]
            if elapsed is None and row["started_at"] and row["completed_at"]:
                try:
                    start = datetime.fromisoformat(row["started_at"])
                    end = datetime.fromisoformat(row["completed_at"])
                    elapsed = round((end - start).total_seconds(), 1)
                except (ValueError, TypeError):
                    pass
            counts["last_run"] = {
                "mode": row["mode"] or "unknown",
                "pipe": row["pipe"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "items_processed": row["items_processed"] or 0,
                "errors": row["errors"] or 0,
                "status": row["status"],
                "elapsed_seconds": elapsed,
            }
        else:
            counts["last_run"] = None
    except sqlite3.OperationalError:
        counts["last_run"] = None

    # Per-entity status breakdown
    try:
        counts["entity_breakdown"] = get_entity_status_breakdown(cursor.connection)
    except sqlite3.OperationalError:
        counts["entity_breakdown"] = {}

    try:
        counts["access_resolution"] = get_access_resolution(cursor.connection)
    except sqlite3.OperationalError:
        counts["access_resolution"] = {}

    return counts


def get_source_health(config: Optional[dict]) -> dict:
    """Check source health via connector hooks and built-in checks."""
    health: dict = {}

    # Dynamic connector health via ConnectorSpec.health_check hooks
    connector_rows: list[dict] = []
    for name, spec in discover_connectors().items():
        if is_installed(spec) and spec.health_check:
            try:
                fn = resolve_hook(spec.health_check)
                if fn and config:
                    connector_rows.extend(fn(config))
            except Exception:
                pass
    health["connector_rows"] = connector_rows
    health["remote_enabled"] = len(connector_rows) > 0

    # Semantic search — config-aware health check
    config_enabled = config.get("semantic", {}).get("file_vectorization", False) if config else False
    try:
        from footprinter.semantic.vector_store import (
            VectorStore,
            _semantic_available,
        )

        installed = _semantic_available()
    except ImportError:
        installed = False
        VectorStore = None  # type: ignore[assignment]

    if not config_enabled:
        health["semantic"] = {"enabled": False, "installed": installed, "available": False}
    elif not installed:
        health["semantic"] = {"enabled": True, "installed": False, "available": False}
    elif not get_chroma_path().exists():
        health["semantic"] = {"enabled": True, "installed": True, "available": False}
    else:
        try:
            vs = VectorStore.get_instance()
            file_stats = vs.get_file_stats()
            conv_stats = vs.get_chat_stats()
            health["semantic"] = {
                "enabled": True,
                "installed": True,
                "available": True,
                "file_chunks": file_stats.get("total_chunks", 0),
                "chat_docs": conv_stats.get("total_documents", 0),
            }
        except Exception:
            health["semantic"] = {
                "enabled": True,
                "installed": True,
                "available": False,
            }

    return health


def visible_totals(counts: dict, health: dict) -> dict:
    """Compute file/folder totals from visible sources only.

    When no remote connector is enabled, remote sources are excluded so
    totals match the displayed breakdown.
    Returns ``{"files": int, "folders": int, "size_mb": float}``.
    """
    files = counts.get("files", {})
    folders = counts.get("folders", {})
    remote_accounts = counts.get("remote_source_accounts", {})
    remote_enabled = health.get("remote_enabled", False)

    if remote_enabled:
        vis_files = files
        vis_folders = folders
    else:
        vis_files = {k: v for k, v in files.items() if k not in remote_accounts}
        vis_folders = {k: v for k, v in folders.items() if k not in remote_accounts}

    return {
        "files": sum(info["count"] for info in vis_files.values()),
        "folders": sum(vis_folders.values()),
        "size_mb": sum(info["size_mb"] for info in vis_files.values()),
    }
