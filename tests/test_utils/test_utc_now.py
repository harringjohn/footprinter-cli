"""Tests for the shared utc_now_iso() utility and naive-datetime enforcement."""

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Pattern matching YYYY-MM-DD HH:MM:SS (SQLite CURRENT_TIMESTAMP format)
_SQLITE_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


class TestUtcNowIso:
    """Tests for footprinter.utils.time.utc_now_iso."""

    def test_returns_string(self):
        from footprinter.utils.time import utc_now_iso

        result = utc_now_iso()
        assert isinstance(result, str)

    def test_matches_sqlite_format(self):
        """utc_now_iso() should return YYYY-MM-DD HH:MM:SS (CURRENT_TIMESTAMP format)."""
        from footprinter.utils.time import utc_now_iso

        result = utc_now_iso()
        assert _SQLITE_TS_RE.fullmatch(result), f"Expected YYYY-MM-DD HH:MM:SS, got '{result}'"

    def test_is_parseable_as_utc(self):
        """utc_now_iso() output should be parseable and represent current UTC time."""
        from footprinter.utils.time import utc_now_iso

        result = utc_now_iso()
        parsed = datetime.fromisoformat(result)
        # Parsed value should be close to current UTC time (within 2 seconds)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = abs((now_utc - parsed).total_seconds())
        assert delta < 2, f"Parsed time {parsed} is {delta}s from UTC now {now_utc}"


class TestNoNaiveDatetime:
    """Enforce that tool-scope code doesn't use naive datetime.now().isoformat()."""

    TOOL_SCOPE_DIRS = [
        "footprinter/ingest",
        "footprinter/services",
        "footprinter/cli",
        "footprinter/connectors",
        "footprinter/db",
        "footprinter/utils",
        "footprinter/semantic",
    ]

    def test_no_naive_datetime_in_tool_scope(self):
        """No bare datetime.now().isoformat() (without timezone) in tool-scope code."""
        root = Path(__file__).parent.parent.parent
        dirs = [str(root / d) for d in self.TOOL_SCOPE_DIRS if (root / d).exists()]

        result = subprocess.run(
            ["grep", "-rn", r"datetime\.now()\.isoformat()", *dirs],
            capture_output=True,
            text=True,
        )
        matches = [line for line in result.stdout.strip().splitlines() if line.strip()]
        assert matches == [], (
            f"Found {len(matches)} naive datetime.now().isoformat() calls in tool-scope code:\n" + "\n".join(matches)
        )
