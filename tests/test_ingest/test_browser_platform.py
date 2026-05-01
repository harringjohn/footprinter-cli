"""Tests for browser parser platform-aware behavior."""

import logging
from unittest.mock import patch

from footprinter.cli.setup import collect_answers, get_available_browsers

# ---------------------------------------------------------------------------
# Setup wizard: platform-specific browser filtering
# ---------------------------------------------------------------------------


class TestGetAvailableBrowsers:
    """get_available_browsers() returns platform-appropriate browser list."""

    def test_safari_available_on_macos(self):
        """Safari and Chrome both offered on macOS."""
        with patch("footprinter.cli.setup.sys") as mock_sys:
            mock_sys.platform = "darwin"
            browsers = get_available_browsers()
            assert "safari" in browsers
            assert "chrome" in browsers

    def test_safari_not_available_on_linux(self):
        """Only Chrome offered on Linux — Safari is macOS-only."""
        with patch("footprinter.cli.setup.sys") as mock_sys:
            mock_sys.platform = "linux"
            browsers = get_available_browsers()
            assert "safari" not in browsers
            assert "chrome" in browsers


class TestCollectAnswersBrowserPlatform:
    """collect_answers() respects platform browser filtering."""

    def test_collect_answers_no_safari_on_linux(self):
        """On Linux, only Chrome should appear in browser prompts."""
        with (
            patch("footprinter.cli.setup.sys") as mock_sys,
            patch("footprinter.cli.setup.os.path.isdir") as mock_isdir,
            patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p),
            patch("footprinter.cli.setup.Prompt.ask") as mock_prompt,
            patch("footprinter.cli.setup.Confirm.ask") as mock_confirm,
            patch("footprinter.cli.setup.console"),
        ):
            mock_sys.platform = "linux"
            mock_isdir.side_effect = lambda p: p == "/tmp"
            mock_prompt.side_effect = ["/tmp", ""]
            # Only one browser (chrome) should be offered
            mock_confirm.side_effect = [True]
            answers = collect_answers()
            assert answers["browsers"] == ["chrome"]
            # Verify Safari was never mentioned in any Confirm.ask call
            confirm_calls = [str(c) for c in mock_confirm.call_args_list]
            for call in confirm_calls:
                assert "safari" not in call.lower()


# ---------------------------------------------------------------------------
# Browser parsers: platform-aware behavior
# ---------------------------------------------------------------------------


def test_safari_skips_on_non_macos():
    """SafariParser should return empty results on non-macOS."""
    with patch("footprinter.ingest.browser_indexer.platform") as mock_platform:
        mock_platform.system.return_value = "Linux"

        from footprinter.ingest.browser_indexer import SafariParser

        parser = SafariParser()

        assert parser.history_db_path is None
        results = list(parser.parse())
        assert results == []


def test_chrome_skips_on_unsupported_platform():
    """ChromeParser should return empty results on unsupported platforms."""
    with patch("footprinter.ingest.browser_indexer.platform") as mock_platform:
        mock_platform.system.return_value = "Windows"

        from footprinter.ingest.browser_indexer import ChromeParser

        parser = ChromeParser()

        assert parser.history_db_path is None
        results = list(parser.parse())
        assert results == []


def test_chrome_sets_path_on_linux():
    """ChromeParser should resolve Chrome history path on Linux."""
    with patch("footprinter.ingest.browser_indexer.platform") as mock_platform:
        mock_platform.system.return_value = "Linux"

        from footprinter.ingest.browser_indexer import ChromeParser

        parser = ChromeParser()

        assert parser.history_db_path is not None
        assert ".config/google-chrome" in str(parser.history_db_path)


def test_chrome_skips_on_unsupported_with_warning(caplog):
    """ChromeParser should log WARNING (not INFO) on unsupported platforms."""
    with patch("footprinter.ingest.browser_indexer.platform") as mock_platform:
        mock_platform.system.return_value = "Windows"

        from footprinter.ingest.browser_indexer import ChromeParser

        parser = ChromeParser()
        assert parser.history_db_path is None

        with caplog.at_level(logging.DEBUG, logger="footprinter.ingest.browser_indexer"):
            list(parser.parse())

        skip_records = [r for r in caplog.records if "skipped" in r.message.lower()]
        assert skip_records, "Expected a log message about skipping Chrome parsing"
        assert skip_records[0].levelno == logging.WARNING


def test_safari_sets_path_on_macos():
    """SafariParser should set history_db_path on macOS."""
    with patch("footprinter.ingest.browser_indexer.platform") as mock_platform:
        mock_platform.system.return_value = "Darwin"

        from footprinter.ingest.browser_indexer import SafariParser

        parser = SafariParser()

        assert parser.history_db_path is not None
        assert "Safari" in str(parser.history_db_path)


def test_chrome_sets_path_on_macos():
    """ChromeParser should set history_db_path on macOS."""
    with patch("footprinter.ingest.browser_indexer.platform") as mock_platform:
        mock_platform.system.return_value = "Darwin"

        from footprinter.ingest.browser_indexer import ChromeParser

        parser = ChromeParser()

        assert parser.history_db_path is not None
        assert "Chrome" in str(parser.history_db_path)


# ---------------------------------------------------------------------------
# SafariParser: schema-compatibility across macOS versions
# ---------------------------------------------------------------------------


def _make_safari_db(path, *, include_title: bool, include_url: bool = True, rows=None):
    """Build a synthetic Safari History.db at `path`.

    Mirrors only the fields SafariParser.parse() reads. `rows` is a list of
    (url, title, visit_time) tuples; visit_time is Core Data seconds since
    2001-01-01 UTC.
    """
    import sqlite3
    from datetime import datetime, timezone

    item_cols = ["id INTEGER PRIMARY KEY"]
    if include_url:
        item_cols.append("url TEXT")
    if include_title:
        item_cols.append("title TEXT")
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"CREATE TABLE history_items ({', '.join(item_cols)})")
        conn.execute(
            "CREATE TABLE history_visits ("
            "id INTEGER PRIMARY KEY, "
            "history_item INTEGER, "
            "visit_time REAL"
            ")"
        )

        # Default to one recent row if caller didn't supply any
        if rows is None:
            now_core = (datetime.now(timezone.utc) - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds()
            rows = [("https://example.com/a", "Example A", now_core)]

        for idx, (url, title, vt) in enumerate(rows, start=1):
            cols: list[str] = ["id"]
            vals: list[str] = ["?"]
            params: list = [idx]
            if include_url:
                cols.append("url")
                vals.append("?")
                params.append(url)
            if include_title:
                cols.append("title")
                vals.append("?")
                params.append(title)
            conn.execute(
                f"INSERT INTO history_items ({', '.join(cols)}) VALUES ({', '.join(vals)})",
                params,
            )
            conn.execute(
                "INSERT INTO history_visits (history_item, visit_time) VALUES (?, ?)",
                (idx, vt),
            )
        conn.commit()
    finally:
        conn.close()


def test_safari_parses_current_macos_schema_without_title(tmp_path):
    """Current macOS Safari has no `title` column; parser must still yield rows."""
    from footprinter.ingest.browser_indexer import SafariParser

    db_path = tmp_path / "History.db"
    _make_safari_db(db_path, include_title=False)

    parser = SafariParser(lookback_days=30)
    parser.history_db_path = db_path

    results = list(parser.parse())

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/a"
    assert results[0]["title"] is None
    assert results[0]["browser"] == "safari"


def test_safari_parses_legacy_schema_with_title(tmp_path):
    """Legacy macOS Safari has a `title` column; parser must still read it."""
    from footprinter.ingest.browser_indexer import SafariParser

    db_path = tmp_path / "History.db"
    _make_safari_db(db_path, include_title=True)

    parser = SafariParser(lookback_days=30)
    parser.history_db_path = db_path

    results = list(parser.parse())

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/a"
    assert results[0]["title"] == "Example A"


def test_safari_handles_missing_title_value_gracefully(tmp_path):
    """Legacy schema row with NULL title yields title=None without crashing."""
    from datetime import datetime, timezone

    from footprinter.ingest.browser_indexer import SafariParser

    db_path = tmp_path / "History.db"
    now_core = (datetime.now(timezone.utc) - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds()
    _make_safari_db(
        db_path,
        include_title=True,
        rows=[("https://example.com/b", None, now_core)],
    )

    parser = SafariParser(lookback_days=30)
    parser.history_db_path = db_path

    results = list(parser.parse())

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/b"
    assert results[0]["title"] is None


def test_safari_surfaces_unexpected_schema(tmp_path, caplog):
    """A truly broken schema (no `url` column) must log ERROR — not silently return 0."""
    from footprinter.ingest.browser_indexer import SafariParser

    db_path = tmp_path / "History.db"
    _make_safari_db(db_path, include_title=False, include_url=False)

    parser = SafariParser(lookback_days=30)
    parser.history_db_path = db_path

    with caplog.at_level(logging.ERROR, logger="footprinter.ingest.browser_indexer"):
        results = list(parser.parse())

    assert results == []
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Expected an ERROR log when Safari schema is incompatible"
    msg = " ".join(r.message for r in error_records).lower()
    assert "url" in msg, f"Error log should name the missing column. Got: {msg}"
