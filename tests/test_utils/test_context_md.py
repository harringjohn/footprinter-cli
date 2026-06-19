"""Tests for footprinter.utils.context_md.resolve_curated_context.

The resolver turns a super-entity row into the curated-context block carrying
the uniform excerpt contract (excerpt / excerpt_source == "context_md" /
chars_available / has_more) plus the resolved context_path, or None when no
readable Markdown file is found.
"""

from footprinter.utils.context_md import resolve_curated_context
from footprinter.utils.text import EXCERPT_BUDGET


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
