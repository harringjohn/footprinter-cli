"""FTS5 full-text search index management."""

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


# Single source of truth for FTS5 virtual table definitions.
# All CREATE TABLE, backfill, and trigger SQL is derived from this.
_FTS_DEFINITIONS: dict[str, dict[str, Any]] = {
    "files_fts": {
        "base_table": "files",
        "columns": ["name", "content_preview"],
        "content_columns": ["content_preview"],
    },
    "emails_fts": {
        "base_table": "emails",
        "columns": ["subject", "from_name", "from_address", "body_preview"],
        "content_columns": ["body_preview"],
    },
    "chats_fts": {
        "base_table": "chats",
        "columns": ["title"],
        "content_columns": [],
    },
}


class FTSMixin:
    """Mixin providing FTS5 trigger management, health checks, and repair."""

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
                    f"CASE WHEN COALESCE(visibility, 'inherit') IN ('opaque', 'hidden') THEN NULL ELSE {col} END"
                )
            else:
                select_exprs.append(col)
        select_str = ", ".join(select_exprs)
        return f"INSERT INTO {fts_table}(rowid, {cols_str}) SELECT id, {select_str} FROM {defn['base_table']}"

    @staticmethod
    def _fts_col_expr(col: str, prefix: str, content_columns: set[str]) -> str:
        """Return a SQL expression for a column value in FTS triggers.

        Content columns are NULLed when visibility is opaque or hidden,
        preventing sensitive content from entering the FTS index.
        Metadata columns (name, subject, title, etc.) pass through unchanged.
        """
        if col in content_columns:
            return (
                f"CASE WHEN COALESCE({prefix}.visibility, 'inherit') "
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

        new_vals = ", ".join(FTSMixin._fts_col_expr(c, "new", content_cols) for c in cols)
        old_vals = ", ".join(FTSMixin._fts_col_expr(c, "old", content_cols) for c in cols)

        # WHEN clause for _au: only re-index when FTS-tracked columns or
        # visibility change.  visibility affects what's stored in FTS for
        # content columns (opaque/hidden → NULL).  Prevents spurious
        # re-indexing from non-FTS updates (e.g. display_name) and avoids
        # corruption when AFTER INSERT triggers do UPDATE on the same row.
        when_cols = list(cols) + ["visibility"]
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

    def _init_fts_tables(self, cursor) -> None:
        """Create FTS5 tables, triggers, and backfill indexes.

        Called from init_db() after all base tables are created.
        """
        # Capture which FTS tables existed BEFORE the CREATE IF NOT EXISTS
        # below.  COUNT(*) on an external-content FTS5 table is delegated
        # to the content table and therefore unreliable as an emptiness
        # check — the prior gate `if COUNT(*) == 0` always
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
        # _fts_backfill_sql's visibility filtering and also self-heals
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
