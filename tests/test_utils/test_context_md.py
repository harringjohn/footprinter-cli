"""Tests for footprinter.utils.context_md.resolve_curated_context.

The resolver turns a super-entity row into the curated-context block carrying
the uniform excerpt contract (excerpt / excerpt_source == "context_md" /
chars_available / has_more) plus the resolved context_path, or None when no
readable Markdown file is found.
"""

import logging

import pytest

from footprinter.utils.context_md import _MAX_CONTEXT_BYTES, resolve_curated_context
from footprinter.utils.text import EXCERPT_BUDGET


@pytest.fixture(autouse=True)
def _home_is_tmp(tmp_path, monkeypatch):
    """Confine the home-containment root to ``tmp_path`` for every test here.

    ``resolve_curated_context`` rejects candidates outside ``Path.home()``.
    Pytest's ``tmp_path`` lives under the system temp root, not the real home,
    so without this every fixture file would fail confinement. ``Path.home()``
    honours ``$HOME`` on this platform, so pointing ``HOME`` at ``tmp_path``
    makes the curated files written under it pass confinement, while files
    written *outside* ``tmp_path`` (the confinement RED cases) still escape.
    """
    monkeypatch.setenv("HOME", str(tmp_path))


class TestFolderReadmeAutoDetect:
    """Folders auto-detect README.md in the folder path (convention-first)."""

    def test_returns_block_for_folder_readme(self, tmp_path):
        folder = tmp_path / "alpha"
        folder.mkdir()
        readme = folder / "README.md"
        readme.write_text("Alpha project notes.")

        row = {"path": str(folder)}
        block = resolve_curated_context(row, "folder")

        assert block is not None
        assert block["excerpt_source"] == "context_md"
        assert block["excerpt"] == "Alpha project notes."
        assert block["context_path"] == str(readme)
        assert block["has_more"] is False

    def test_has_more_for_over_budget_readme(self, tmp_path):
        folder = tmp_path / "big"
        folder.mkdir()
        readme = folder / "README.md"
        body = "x" * (EXCERPT_BUDGET + 200)
        readme.write_text(body)

        block = resolve_curated_context({"path": str(folder)}, "folder")

        assert block is not None
        assert block["excerpt"] == "x" * EXCERPT_BUDGET
        assert block["chars_returned"] == EXCERPT_BUDGET
        assert block["chars_available"] == len(body)
        assert block["has_more"] is True

    def test_folder_without_readme_returns_none(self, tmp_path):
        folder = tmp_path / "empty"
        folder.mkdir()
        assert resolve_curated_context({"path": str(folder)}, "folder") is None


class TestColumnOverride:
    """An explicit context_path overrides the convention for all three types."""

    def test_project_column_override(self, tmp_path):
        md = tmp_path / "PROJECT.md"
        md.write_text("Curated project context.")

        row = {"context_path": str(md)}
        block = resolve_curated_context(row, "project")

        assert block is not None
        assert block["excerpt"] == "Curated project context."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(md)

    def test_folder_column_override_beats_readme(self, tmp_path):
        folder = tmp_path / "alpha"
        folder.mkdir()
        (folder / "README.md").write_text("the readme")
        override = tmp_path / "override.md"
        override.write_text("the override")

        row = {"path": str(folder), "context_path": str(override)}
        block = resolve_curated_context(row, "folder")

        assert block is not None
        assert block["excerpt"] == "the override"
        assert block["context_path"] == str(override)


class TestClientConvention:
    """Clients resolve context/client-<slug>.md under a context root."""

    def test_client_convention_block(self, tmp_path):
        ctx_dir = tmp_path / "context"
        ctx_dir.mkdir()
        md = ctx_dir / "client-acme.md"
        md.write_text("Acme background.")

        row = {"slug": "acme"}
        block = resolve_curated_context(row, "client", context_root=tmp_path)

        assert block is not None
        assert block["excerpt"] == "Acme background."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(md)

    def test_client_convention_missing_file_returns_none(self, tmp_path):
        row = {"slug": "acme"}
        assert resolve_curated_context(row, "client", context_root=tmp_path) is None

    def test_client_no_root_no_path_returns_none(self, tmp_path):
        row = {"slug": "acme"}
        assert resolve_curated_context(row, "client") is None


class TestMissingAndUnset:
    """Missing-file tolerant and unset → None across types."""

    def test_nonexistent_context_path_returns_none(self, tmp_path):
        row = {"context_path": str(tmp_path / "does-not-exist.md")}
        assert resolve_curated_context(row, "project") is None

    def test_project_without_context_path_returns_none(self, tmp_path):
        # Projects have no convention auto-detect (no path column).
        assert resolve_curated_context({}, "project") is None

    def test_unset_folder_returns_none(self, tmp_path):
        # No path, no context_path → nothing to resolve.
        assert resolve_curated_context({}, "folder") is None


class TestSizeCap:
    """The read is bounded by ``_MAX_CONTEXT_BYTES`` — never the full file."""

    def test_oversized_file_is_bounded(self, tmp_path):
        md = tmp_path / "huge.md"
        # Write a file comfortably larger than the read cap.
        md.write_text("x" * (_MAX_CONTEXT_BYTES + 4096))

        block = resolve_curated_context({"context_path": str(md)}, "project")

        assert block is not None
        # chars_available reflects the bounded-decoded length, not the on-disk
        # length, so a multi-MB note never loads fully into memory.
        assert block["chars_available"] <= _MAX_CONTEXT_BYTES


class TestConfinement:
    """Candidates resolving outside the home root are rejected (defense-in-depth)."""

    def test_context_path_outside_home_returns_none(self, tmp_path):
        # tmp_path is home (autouse fixture); a sibling dir escapes it.
        outside = tmp_path.parent / "outside-home"
        outside.mkdir(exist_ok=True)
        md = outside / "escape.md"
        md.write_text("Should never be read.")

        assert resolve_curated_context({"context_path": str(md)}, "project") is None

    def test_symlink_escaping_root_returns_none(self, tmp_path):
        # A real file outside home, reached via a symlink that lives inside home.
        outside = tmp_path.parent / "symlink-target"
        outside.mkdir(exist_ok=True)
        target = outside / "secret.md"
        target.write_text("Symlink target outside home.")

        link = tmp_path / "link.md"
        link.symlink_to(target)

        # The symlink itself is under home, but .resolve() follows it to the
        # escaping target, so confinement must still reject it.
        assert resolve_curated_context({"context_path": str(link)}, "project") is None


class TestErrorLogging:
    """Permission/decode failures log at debug instead of dropping silently."""

    def test_oserror_is_logged(self, tmp_path, caplog, monkeypatch):
        md = tmp_path / "blocked.md"
        md.write_text("unreadable")

        real_open = open

        def _raising_open(file, *args, **kwargs):
            if str(file) == str(md):
                raise PermissionError("permission denied")
            return real_open(file, *args, **kwargs)

        # Confinement and is_file() both pass for this real path; the bounded
        # read raises OSError (PermissionError) — that must be logged.
        monkeypatch.setattr("footprinter.utils.context_md.open", _raising_open, raising=False)

        with caplog.at_level(logging.DEBUG, logger="footprinter.utils.context_md"):
            result = resolve_curated_context({"context_path": str(md)}, "project")

        assert result is None
        assert any(
            "blocked.md" in r.getMessage() and r.levelno == logging.DEBUG
            for r in caplog.records
        )
