"""Tests for fp-api console_script entry point."""

import pytest


class TestApiCliEntryPoint:
    """Test the fp-api console_script entry point."""

    def test_cli_importable(self):
        """footprinter.api.server.cli is importable and callable."""
        from footprinter.api.server import cli

        assert callable(cli)

    def test_cli_help_exits_zero(self):
        """fp-api --help exits 0."""
        from footprinter.api.server import cli

        with pytest.raises(SystemExit) as exc_info:
            cli(["--help"])
        assert exc_info.value.code == 0

    def test_cli_default_host_port(self):
        """Default host is 127.0.0.1 and port is 8000."""
        from unittest.mock import patch

        from footprinter.api.server import cli

        with patch("footprinter.api.server.main") as mock_main:
            cli([])
            mock_main.assert_called_once_with(host="127.0.0.1", port=8000)
