"""
Source registry — runtime registry for all Footprinter data sources.

Seeded from config.yaml on database init. Provides a query/mutation API
that all other code can use to discover available sources.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

import yaml

from footprinter.paths import get_config_path


class ConfigError(Exception):
    """Raised when the config file is missing or invalid."""


def get_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from YAML file.

    Checks FOOTPRINTER_CONFIG env var first, then falls back to default path.
    Raises ConfigError with a friendly message on missing/corrupt files.
    """
    path = config_path or str(get_config_path())
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config not found: {path}\nRun 'fp setup' to get started.") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid config file: {path}\n{e}") from None


def home_path() -> str:
    """Return the user's home directory path."""
    return os.path.expanduser("~")


def remote_accounts() -> List[str]:
    """Return list of remote account names from config."""
    config = get_config()
    seeds = config.get("source_seeds", [])
    return [s["account"] for s in seeds if s.get("source_type") == "remote" and s.get("account")]


class SourceRegistry:
    """Registry for data sources, backed by the sources table."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def seed_from_config(self, config_path: Optional[str] = None) -> int:
        """Seed the sources table from config.yaml source_seeds.

        Uses INSERT OR IGNORE so user edits to existing rows are preserved.
        Returns the number of rows inserted.
        """
        config = get_config(config_path)
        seeds = config.get("source_seeds", [])
        inserted = 0
        cursor = self.conn.cursor()
        for seed in seeds:
            config_json = json.dumps(seed.get("config")) if seed.get("config") else None
            cursor.execute(
                """
                INSERT OR IGNORE
                    INTO sources (name, source_type, adapter, account, label, icon, enabled, config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    seed["name"],
                    seed["source_type"],
                    None,
                    seed.get("account"),
                    seed.get("label"),
                    seed.get("icon"),
                    1 if seed.get("enabled", True) else 0,
                    config_json,
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        self.conn.commit()
        return inserted

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def all_source_names(self) -> List[str]:
        """Return all source names."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sources ORDER BY name")
        return [row[0] for row in cursor.fetchall()]

    def all_sources(self) -> List[Dict[str, Any]]:
        """Return all sources as dicts."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sources ORDER BY name")
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_source(self, name: str) -> Optional[Dict[str, Any]]:
        """Return a single source by name, or None."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sources WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    def remote_source_names(self) -> List[str]:
        """Return names of sources with source_type='remote'."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sources WHERE source_type = 'remote' ORDER BY name")
        return [row[0] for row in cursor.fetchall()]

    def file_source_names(self) -> List[str]:
        """Return names of sources with source_type='file'."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sources WHERE source_type = 'file' ORDER BY name")
        return [row[0] for row in cursor.fetchall()]

    def source_label(self, name: str) -> Optional[str]:
        """Return the label for a source, or None if not found."""
        source = self.get_source(name)
        return source["label"] if source else None

    def source_account(self, name: str) -> Optional[str]:
        """Return the account for a source, or None if not found."""
        source = self.get_source(name)
        return source["account"] if source else None

    def is_remote_source(self, name: str) -> bool:
        """Return True if the named source is a remote source."""
        source = self.get_source(name)
        return source is not None and source["source_type"] == "remote"

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------

    def update_label(self, name: str, label: str) -> bool:
        """Update a source's label. Returns True if a row was updated."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE sources SET label = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
            (label, name),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a source. Returns True if a row was updated."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE sources SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
            (1 if enabled else 0, name),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def register_source(self, name: str, source_type: str, **kwargs) -> bool:
        """Register a new source. Returns True if inserted, False if already exists."""
        cursor = self.conn.cursor()
        config_json = json.dumps(kwargs.get("config")) if kwargs.get("config") else None
        cursor.execute(
            """
            INSERT OR IGNORE
                INTO sources (name, source_type, adapter, account, label, icon, enabled, config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                source_type,
                kwargs.get("adapter"),
                kwargs.get("account"),
                kwargs.get("label"),
                kwargs.get("icon"),
                1 if kwargs.get("enabled", True) else 0,
                config_json,
            ),
        )
        self.conn.commit()
        return cursor.rowcount > 0
