"""
Browser parsers for Safari and Chrome.
"""

import logging
import platform
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Generator

from footprinter.utils.time import UTC_FMT

logger = logging.getLogger(__name__)


class BrowserParser:
    """Base class for browser history parsing."""

    def __init__(self, lookback_days: int = 14, since: datetime | None = None):
        self.lookback_days = lookback_days
        # Ensure cutoff is tz-aware UTC for comparison with tz-aware epoch constants
        if since is not None:
            self.cutoff_date = since.astimezone(timezone.utc) if since.tzinfo else since.replace(tzinfo=timezone.utc)
        else:
            self.cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    def parse(self) -> Generator[Dict, None, None]:
        """Parse browser history. To be implemented by subclasses."""
        raise NotImplementedError


class SafariParser(BrowserParser):
    """Parse Safari browser history."""

    def __init__(self, lookback_days: int = 14, since: datetime | None = None):
        super().__init__(lookback_days, since=since)
        if platform.system() != "Darwin":
            self.history_db_path = None
        else:
            self.history_db_path = Path.home() / "Library" / "Safari" / "History.db"

    def parse(self) -> Generator[Dict, None, None]:
        """Parse Safari history from SQLite database."""
        if self.history_db_path is None:
            logger.warning(
                "Safari history parsing skipped (unsupported platform: %s)",
                platform.system(),
            )
            return
        if not self.history_db_path.exists():
            logger.warning(f"Safari history not found at {self.history_db_path}")
            return

        # Safari's History.db may be locked, so copy it first
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        conn = None
        try:
            shutil.copy2(self.history_db_path, tmp_path)

            conn = sqlite3.connect(tmp_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Safari's history_items schema drifts between macOS releases (the `title`
            # column was dropped in macOS Tahoe / 26.x). Introspect rather than hardcode.
            item_columns = {row[1] for row in cursor.execute("PRAGMA table_info(history_items)")}
            if "url" not in item_columns:
                logger.error(
                    "Safari schema incompatible: history_items is missing required `url` column "
                    "(found: %s). Skipping Safari history.",
                    sorted(item_columns) or "no columns",
                )
                return
            title_select = "hi.title" if "title" in item_columns else "NULL AS title"

            # Safari stores visit time as seconds since 2001-01-01 UTC (Core Data timestamp)
            core_data_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
            cutoff_timestamp = (self.cutoff_date - core_data_epoch).total_seconds()

            query = f"""
                SELECT
                    hv.visit_time,
                    hi.url,
                    {title_select}
                FROM history_visits hv
                JOIN history_items hi ON hv.history_item = hi.id
                WHERE hv.visit_time > ?
                ORDER BY hv.visit_time DESC
            """

            cursor.execute(query, (cutoff_timestamp,))

            for row in cursor:
                # Convert Safari's Core Data timestamp to datetime
                visit_time = core_data_epoch + timedelta(seconds=row["visit_time"])

                yield {
                    "url": row["url"],
                    "title": row["title"],
                    "visit_time": visit_time.strftime(UTC_FMT),
                    "browser": "safari",
                    "visit_count": 1,
                }

        except Exception as e:
            logger.error(f"Error parsing Safari history: {e}")
        finally:
            if conn:
                conn.close()
            # Clean up temp file
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass


class ChromeParser(BrowserParser):
    """Parse Chrome browser history."""

    def __init__(self, lookback_days: int = 14, since: datetime | None = None):
        super().__init__(lookback_days, since=since)
        system = platform.system()
        if system == "Darwin":
            self.history_db_path = (
                Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
            )
        elif system == "Linux":
            self.history_db_path = Path.home() / ".config" / "google-chrome" / "Default" / "History"
        else:
            self.history_db_path = None

    def parse(self) -> Generator[Dict, None, None]:
        """Parse Chrome history from SQLite database."""
        if self.history_db_path is None:
            logger.warning(
                "Chrome history parsing skipped (unsupported platform: %s)",
                platform.system(),
            )
            return
        if not self.history_db_path.exists():
            logger.warning(f"Chrome history not found at {self.history_db_path}")
            return

        # Chrome's History may be locked, so copy it first
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        conn = None
        try:
            shutil.copy2(self.history_db_path, tmp_path)

            conn = sqlite3.connect(tmp_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Chrome stores time as microseconds since 1601-01-01 UTC (Windows epoch)
            chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
            cutoff_timestamp = int((self.cutoff_date - chrome_epoch).total_seconds() * 1_000_000)

            query = """
                SELECT
                    urls.url,
                    urls.title,
                    urls.visit_count,
                    visits.visit_time
                FROM urls
                LEFT JOIN visits ON urls.id = visits.url
                WHERE visits.visit_time > ?
                ORDER BY visits.visit_time DESC
            """

            cursor.execute(query, (cutoff_timestamp,))

            for row in cursor:
                # Convert Chrome's timestamp to datetime
                visit_time = chrome_epoch + timedelta(microseconds=row["visit_time"])

                yield {
                    "url": row["url"],
                    "title": row["title"],
                    "visit_time": visit_time.strftime(UTC_FMT),
                    "browser": "chrome",
                    "visit_count": row["visit_count"] or 1,
                }

        except Exception as e:
            logger.error(f"Error parsing Chrome history: {e}")
        finally:
            if conn:
                conn.close()
            # Clean up temp file
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass


class BrowserManager:
    """Manage parsing of multiple browsers."""

    def __init__(self, config: Dict, since: datetime | None = None):
        self.config = config
        self.lookback_days = config.get("indexing", {}).get("lookback_days", 14)
        self.browsers = config.get("browsers", [])
        self.since = since

    def parse_all(self) -> Generator[Dict, None, None]:
        """Parse history from all configured browsers."""
        for browser in self.browsers:
            browser_lower = browser.lower()

            if browser_lower == "safari":
                parser = SafariParser(self.lookback_days, since=self.since)
                logger.info("Parsing Safari history...")
                yield from parser.parse()

            elif browser_lower == "chrome":
                parser = ChromeParser(self.lookback_days, since=self.since)
                logger.info("Parsing Chrome history...")
                yield from parser.parse()

            else:
                logger.warning(f"Unknown browser: {browser}")
