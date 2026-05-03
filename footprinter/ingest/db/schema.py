"""Database schema initialization."""

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


# Standard Entity Column Set
# ─────────────────────────
# All 8 entity tables (files, folders, visits, projects, chats,
# messages, emails, clients) share these baseline columns:
#
#   id            INTEGER PRIMARY KEY AUTOINCREMENT
#   status        TEXT DEFAULT 'listed'   CHECK (listed|unlisted|removed)
#   created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
#   display_name  TEXT                    (auto-populated via trigger)
#   mcp_read      TEXT DEFAULT 'inherit'  CHECK (allow|deny|inherit)
#   mcp_view      TEXT DEFAULT 'inherit'  CHECK (hidden|opaque|visible|inherit)
#
# Data-source entities (files, folders, emails, chats, visits, messages)
# also have audit timestamp columns:
#   indexed_at    DATETIME DEFAULT CURRENT_TIMESTAMP  (immutable first-seen)
#   updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP  (refreshed on re-process)
#   project_id    INTEGER REFERENCES projects(id)
#   client_id     INTEGER REFERENCES clients(id)
#
# Timestamp format: YYYY-MM-DD HH:MM:SS (UTC, matches SQLite CURRENT_TIMESTAMP).
# Python code uses utils.time.UTC_FMT / utc_now_iso() for the same format.
#
# Source-specific metadata is stored in the `metadata` TEXT column
# (JSON) on tables that need it: files, projects, chats, messages,
# emails, clients.
#
# Columns populated by app or future scope
# ─────────────────────────────────────────
#   summary       TEXT     — AI-generated summary (files, emails, chats)
#   summarized_at DATETIME — when summary was generated (files only)
#
# files_fts and chats_fts reference the summary column via FTS5
# triggers, so summary stays in the standard schema.  emails also
# has summary for consistency.  Tool-only installs leave them NULL.


# Single source of truth for FTS5 virtual table definitions.
# All CREATE TABLE, backfill, and trigger SQL is derived from this.
_FTS_DEFINITIONS: dict[str, dict[str, Any]] = {
    "files_fts": {
        "base_table": "files",
        "columns": ["name", "content_preview", "summary"],
        "content_columns": ["content_preview", "summary"],
    },
    "emails_fts": {
        "base_table": "emails",
        "columns": ["subject", "from_name", "from_address", "body_preview"],
        "content_columns": ["body_preview"],
    },
    "chats_fts": {
        "base_table": "chats",
        "columns": ["title", "summary"],
        "content_columns": ["summary"],
    },
}

# Single source of truth for the ingests table DDL.
# Referenced by both migration.py (early creation for last-run migration)
# and init_db() (canonical DDL).
_INGESTS_DDL = (
    "CREATE TABLE IF NOT EXISTS ingests ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "pipe TEXT NOT NULL, "
    "started_at DATETIME NOT NULL, "
    "completed_at DATETIME, "
    "status TEXT NOT NULL DEFAULT 'running' "
    "  CHECK (status IN ('running', 'completed', 'failed', 'interrupted')), "
    "mode TEXT, "
    "trigger TEXT, "
    "items_processed INTEGER DEFAULT 0, "
    "items_new INTEGER DEFAULT 0, "
    "items_updated INTEGER DEFAULT 0, "
    "items_skipped INTEGER DEFAULT 0, "
    "errors INTEGER DEFAULT 0, "
    "elapsed_seconds REAL, "
    "metadata TEXT)"
)


# All 8 entity tables that carry mcp_read / mcp_view columns.
# Shared by init_db() (display_name triggers) and migration.py.
ACCESS_CONTROL_TABLES = (
    "files",
    "folders",
    "visits",
    "projects",
    "chats",
    "messages",
    "emails",
    "clients",
)


class SchemaMixin:
    """Mixin providing database schema initialization."""

    def init_db(self):
        """Initialize database with schema."""
        self.conn = sqlite3.connect(self.db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")

        cursor = self.conn.cursor()

        # Only run migration on existing databases (not fresh installs).
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        if cursor.fetchone() is not None:
            from footprinter.ingest.db.migration import migrate_schema

            migrate_schema(cursor)

        # Enable FK enforcement AFTER migrations.  The browser_visits →
        # visits rename triggers SQLite's schema rewriter which recompiles
        # FK references.  The messages table's FK was originally REFERENCES
        # chat_conversations(id); with foreign_keys ON the rewriter
        # validates the stale compiled reference and fails.
        self.conn.execute("PRAGMA foreign_keys=ON")

        # ========================================
        # Files Table (unified content metadata)
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Source identification
                source TEXT NOT NULL,
                external_id TEXT,
                account TEXT,

                -- Core file info
                name TEXT NOT NULL,
                path TEXT,
                content_type TEXT,
                mime_type TEXT,
                size_bytes INTEGER,

                -- Origin timestamps
                created_at DATETIME,
                modified_at DATETIME,
                accessed_at DATETIME,

                -- Audit timestamps
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Content
                content_preview TEXT,
                sha256_hash TEXT,

                -- Vectorization status
                vectorized_at DATETIME,
                vectorized_chunks INTEGER DEFAULT 0,

                -- Project/client association
                project_id INTEGER REFERENCES projects(id),
                client_id INTEGER REFERENCES clients(id),

                -- Flexible metadata (source-specific fields as JSON)
                metadata TEXT,

                -- Folder linkage
                folder_id INTEGER REFERENCES folders(id),

                -- Hash for Drive linking
                md5_hash TEXT,

                -- Status tracking
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),
                status_reason TEXT,
                status_changed_at DATETIME,

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- AI-generated summaries
                summary TEXT,
                summarized_at DATETIME,

                -- Display
                display_name TEXT
            )
        """
        )

        # Files indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_source ON files(source)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_type ON files(content_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(sha256_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_account ON files(account)")

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_files_local_unique
            ON files(source, path)
            WHERE source = 'local' AND path IS NOT NULL
        """
        )

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_files_drive_unique
            ON files(source, external_id, account)
            WHERE source != 'local' AND external_id IS NOT NULL
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_md5 ON files(md5_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_visibility ON files(mcp_view)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_client ON files(client_id)")

        # ========================================
        # Folders Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Core folder info
                path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_path TEXT,

                -- Stats
                file_count INTEGER DEFAULT 0,

                -- Timestamps
                scanned_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Project association
                project_id INTEGER REFERENCES projects(id),

                -- Source identification (for remote folders)
                source TEXT DEFAULT 'local',
                external_id TEXT,
                account TEXT,

                -- Hierarchy
                parent_folder_id INTEGER REFERENCES folders(id),

                -- Pre-computed counts
                direct_file_count INTEGER DEFAULT 0,
                total_file_count INTEGER DEFAULT 0,
                total_size_bytes INTEGER DEFAULT 0,

                -- Status tracking
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),
                status_reason TEXT,
                status_changed_at DATETIME,

                -- Audit timestamps
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Client association
                client_id INTEGER REFERENCES clients(id),

                -- MCP access control
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_path ON folders(path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_project ON folders(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_source ON folders(source)")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_unique_path ON folders(path) WHERE source = 'local'"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_visibility ON folders(mcp_view)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_status ON folders(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_client ON folders(client_id)")

        # ========================================
        # Visits Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                visit_time DATETIME NOT NULL,
                browser TEXT NOT NULL,
                visit_count INTEGER DEFAULT 1,

                -- Audit timestamps
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Status tracking
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- Origin timestamps
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Client/project association
                client_id INTEGER REFERENCES clients(id),
                project_id INTEGER REFERENCES projects(id),

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_time ON visits(visit_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_browser ON visits(browser)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_project ON visits(project_id)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_visits_unique ON visits(url, visit_time, browser)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_client ON visits(client_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_status ON visits(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_visibility ON visits(mcp_view)")

        # ========================================
        # Projects Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),
                status_reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,

                -- Code project info (app-scope adds more columns)
                root_path TEXT,
                project_type TEXT,

                -- Client association
                client_id INTEGER REFERENCES clients(id),
                client TEXT,
                github_url TEXT,
                root_folder_id INTEGER REFERENCES folders(id),

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_root ON projects(root_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_visibility ON projects(mcp_view)")

        # ========================================
        # Chats Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT UNIQUE NOT NULL,
                account TEXT NOT NULL,
                title TEXT,
                summary TEXT,

                -- Origin timestamps
                created_at DATETIME,
                modified_at DATETIME,

                message_count INTEGER DEFAULT 0,

                -- Audit timestamps
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                metadata TEXT,

                -- Vectorization
                metadata_vectorized_at DATETIME,

                -- Status tracking
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- Client/project association
                client_id INTEGER REFERENCES clients(id),
                project_id INTEGER REFERENCES projects(id),

                -- Merge tracking
                merged_into_id INTEGER REFERENCES chats(id),

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_created ON chats(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_account ON chats(account)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_status ON chats(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_client ON chats(client_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_visibility ON chats(mcp_view)")

        # ========================================
        # Messages Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id TEXT,
                role TEXT NOT NULL,
                content TEXT,
                created_at DATETIME,
                metadata TEXT,
                vectorized_at DATETIME,
                vectorized_chunks INTEGER DEFAULT 0,

                -- Audit timestamps
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Status tracking
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- Display
                display_name TEXT,

                FOREIGN KEY (chat_id) REFERENCES chats(id)
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON messages(chat_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_created ON messages(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_visibility ON messages(mcp_view)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status)")

        # ========================================
        # Emails Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                account TEXT NOT NULL,
                from_address TEXT,
                from_name TEXT,
                to_addresses TEXT,
                cc_addresses TEXT,
                subject TEXT,
                body_preview TEXT,
                received_at DATETIME NOT NULL,
                labels TEXT,
                has_attachments BOOLEAN DEFAULT 0,
                is_read BOOLEAN DEFAULT 1,

                -- Audit timestamps
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                metadata TEXT,

                -- Status tracking
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- AI-generated summaries
                summary TEXT,

                -- Timestamps
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Client/project association
                client_id INTEGER REFERENCES clients(id),
                project_id INTEGER REFERENCES projects(id),

                -- Display
                display_name TEXT,

                UNIQUE(message_id, account)
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_account ON emails(account)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_received ON emails(received_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_from ON emails(from_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_thread ON emails(thread_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_client ON emails(client_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_project ON emails(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_visibility ON emails(mcp_view)")

        # ========================================
        # Clients Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE,
                client_type TEXT NOT NULL,
                path_pattern TEXT,
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),
                status_reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,

                -- MCP access control
                mcp_read TEXT DEFAULT 'inherit'
                    CHECK (mcp_read IN ('allow', 'deny', 'inherit')),
                mcp_view TEXT DEFAULT 'inherit'
                    CHECK (mcp_view IN ('hidden', 'opaque', 'visible', 'inherit')),

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_slug ON clients(slug)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_type ON clients(client_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_visibility ON clients(mcp_view)")

        # ========================================
        # Sources Table (runtime registry)
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                name TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                adapter TEXT,
                account TEXT,
                label TEXT,
                icon TEXT,
                enabled INTEGER DEFAULT 1,
                config TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_type ON sources(source_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled)")

        # ========================================
        # Uploads Table (generic upload log)
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_hash TEXT NOT NULL UNIQUE,
                file_size INTEGER,
                type TEXT NOT NULL,
                source TEXT,
                items_added INTEGER DEFAULT 0,
                items_updated INTEGER DEFAULT 0,
                items_total INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                metadata TEXT
            )
        """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_uploads_type ON uploads(type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_uploads_hash ON uploads(file_hash)")

        # ========================================
        # Permission Policies Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_policies (
                scope TEXT PRIMARY KEY,
                setting TEXT NOT NULL CHECK (setting IN ('allow', 'deny')),
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # ========================================
        # Visibility Policies Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS visibility_policies (
                scope TEXT PRIMARY KEY,
                setting TEXT NOT NULL CHECK (setting IN ('hidden', 'opaque', 'visible')),
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # ========================================
        # Ingests Table (per-pipe run history)
        # ========================================
        cursor.execute(_INGESTS_DDL)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ingests_pipe_status ON ingests(pipe, status)")

        # ========================================
        # FTS5 Full-Text Search Indexes
        # ========================================
        # Capture which FTS tables existed BEFORE the CREATE IF NOT EXISTS
        # below.  COUNT(*) on an external-content FTS5 table is delegated
        # to the content table and therefore unreliable as an emptiness
        # check (FPR-1638) — the prior gate `if COUNT(*) == 0` always
        # short-circuited because the count came from the base table.
        fts_placeholders = ", ".join("?" for _ in _FTS_DEFINITIONS)
        existing_fts_tables = {
            row[0]
            for row in cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({fts_placeholders})",
                list(_FTS_DEFINITIONS.keys()),
            ).fetchall()
        }
        for fts_table in _FTS_DEFINITIONS:
            try:
                cursor.execute(self._fts_create_sql(fts_table, if_not_exists=True))
            except sqlite3.OperationalError as e:
                if "no such module: fts5" in str(e):
                    logger.warning(
                        "FTS5 not available — %s keyword search will use LIKE fallback",
                        _FTS_DEFINITIONS[fts_table]["base_table"],
                    )
                else:
                    raise

        # Drop old FTS _au triggers so they can be recreated with WHEN
        # clauses (prevents spurious re-indexing on non-FTS column updates).
        for fts_table in _FTS_DEFINITIONS:
            cursor.execute(f"DROP TRIGGER IF EXISTS {fts_table}_au")

        # Create all FTS triggers (shared with rebuild_fts_indexes)
        self.create_fts_triggers()

        # ========================================
        # FTS5 Backfill (idempotent)
        # ========================================
        # Backfill if EITHER the table was just created OR its inverted
        # index is empty.  The second condition uses the FTS5 `_docsize`
        # shadow table, which holds one row per indexed document and is
        # NOT delegated to the content table — making it a reliable
        # honest emptiness probe for external-content tables (unlike
        # `SELECT COUNT(*) FROM <fts>`).  Together this preserves
        # _fts_backfill_sql's mcp_view filtering and also self-heals
        # any FTS table that exists but has an empty index (e.g. after
        # a future migration drops it, or a manual SQL repair).
        # Each iteration has its own try/except so a single failure
        # doesn't silently abort the rest, and surprising errors (e.g.
        # FTS5 internals changing in a future SQLite version) surface
        # at WARNING rather than vanishing into a DEBUG log.
        for fts_table in _FTS_DEFINITIONS:
            try:
                if fts_table not in existing_fts_tables:
                    cursor.execute(self._fts_backfill_sql(fts_table))
                    continue
                cursor.execute(f"SELECT COUNT(*) FROM {fts_table}_docsize")
                if cursor.fetchone()[0] == 0:
                    cursor.execute(self._fts_backfill_sql(fts_table))
            except sqlite3.OperationalError as e:
                msg = str(e)
                if "no such table" in msg or "no such module: fts5" in msg:
                    logger.debug("FTS5 backfill skipped for %s: %s", fts_table, msg)
                else:
                    logger.warning(
                        "FTS5 backfill failed for %s: %s — search index may be incomplete",
                        fts_table,
                        msg,
                    )

        # ========================================
        # display_name AFTER INSERT triggers
        # ========================================
        _DISPLAY_NAME_SOURCES = {
            "files": "NEW.name",
            "folders": "NEW.name",
            "visits": "NEW.title",
            "projects": "NEW.project_name",
            "chats": "NEW.title",
            "messages": "SUBSTR(NEW.content, 1, 100)",
            "emails": "NEW.subject",
            "clients": "NEW.name",
        }
        for table, source_expr in _DISPLAY_NAME_SOURCES.items():
            cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS set_display_name_{table}
                AFTER INSERT ON {table}
                FOR EACH ROW
                WHEN NEW.display_name IS NULL
                BEGIN
                    UPDATE {table} SET display_name = {source_expr}
                    WHERE id = NEW.id;
                END
            """)

        self.conn.commit()

        # Seed the sources registry from config
        try:
            from footprinter.source_registry import SourceRegistry

            registry = SourceRegistry(self.conn)
            registry.seed_from_config()
        except Exception as e:
            logger.warning(f"Could not seed sources from config: {e}")

    # ========================================
    # FTS Trigger Management
    # ========================================

    _FTS_TRIGGER_NAMES = [f"{fts_table}_{suffix}" for fts_table in _FTS_DEFINITIONS for suffix in ("ai", "ad", "au")]

    @staticmethod
    def _fts_create_sql(fts_table: str, *, if_not_exists: bool = False) -> str:
        """Return CREATE VIRTUAL TABLE SQL for an FTS5 table."""
        defn = _FTS_DEFINITIONS[fts_table]
        cols = ", ".join(defn["columns"])
        exists = "IF NOT EXISTS " if if_not_exists else ""
        return (
            f"CREATE VIRTUAL TABLE {exists}{fts_table} USING fts5("
            f"{cols}, content='{defn['base_table']}', content_rowid='id')"
        )

    @staticmethod
    def _fts_backfill_sql(fts_table: str) -> str:
        """Return INSERT...SELECT SQL to backfill an FTS table from its base table."""
        defn = _FTS_DEFINITIONS[fts_table]
        content_cols = set(defn.get("content_columns", []))
        cols_str = ", ".join(defn["columns"])
        select_exprs = []
        for col in defn["columns"]:
            if col in content_cols:
                select_exprs.append(
                    f"CASE WHEN COALESCE(mcp_view, 'inherit') IN ('opaque', 'hidden') THEN NULL ELSE {col} END"
                )
            else:
                select_exprs.append(col)
        select_str = ", ".join(select_exprs)
        return f"INSERT INTO {fts_table}(rowid, {cols_str}) SELECT id, {select_str} FROM {defn['base_table']}"

    @staticmethod
    def _fts_col_expr(col: str, prefix: str, content_columns: set[str]) -> str:
        """Return a SQL expression for a column value in FTS triggers.

        Content columns are NULLed when mcp_view is opaque or hidden,
        preventing sensitive content from entering the FTS index.
        Metadata columns (name, subject, title, etc.) pass through unchanged.
        """
        if col in content_columns:
            return (
                f"CASE WHEN COALESCE({prefix}.mcp_view, 'inherit') "
                f"IN ('opaque', 'hidden') THEN NULL ELSE {prefix}.{col} END"
            )
        return f"{prefix}.{col}"

    @staticmethod
    def _fts_trigger_sql(fts_table: str) -> list[str]:
        """Return the 3 trigger CREATE statements (ai, ad, au) for an FTS table."""
        defn = _FTS_DEFINITIONS[fts_table]
        base = defn["base_table"]
        cols = defn["columns"]
        content_cols = set(defn.get("content_columns", []))
        cols_str = ", ".join(cols)

        new_vals = ", ".join(SchemaMixin._fts_col_expr(c, "new", content_cols) for c in cols)
        old_vals = ", ".join(SchemaMixin._fts_col_expr(c, "old", content_cols) for c in cols)

        # WHEN clause for _au: only re-index when FTS-tracked columns or
        # mcp_view change.  mcp_view affects what's stored in FTS for content
        # columns (opaque/hidden → NULL).  Prevents spurious re-indexing from
        # non-FTS updates (e.g. display_name) and avoids corruption when
        # AFTER INSERT triggers do UPDATE on the same row.
        when_cols = list(cols) + ["mcp_view"]
        when_parts = " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in when_cols)

        return [
            # AFTER INSERT
            f"CREATE TRIGGER IF NOT EXISTS {fts_table}_ai AFTER INSERT ON {base} BEGIN "
            f"INSERT INTO {fts_table}(rowid, {cols_str}) "
            f"VALUES (new.id, {new_vals}); END",
            # AFTER DELETE
            f"CREATE TRIGGER IF NOT EXISTS {fts_table}_ad AFTER DELETE ON {base} BEGIN "
            f"INSERT INTO {fts_table}({fts_table}, rowid, {cols_str}) "
            f"VALUES ('delete', old.id, {old_vals}); END",
            # AFTER UPDATE (only when FTS-tracked columns change)
            f"CREATE TRIGGER IF NOT EXISTS {fts_table}_au AFTER UPDATE ON {base} "
            f"WHEN {when_parts} BEGIN "
            f"INSERT INTO {fts_table}({fts_table}, rowid, {cols_str}) "
            f"VALUES ('delete', old.id, {old_vals}); "
            f"INSERT INTO {fts_table}(rowid, {cols_str}) "
            f"VALUES (new.id, {new_vals}); END",
        ]

    def check_fts_triggers(self) -> list[str]:
        """Return names of expected FTS triggers that are missing from the database.

        Returns an empty list when all triggers are present.
        """
        cursor = self.conn.cursor()
        placeholders = ", ".join("?" for _ in self._FTS_TRIGGER_NAMES)
        present = {
            row[0]
            for row in cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='trigger' AND name IN ({placeholders})",
                self._FTS_TRIGGER_NAMES,
            ).fetchall()
        }
        return [name for name in self._FTS_TRIGGER_NAMES if name not in present]

    def drop_fts_triggers(self) -> None:
        """Drop all FTS sync triggers. Safe to call when FTS5 is unavailable."""
        try:
            cursor = self.conn.cursor()
            for name in self._FTS_TRIGGER_NAMES:
                cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
            self.conn.commit()
            logger.info("Dropped FTS triggers for bulk ingest")
        except sqlite3.OperationalError as e:
            if "no such module: fts5" in str(e):
                logger.debug("drop_fts_triggers skipped — FTS5 not available")
            else:
                raise

    def create_fts_triggers(self) -> None:
        """Create all FTS sync triggers. Safe to call when FTS5 is unavailable."""
        try:
            cursor = self.conn.cursor()

            # Only create triggers if FTS tables exist
            placeholders = ", ".join("?" for _ in _FTS_DEFINITIONS)
            cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
                list(_FTS_DEFINITIONS.keys()),
            )
            fts_tables = {row[0] for row in cursor.fetchall()}
            if not fts_tables:
                logger.debug("create_fts_triggers skipped — no FTS tables exist")
                return

            for fts_table in _FTS_DEFINITIONS:
                if fts_table in fts_tables:
                    for sql in self._fts_trigger_sql(fts_table):
                        cursor.execute(sql)

            self.conn.commit()
        except sqlite3.OperationalError as e:
            if "no such module: fts5" in str(e):
                logger.debug("create_fts_triggers skipped — FTS5 not available")
            else:
                raise

    def rebuild_fts_indexes(self) -> None:
        """Rebuild all FTS indexes from base tables and restore triggers.

        Uses drop+create+backfill (not FTS5 ``rebuild``) so that content
        columns are NULLed for opaque/hidden records via ``_fts_backfill_sql``.
        Safe to call when FTS5 is unavailable.
        """
        try:
            cursor = self.conn.cursor()

            # Drop triggers first (they reference FTS tables)
            for name in self._FTS_TRIGGER_NAMES:
                cursor.execute(f"DROP TRIGGER IF EXISTS {name}")

            # Drop and recreate with filtered backfill
            for fts_table in _FTS_DEFINITIONS:
                cursor.execute(f"DROP TABLE IF EXISTS {fts_table}")
                cursor.execute(self._fts_create_sql(fts_table))
                cursor.execute(self._fts_backfill_sql(fts_table))

            counts = {
                fts_table: cursor.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
                for fts_table in _FTS_DEFINITIONS
            }

            self.conn.commit()
            logger.info(
                "Rebuilt FTS indexes: %s",
                ", ".join(f"{t}={c}" for t, c in counts.items()),
            )
        except sqlite3.OperationalError as e:
            if "no such table" in str(e) or "no such module" in str(e):
                logger.debug("rebuild_fts_indexes skipped: %s", e)
            else:
                raise
        finally:
            # Always restore triggers — even if rebuild raised
            self.create_fts_triggers()

    # ========================================
    # FTS Health Check & Repair
    # ========================================

    _FTS_TABLE_MAP = {k: v["base_table"] for k, v in _FTS_DEFINITIONS.items()}

    def check_fts_health(self) -> dict:
        """Check FTS table health: existence and queryability.

        All three FTS tables are external content tables, so
        ``SELECT COUNT(*)`` delegates to the content table and row counts
        always match.  Drift detection via row counts is therefore a no-op.
        Real drift protection comes from sync triggers and
        auto-recovery on pipeline startup.

        We don't use FTS5 ``integrity-check`` because our triggers
        intentionally NULL content columns for opaque/hidden records.

        Safe to call when FTS5 is unavailable — returns all tables as
        ``"error"`` with an explanatory message.

        Returns a dict keyed by FTS table name, each with:
            status: "ok" | "error"
            fts_rows: int (or None if table missing)
            base_rows: int
            message: str (only on error)
            triggers_missing: list[str] (trigger names missing for this table)
        """
        cursor = self.conn.cursor()
        result = {}
        all_missing = set(self.check_fts_triggers())

        for fts_table, base_table in self._FTS_TABLE_MAP.items():
            table_triggers_missing = [t for t in all_missing if t.startswith(f"{fts_table}_")]
            base_rows = cursor.execute(f"SELECT COUNT(*) FROM {base_table}").fetchone()[0]

            try:
                fts_rows = cursor.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
            except sqlite3.OperationalError as e:
                if "no such module: fts5" in str(e) or "no such table" in str(e):
                    result[fts_table] = {
                        "status": "error",
                        "fts_rows": None,
                        "base_rows": base_rows,
                        "message": f"{fts_table} is missing or corrupted",
                        "triggers_missing": table_triggers_missing,
                    }
                    continue
                raise
            except sqlite3.DatabaseError:
                result[fts_table] = {
                    "status": "error",
                    "fts_rows": None,
                    "base_rows": base_rows,
                    "message": f"{fts_table} is corrupted or unreadable",
                    "triggers_missing": table_triggers_missing,
                }
                continue

            result[fts_table] = {
                "status": "ok",
                "fts_rows": fts_rows,
                "base_rows": base_rows,
                "triggers_missing": table_triggers_missing,
            }

        return result

    def repair_fts(self) -> dict:
        """Drop and rebuild all FTS tables from base table data.

        Safe to call when FTS5 is unavailable — logs a debug message
        and returns empty dict.  Always restores triggers in a finally
        block, matching the safety pattern of ``rebuild_fts_indexes()``.

        Returns a dict keyed by FTS table name with before/after row counts.
        """
        try:
            cursor = self.conn.cursor()

            # Capture before state
            before = {}
            for fts_table in self._FTS_TABLE_MAP:
                try:
                    before[fts_table] = cursor.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
                except sqlite3.OperationalError:
                    before[fts_table] = None

            # Drop triggers and FTS tables
            self.drop_fts_triggers()
            for fts_table in self._FTS_TABLE_MAP:
                cursor.execute(f"DROP TABLE IF EXISTS {fts_table}")

            # Recreate FTS virtual tables and backfill from base tables
            for fts_table in _FTS_DEFINITIONS:
                cursor.execute(self._fts_create_sql(fts_table))
                cursor.execute(self._fts_backfill_sql(fts_table))

            self.conn.commit()

            # Capture after state
            result = {}
            for fts_table in self._FTS_TABLE_MAP:
                after = cursor.execute(f"SELECT COUNT(*) FROM {fts_table}").fetchone()[0]
                result[fts_table] = {"before": before[fts_table], "after": after}

            logger.info(
                "Repaired FTS indexes: %s",
                ", ".join(f"{t}={r['after']}" for t, r in result.items()),
            )
            return result

        except sqlite3.OperationalError as e:
            if "no such module: fts5" in str(e):
                logger.debug("repair_fts skipped — FTS5 not available")
                return {}
            raise
        finally:
            # Always restore triggers — even if repair raised
            self.create_fts_triggers()
