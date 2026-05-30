"""Tests for fp-api console_script entry point (footprinter.api.server.cli).

Validates:
  1. cli(["--help"]) exits 0
  2. cli() dispatches to main() with correct host/port
  3. Host safety: non-loopback binds require --allow-insecure-bind
"""

import importlib.util
from unittest.mock import patch

import pytest

_FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestApiCliHelp:
    """fp-api --help exits 0."""

    def test_help_exits_zero(self):
        from footprinter.api.server import cli

        with pytest.raises(SystemExit) as exc_info:
            cli(["--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# 2. Startup
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi extra not installed")
class TestApiCliStartup:
    """cli() invokes main() with parsed host/port."""

    @patch("footprinter.api.server.main")
    def test_default_host_and_port(self, mock_main):
        from footprinter.api.server import cli

        cli([])
        mock_main.assert_called_once_with(host="127.0.0.1", port=8000)

    @patch("footprinter.api.server.main")
    def test_custom_port(self, mock_main):
        from footprinter.api.server import cli

        cli(["--port", "9000"])
        mock_main.assert_called_once_with(host="127.0.0.1", port=9000)

    @patch("footprinter.api.server.main")
    def test_custom_host_with_insecure_bind(self, mock_main):
        from footprinter.api.server import cli

        cli(["--host", "0.0.0.0", "--allow-insecure-bind"])
        mock_main.assert_called_once_with(host="0.0.0.0", port=8000)


# ---------------------------------------------------------------------------
# 3. Host safety
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi extra not installed")
class TestApiCliHostSafety:
    """Non-loopback binds require explicit --allow-insecure-bind opt-in."""

    def test_non_loopback_host_refused_without_flag(self):
        from footprinter.api.server import cli

        with pytest.raises(SystemExit) as exc_info:
            cli(["--host", "0.0.0.0"])
        assert exc_info.value.code == 2

    @patch("footprinter.api.server.main")
    def test_non_loopback_host_with_flag_warns(self, mock_main, capsys):
        from footprinter.api.server import cli

        cli(["--host", "0.0.0.0", "--allow-insecure-bind"])
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "no authentication" in captured.err.lower()

    @patch("footprinter.api.server.main")
    def test_loopback_default_no_warning(self, mock_main, capsys):
        from footprinter.api.server import cli

        cli([])
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    @patch("footprinter.api.server.main")
    def test_localhost_treated_as_loopback(self, mock_main, capsys):
        from footprinter.api.server import cli

        cli(["--host", "localhost"])
        mock_main.assert_called_once_with(host="localhost", port=8000)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    @patch("footprinter.api.server.main")
    def test_ipv6_loopback_treated_as_loopback(self, mock_main, capsys):
        from footprinter.api.server import cli

        cli(["--host", "::1"])
        mock_main.assert_called_once_with(host="::1", port=8000)
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err
