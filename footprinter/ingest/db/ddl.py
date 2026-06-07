"""Database DDL: table definitions, indexes, migrations, and column upgrades."""

import logging
import sqlite3

from footprinter.db_base import get_connection

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
#   access        TEXT DEFAULT 'inherit'  CHECK (allow|deny|inherit)
#   visibility    TEXT DEFAULT 'inherit'  CHECK (hidden|opaque|full|inherit)
#   access_source TEXT                    (policy scope that set access)
#   visibility_source TEXT                (policy scope that set visibility)
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
# (JSON) on tables that need it: files, chats, messages, emails.


# Single source of truth for the ingests table DDL.
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


# All 8 entity tables that carry access / visibility columns.
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


class DDLMixin:
    """Mixin providing database DDL initialization, migrations, and column upgrades."""

    def init_db(self):
        """Initialize database with schema."""
        self.conn = get_connection(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")

        cursor = self.conn.cursor()

        self._migrate_access_columns()
        self._migrate_project_name_column()
        self._ensure_super_entity_columns()

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

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

                -- Display
                display_name TEXT,

                -- Vectorization control
                vectorize INTEGER DEFAULT 1
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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_visibility ON files(visibility)")
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

                -- Access control
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility_source TEXT,
                access_source TEXT,

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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_folders_visibility ON folders(visibility)")
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

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visits_visibility ON visits(visibility)")

        # ========================================
        # Projects Table
        # ========================================
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT,
                description TEXT,
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),
                status_reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status_changed_at DATETIME,

                -- Client association
                client_id INTEGER REFERENCES clients(id),
                client TEXT,

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_client ON projects(client_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_visibility ON projects(visibility)")

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

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

                -- Client/project association
                client_id INTEGER REFERENCES clients(id),
                project_id INTEGER REFERENCES projects(id),

                -- Merge tracking
                merged_into_id INTEGER REFERENCES chats(id),

                -- Display
                display_name TEXT,

                -- Vectorization control
                vectorize INTEGER DEFAULT 1
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_created ON chats(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_account ON chats(account)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_status ON chats(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_client ON chats(client_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_visibility ON chats(visibility)")

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

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

                -- Display
                display_name TEXT,

                -- Vectorization control
                vectorize INTEGER DEFAULT 1,

                FOREIGN KEY (chat_id) REFERENCES chats(id)
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON messages(chat_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg_created ON messages(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_visibility ON messages(visibility)")
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

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_visibility ON emails(visibility)")

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
                status TEXT DEFAULT 'listed'
                    CHECK (status IN ('listed', 'unlisted', 'removed')),
                status_reason TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status_changed_at DATETIME,

                -- Access control
                access TEXT DEFAULT 'inherit'
                    CHECK (access IN ('allow', 'deny', 'inherit')),
                visibility TEXT DEFAULT 'inherit'
                    CHECK (visibility IN ('hidden', 'opaque', 'full', 'inherit')),
                access_source TEXT,
                visibility_source TEXT,

                -- Display
                display_name TEXT
            )
        """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_slug ON clients(slug)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_type ON clients(client_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_visibility ON clients(visibility)")

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
                setting TEXT NOT NULL CHECK (setting IN ('hidden', 'opaque', 'full')),
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
        self._init_fts_tables(cursor)

        # ========================================
        # display_name AFTER INSERT triggers
        # ========================================
        _DISPLAY_NAME_SOURCES = {
            "files": "NEW.name",
            "folders": "NEW.name",
            "visits": "NEW.title",
            "projects": "NEW.name",
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

        self._ensure_source_columns()
        self._ensure_vectorize_column()
        self._ensure_updated_at_columns()
        self.conn.commit()

        # Seed the sources registry from config
        try:
            from footprinter.source_registry import SourceRegistry

            registry = SourceRegistry(self.conn)
            registry.seed_from_config()
        except Exception as e:
            logger.warning(f"Could not seed sources from config: {e}")

    def _migrate_access_columns(self):
        """Rename mcp_view/mcp_read → visibility/access on existing databases (idempotent)."""
        cols = [
            row[1]
            for row in self.conn.execute("PRAGMA table_info(files)").fetchall()
        ]
        if "visibility" in cols:
            return
        if "mcp_view" not in cols:
            return

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for trigger_name in self._FTS_TRIGGER_NAMES:
                self.conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

            for table in ACCESS_CONTROL_TABLES:
                self.conn.execute(f"ALTER TABLE {table} RENAME COLUMN mcp_view TO visibility")
                self.conn.execute(f"ALTER TABLE {table} RENAME COLUMN mcp_read TO access")
                try:
                    self.conn.execute(f"ALTER TABLE {table} RENAME COLUMN mcp_view_source TO visibility_source")
                    self.conn.execute(f"ALTER TABLE {table} RENAME COLUMN mcp_read_source TO access_source")
                except sqlite3.OperationalError:
                    pass

            self.conn.execute("PRAGMA writable_schema = ON")
            for table in list(ACCESS_CONTROL_TABLES) + ["visibility_policies"]:
                row = self.conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not row:
                    continue
                new_sql = row[0]
                # _source variants must be replaced before their prefixes to avoid partial matches
                new_sql = new_sql.replace("mcp_view_source", "visibility_source")
                new_sql = new_sql.replace("mcp_read_source", "access_source")
                new_sql = new_sql.replace("mcp_view", "visibility")
                new_sql = new_sql.replace("mcp_read", "access")
                new_sql = new_sql.replace("'visible'", "'full'")
                self.conn.execute(
                    "UPDATE sqlite_master SET sql = ? WHERE type='table' AND name=?",
                    (new_sql, table),
                )
            self.conn.execute("PRAGMA writable_schema = OFF")
            v = self.conn.execute("PRAGMA schema_version").fetchone()[0]
            self.conn.execute(f"PRAGMA schema_version = {v + 1}")

            # Constraints now permit 'full'; migrate the values afterward so the
            # write isn't rejected by the pre-rename CHECK on existing databases.
            for table in ACCESS_CONTROL_TABLES:
                self.conn.execute(f"UPDATE {table} SET visibility = 'full' WHERE visibility = 'visible'")
            self.conn.execute("UPDATE visibility_policies SET setting = 'full' WHERE setting = 'visible'")

            result = self.conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                raise RuntimeError(f"Integrity check failed after access column migration: {result[0]}")

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        logger.info("Migrated mcp_view/mcp_read → visibility/access columns")

    def _migrate_project_name_column(self):
        """Rename the legacy ``projects.project_name`` column to ``name`` (idempotent).

        The fresh-DB schema standardized this column to ``name``, but databases
        created before the standardization still carry ``project_name``. Every
        search and navigation query joins ``projects`` and selects ``project.name``,
        so an unmigrated database fails at statement-prepare time with
        ``no such column: project.name`` — even with zero project rows, since
        SQLite resolves column names before reading data. SQLite (>= 3.25)
        rewrites the dependent ``set_display_name_projects`` trigger automatically
        on RENAME COLUMN, so no trigger handling is needed here.
        """
        cols = [
            row[1]
            for row in self.conn.execute("PRAGMA table_info(projects)").fetchall()
        ]

        # Idempotent guard. Check the already-migrated case first: if `name` is
        # present, return before attempting any rename (renaming would raise
        # "duplicate column name: name" should `project_name` also linger). An
        # empty `cols` (fresh DB, table not yet created) falls through this same
        # `project_name not in cols` check, so both no-op cases are covered.
        if "name" in cols:
            return
        if "project_name" not in cols:
            return

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("ALTER TABLE projects RENAME COLUMN project_name TO name")
            result = self.conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != "ok":
                raise RuntimeError(
                    f"Integrity check failed after project_name migration: {result[0]}"
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        logger.info("Migrated projects.project_name → name column")

    def _ensure_source_columns(self):
        """Add visibility_source/access_source to entity tables (idempotent upgrade)."""
        for table in ACCESS_CONTROL_TABLES:
            for col in ("visibility_source", "access_source"):
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise

    _SUPER_ENTITY_COLUMNS: dict[str, list[tuple[str, str]]] = {
        "projects": [("slug", "TEXT"), ("status_changed_at", "DATETIME")],
        "clients": [("slug", "TEXT"), ("status_changed_at", "DATETIME")],
        "folders": [("status_changed_at", "DATETIME")],
    }

    def _ensure_super_entity_columns(self):
        """Add slug/status_changed_at to super-entity tables (idempotent upgrade)."""
        for table, columns in self._SUPER_ENTITY_COLUMNS.items():
            for col_name, col_type in columns:
                try:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                    )
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" not in msg and "no such table" not in msg:
                        raise

    _VECTORIZE_TABLES = ("files", "messages", "chats")

    def _ensure_vectorize_column(self):
        """Add vectorize column and backfill from JSON metadata (idempotent)."""
        for table in self._VECTORIZE_TABLES:
            try:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN vectorize INTEGER DEFAULT 1"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
                continue
            self.conn.execute(
                f"UPDATE {table} SET vectorize = 0 "
                f"WHERE json_extract(metadata, '$.vectorize') = 0"
            )

    _UPDATED_AT_TABLES = ("clients",)

    def _ensure_updated_at_columns(self):
        """Add updated_at to tables that lack it on existing DBs; backfill from created_at.

        SQLite forbids DEFAULT CURRENT_TIMESTAMP on ALTER TABLE ADD COLUMN
        (non-constant default), so the column is added nullable and backfilled
        from created_at. Idempotent: a fresh DB already has the column, so the
        duplicate-column error is swallowed.
        """
        for table in self._UPDATED_AT_TABLES:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise
                continue
            self.conn.execute(
                f"UPDATE {table} SET updated_at = created_at WHERE updated_at IS NULL"
            )
