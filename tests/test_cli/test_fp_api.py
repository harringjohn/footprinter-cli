"""Tests for fp api — HTTP API server entry point.

Validates:
  1. fp api --help exits 0 and lists --host/--port flags
  2. fp api dispatches to footprinter.api.server.main with correct host/port
"""

import importlib.util
from unittest.mock import patch

import pytest
from conftest import run_fp

# The [api] extra (FastAPI) may not be installed in dev environments.
# Skip startup tests when it's missing — help text still renders without it.
_FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


# ---------------------------------------------------------------------------
# 1. Help
# ---------------------------------------------------------------------------


class TestApiHelp:
    """fp api --help exits 0 and shows --host/--port flags."""

    def test_help_exits_zero(self):
        _, _, code = run_fp("api", "--help")
        assert code == 0

    def test_help_shows_host_flag(self):
        stdout, stderr, _ = run_fp("api", "--help")
        output = stdout + stderr
        assert "--host" in output

    def test_help_shows_port_flag(self):
        stdout, stderr, _ = run_fp("api", "--help")
        output = stdout + stderr
        assert "--port" in output


# ---------------------------------------------------------------------------
# 2. Startup
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi extra not installed")
class TestApiStartup:
    """fp api invokes footprinter.api.server.main with parsed host/port."""

    @patch("footprinter.api.server.main")
    def test_default_host_and_port(self, mock_main):
        _, _, code = run_fp("api")

        assert code == 0
        mock_main.assert_called_once_with(host="127.0.0.1", port=8000)

    @patch("footprinter.api.server.main")
    def test_custom_port(self, mock_main):
        run_fp("api", "--port", "9000")

        mock_main.assert_called_once_with(host="127.0.0.1", port=9000)

    @patch("footprinter.api.server.main")
    def test_custom_host(self, mock_main):
        run_fp("api", "--host", "0.0.0.0", "--port", "8080", "--allow-insecure-bind")

        mock_main.assert_called_once_with(host="0.0.0.0", port=8080)


# ---------------------------------------------------------------------------
# 3. Host safety
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="fastapi extra not installed")
class TestApiHostSafety:
    """Non-loopback binds require explicit --allow-insecure-bind opt-in."""

    @patch("footprinter.api.server.main")
    def test_non_loopback_host_refused_without_flag(self, mock_main):
        _, stderr, code = run_fp("api", "--host", "0.0.0.0")

        assert code != 0
        assert "--allow-insecure-bind" in stderr
        mock_main.assert_not_called()

    @patch("footprinter.api.server.main")
    def test_non_loopback_host_with_flag_proceeds_and_warns(self, mock_main):
        stdout, stderr, _ = run_fp("api", "--host", "0.0.0.0", "--allow-insecure-bind")
        output = stdout + stderr

        mock_main.assert_called_once_with(host="0.0.0.0", port=8000)
        assert "WARNING" in output
        assert "no authentication" in output.lower()

    @patch("footprinter.api.server.main")
    def test_loopback_default_unchanged(self, mock_main):
        stdout, stderr, code = run_fp("api")

        assert code == 0
        mock_main.assert_called_once_with(host="127.0.0.1", port=8000)
        assert "WARNING" not in (stdout + stderr)

    @patch("footprinter.api.server.main")
    def test_localhost_string_treated_as_loopback(self, mock_main):
        stdout, stderr, code = run_fp("api", "--host", "localhost")

        assert code == 0
        mock_main.assert_called_once_with(host="localhost", port=8000)
        assert "WARNING" not in (stdout + stderr)

    @patch("footprinter.api.server.main")
    def test_ipv6_loopback_treated_as_loopback(self, mock_main):
        stdout, stderr, code = run_fp("api", "--host", "::1")

        assert code == 0
        mock_main.assert_called_once_with(host="::1", port=8000)
        assert "WARNING" not in (stdout + stderr)

    @patch("footprinter.api.server.main")
    def test_allow_insecure_bind_with_loopback_is_noop(self, mock_main):
        stdout, stderr, code = run_fp("api", "--allow-insecure-bind")

        assert code == 0
        mock_main.assert_called_once_with(host="127.0.0.1", port=8000)
        assert "WARNING" not in (stdout + stderr)
