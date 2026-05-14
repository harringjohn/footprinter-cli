"""Tests for footprinter.utils.paths.abbreviate_home."""

import os

from footprinter.utils.paths import abbreviate_home


class TestAbbreviateHome:
    def test_replaces_home_prefix(self):
        home = os.path.expanduser("~")
        result = abbreviate_home(home + "/foo/bar.txt")
        assert result == "~/foo/bar.txt"

    def test_no_home_prefix_unchanged(self):
        result = abbreviate_home("/var/log/app.log")
        assert result == "/var/log/app.log"

    def test_empty_string(self):
        assert abbreviate_home("") == ""

    def test_none_returns_empty(self):
        assert abbreviate_home(None) == ""

    def test_exact_home_path(self):
        home = os.path.expanduser("~")
        result = abbreviate_home(home)
        assert result == "~"
