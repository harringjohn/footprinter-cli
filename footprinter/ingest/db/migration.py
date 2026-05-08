"""Database schema migration for pre-existing Footprinter databases.

Extracted for separation of concerns. Contains all
ALTER TABLE, RENAME, DROP, and data-migration logic needed to upgrade
databases created before the current DDL.

Only runs on databases that already have tables — fresh installs skip
this entirely (init_db handles everything via CREATE TABLE IF NOT EXISTS).
"""

import logging
import sqlite3

from footprinter.ingest.db.schema import _INGESTS_DDL, ACCESS_CONTROL_TABLES

logger = logging.getLogger(__name__)

_DISPLAY_NAME_BACKFILL = {
    "files": "name",
    "folders": "name",
    "visits": "title",
    "projects": "project_name",
    "chats": "title",
    "messages": "SUBSTR(content, 1, 100)",
    "emails": "subject",
    "clients": "name",
}


def migrate_schema(cursor: sqlite3.Cursor) -> None:
    """Upgrade a pre-existing database to the current schema.

    Adds missing columns, renames legacy columns, drops stale artefacts,
    and migrates data where needed.  Silently skips tables that don't
    exist yet and columns that already exist.

    Must run BEFORE ``PRAGMA foreign_keys=ON`` — the browser_visits →
    visits rename triggers SQLite's schema rewriter which recompiles FK
    references and fails on stale compiled references with FK enforcement.
    """

    # ── mcp_read / mcp_view on all entity tables ──
    for table in ACCESS_CONTROL_TABLES:
        for col, col_def in [
            ("mcp_read", "TEXT DEFAULT 'inherit'"),
            ("mcp_view", "TEXT DEFAULT 'inherit'"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # table doesn't exist yet or column already exists

    # Drop stale artefacts from chat_conversations → chats rename.
    for name in (
        "chat_conversations_ai",
        "chat_conversations_ad",
        "chat_conversations_au",
        "chats_ai",
        "chats_ad",
        "chats_au",
    ):
        cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
    cursor.execute("DROP TABLE IF EXISTS chat_conversations_fts")
    # If browser_visits still exists, the RENAME TO visits below will
    # trigger SQLite's schema rewriter.  With foreign_keys ON, the
    # rewriter recompiles FK references and hits the stale compiled
    # chat_conversations FK.  Dropping chats_fts BEFORE the rename
    # prevents corruption.  Skip on fresh/already-migrated DBs.
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_visits'")
    if cursor.fetchone() is not None:
        for name in ("chats_fts_ai", "chats_fts_ad", "chats_fts_au"):
            cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
        cursor.execute("DROP TABLE IF EXISTS chats_fts")

    # Rename indexed_drive_id → remote_file_id, indexed_drive_folder_id → remote_folder_id
    for old, new, table in [
        ("indexed_drive_id", "remote_file_id", "files"),
        ("indexed_drive_folder_id", "remote_folder_id", "folders"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet or column already renamed

    # Standardize column naming conventions
    for old, new, table in [
        ("last_scanned_at", "scanned_at", "folders"),
        ("info_vectorized_at", "metadata_vectorized_at", "chats"),
        ("direct_in_drive", "remote_file_count", "folders"),
        ("total_in_drive", "remote_file_count_recursive", "folders"),
        ("last_drive_check", "remote_checked_at", "folders"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet or column already renamed

    # Rename artifact_count → file_count (missed in artifacts → files rename)
    for old, new, table in [
        ("direct_artifact_count", "direct_file_count", "folders"),
        ("total_artifact_count", "total_file_count", "folders"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet or column already renamed

    # Rename files.content_hash → sha256_hash
    try:
        cursor.execute("ALTER TABLE files RENAME COLUMN content_hash TO sha256_hash")
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet or column already renamed

    # Data migration: Drive files stored MD5 in content_hash — move to md5_hash
    try:
        cursor.execute("""
            UPDATE files SET md5_hash = sha256_hash, sha256_hash = NULL
            WHERE source != 'local' AND sha256_hash IS NOT NULL AND md5_hash IS NULL
        """)
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet on fresh install

    # Drop duplicate total_size column (total_size_bytes is canonical)
    try:
        cursor.execute("ALTER TABLE folders DROP COLUMN total_size")
    except sqlite3.OperationalError:
        pass  # column doesn't exist or already dropped

    # Drop dead columns: written but never read
    # Include old name (counts_updated_at) for DBs that were never
    # migrated through the rename step.
    for col, table in [
        ("stats_updated_at", "folders"),
        ("counts_updated_at", "folders"),
        ("summarized_at", "emails"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # column doesn't exist or table missing

    # Drop orphan tables from old schema.
    for table in ("artifact_sync_state", "file_ai_analysis", "permission_defaults", "visibility_defaults"):
        cursor.execute(f"DROP TABLE IF EXISTS {table}")

    # Retire dead tracking tables.
    # Migrate the live watermark row before dropping the table.
    # Create ingests early if needed — the main DDL is idempotent.
    cursor.execute(_INGESTS_DDL)
    try:
        cursor.execute("SELECT stage, last_completed_at FROM pipeline_watermarks")
        for row in cursor.fetchall():
            stage = row[0] if isinstance(row, tuple) else row["stage"]
            ts = row[1] if isinstance(row, tuple) else row["last_completed_at"]
            if ts:
                cursor.execute(
                    "INSERT INTO ingests (pipe, started_at, completed_at, status) "
                    "SELECT ?, ?, ?, 'completed' "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM ingests WHERE pipe = ? AND completed_at = ?"
                    ")",
                    (stage, ts, ts, stage, ts),
                )
    except sqlite3.OperationalError:
        pass  # table doesn't exist on fresh install
    cursor.execute("DROP TABLE IF EXISTS pipeline_watermarks")
    cursor.execute("DROP TABLE IF EXISTS runs")

    # browser_visits columns added with status/client/project support
    for col, col_def in [
        ("status", "TEXT DEFAULT 'active'"),
        ("client_id", "INTEGER"),
        ("project_id", "INTEGER"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE browser_visits ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet or column already exists

    # emails: add status column
    try:
        cursor.execute("ALTER TABLE emails ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet or column already exists

    # files: add client_id
    try:
        cursor.execute("ALTER TABLE files ADD COLUMN client_id INTEGER")
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet or column already exists

    # Rename browser_visits → visits
    # Add mcp columns to old table first — the ACCESS_CONTROL_TABLES loop
    # above targets "visits" which doesn't exist yet on legacy DBs.
    for col, col_def in [
        ("mcp_read", "TEXT DEFAULT 'inherit'"),
        ("mcp_view", "TEXT DEFAULT 'inherit'"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE browser_visits ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # table gone, or column exists
    try:
        for idx in [
            "idx_browser_time",
            "idx_browser_browser",
            "idx_browser_visits_project",
            "idx_browser_unique",
            "idx_browser_visits_client",
            "idx_browser_visits_status",
            "idx_browser_visits_visibility",
        ]:
            cursor.execute(f"DROP INDEX IF EXISTS {idx}")
        cursor.execute("ALTER TABLE browser_visits RENAME TO visits")
    except sqlite3.OperationalError:
        pass  # already renamed or fresh install

    # If both tables now exist, the rename above failed silently because
    # `visits` was already created by an earlier partial init or by the
    # schema's CREATE TABLE IF NOT EXISTS.  Merge any legacy rows into
    # `visits` (INSERT OR IGNORE preserves existing visits on PRIMARY
    # KEY collision) and drop the legacy table so the `browser_visits`
    # guard above stops re-firing on every init (which, pre-fix,
    # dropped chats_fts each session).
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_visits'")
    legacy_exists = cursor.fetchone() is not None
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visits'")
    canonical_exists = cursor.fetchone() is not None
    if legacy_exists and canonical_exists:
        # Use the column intersection — schema versions vary across the
        # legacy and canonical tables, so we copy whatever overlaps.
        # Column names come from PRAGMA table_info, so the f-string
        # interpolation is safe (no user input).
        legacy_cols = {row[1] for row in cursor.execute("PRAGMA table_info(browser_visits)").fetchall()}
        canonical_cols = {row[1] for row in cursor.execute("PRAGMA table_info(visits)").fetchall()}
        shared_cols = sorted(legacy_cols & canonical_cols)
        if shared_cols:
            col_list = ", ".join(shared_cols)
            cursor.execute(f"INSERT OR IGNORE INTO visits ({col_list}) SELECT {col_list} FROM browser_visits")
        cursor.execute("DROP TABLE browser_visits")

    # clients/projects: add status_reason column
    for table in ("clients", "projects"):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN status_reason TEXT")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet or column already exists

    # ── standard entity column set ──

    # folders: add status, client_id, indexed_at
    for col, col_def in [
        ("status", "TEXT DEFAULT 'active'"),
        ("client_id", "INTEGER"),
        ("indexed_at", "DATETIME"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE folders ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet or column already exists

    # messages: add status
    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass

    # emails: add created_at (no DEFAULT — see visits comment above)
    try:
        cursor.execute("ALTER TABLE emails ADD COLUMN created_at DATETIME")
    except sqlite3.OperationalError:
        pass

    # visits / browser_visits: add created_at
    # Note: ALTER TABLE cannot use CURRENT_TIMESTAMP as default
    # (non-constant), so we add without default. The CREATE TABLE
    # DDL has the default for fresh DBs.
    for table in ("visits", "browser_visits"):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN created_at DATETIME")
        except sqlite3.OperationalError:
            pass

    # display_name on all 8 entity tables
    for table in ACCESS_CONTROL_TABLES:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN display_name TEXT")
        except sqlite3.OperationalError:
            pass

    # Backfill display_name from source columns for existing rows
    for table, source_col in _DISPLAY_NAME_BACKFILL.items():
        try:
            cursor.execute(f"UPDATE {table} SET display_name = {source_col} WHERE display_name IS NULL")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet

    # ── Timestamp column standardization ──

    # Rename chats.updated_at → modified_at (origin timestamp)
    try:
        cursor.execute("ALTER TABLE chats RENAME COLUMN updated_at TO modified_at")
    except sqlite3.OperationalError:
        pass  # already renamed or table doesn't exist

    # Add updated_at audit column to all 6 entity tables
    for table in ("files", "folders", "visits", "chats", "messages", "emails"):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME")
        except sqlite3.OperationalError:
            pass  # column already exists or table doesn't exist

    # Add indexed_at to messages (was missing)
    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN indexed_at DATETIME")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Add vectorized_chunks to messages (matches files pattern)
    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN vectorized_chunks INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Backfill new audit columns from existing data
    _timestamp_backfill = {
        "files": "UPDATE files SET updated_at = indexed_at WHERE updated_at IS NULL",
        "folders": "UPDATE folders SET updated_at = indexed_at WHERE updated_at IS NULL",
        "visits": "UPDATE visits SET updated_at = indexed_at WHERE updated_at IS NULL",
        "chats": "UPDATE chats SET updated_at = indexed_at WHERE updated_at IS NULL",
        "emails": "UPDATE emails SET updated_at = indexed_at WHERE updated_at IS NULL",
        "messages_indexed": "UPDATE messages SET indexed_at = created_at WHERE indexed_at IS NULL",
        "messages_updated": "UPDATE messages SET updated_at = created_at WHERE updated_at IS NULL",
    }
    for label, sql in _timestamp_backfill.items():
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet

    # Backfill NULL status on chats/messages from legacy schemas that lacked
    # a column DEFAULT. MCP filters checking `status != 'removed'` exclude
    # NULLs (NULL comparisons evaluate to NULL), so new rows silently
    # disappear from counts and search until backfilled.
    for sql in (
        "UPDATE chats SET status = 'active' WHERE status IS NULL",
        "UPDATE messages SET status = 'active' WHERE status IS NULL",
    ):
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet
