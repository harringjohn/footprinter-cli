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
