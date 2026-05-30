"""Convention compliance tests for CLI modules.

Enforces structural invariants across all CLI modules:
- open_db() context manager exists and works
- Dispatcher dest names standardized to "noun"; connect uses "verb"
- get_file() returns explicit field dict
- _make_slug not re-exported from _common or queries
"""

import argparse
import sqlite3

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conventions_db(tmp_path):
    """Create a minimal DB with tool-scope schema for convention tests."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "conv.db"
    db = Database(str(db_path))
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# open_db() context manager
# ---------------------------------------------------------------------------


class TestOpenDb:
    def test_open_db_is_context_manager(self, conventions_db):
        """open_db() yields a connection and closes it on exit."""
        from footprinter.cli._common import open_db

        with open_db(conventions_db) as conn:
            assert conn is not None
            assert conn.row_factory == sqlite3.Row
            # Connection should be usable inside the block
            conn.execute("SELECT 1")

        # After exiting, the connection should be closed
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_open_db_exits_on_missing_db(self, tmp_path):
        """open_db() calls sys.exit(1) when DB not found."""
        from footprinter.cli._common import open_db

        with pytest.raises(SystemExit) as exc_info:
            with open_db(tmp_path / "nonexistent.db"):
                pass
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# dest="verb" convention
# ---------------------------------------------------------------------------


# Dispatcher modules that use dest="noun" for sub-subparsers
DISPATCHER_MODULES = [
    "footprinter.cli.view",
    "footprinter.cli.delete",
]


class TestNounDestConvention:
    @pytest.mark.parametrize("module_path", DISPATCHER_MODULES)
    def test_dispatcher_module_uses_noun_dest(self, module_path):
        """Dispatcher CLI modules must use dest='noun' for sub-subparsers."""
        import importlib

        mod = importlib.import_module(module_path)
        root = argparse.ArgumentParser()
        subs = root.add_subparsers()
        mod.register(subs)

        cmd_name = module_path.rsplit(".", 1)[-1]
        entity_parser = subs.choices[cmd_name]
        for action in entity_parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                assert action.dest == "noun", f"{module_path} uses dest={action.dest!r}, expected 'noun'"
                break
        else:
            pytest.fail(f"{module_path} has no sub-subparsers")

    def test_connect_module_uses_verb_dest(self):
        """Connect module still uses dest='verb' for its sub-subparsers."""
        import importlib

        mod = importlib.import_module("footprinter.cli.connect")
        root = argparse.ArgumentParser()
        subs = root.add_subparsers()
        mod.register(subs)

        entity_parser = subs.choices["connect"]
        for action in entity_parser._subparsers._actions:
            if isinstance(action, argparse._SubParsersAction):
                assert action.dest == "verb", f"connect uses dest={action.dest!r}, expected 'verb'"
                break
        else:
            pytest.fail("connect has no sub-subparsers")


# ---------------------------------------------------------------------------
# get_file() explicit fields
# ---------------------------------------------------------------------------


class TestEntityExplicitFields:
    def test_entity_view_json_has_explicit_fields(self, conventions_db):
        """get_file() must return a dict with only known keys."""
        conn = sqlite3.connect(str(conventions_db))
        conn.row_factory = sqlite3.Row

        # Insert a minimal file
        conn.execute(
            """INSERT INTO files (name, path, source, status, content_type,
                                      size_bytes)
               VALUES ('test.py', '/tmp/test.py', 'local', 'listed', 'code',
                       100)"""
        )
        conn.commit()
        art_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from footprinter.db.files import get_file

        result = get_file(conn, art_id)
        conn.close()

        assert result is not None

        # Known keys that should be in the explicit field dict
        expected_keys = {
            "id",
            "name",
            "path",
            "source",
            "status",
            "status_reason",
            "content_type",
            "size_bytes",
            "created_at",
            "modified_at",
            "indexed_at",
            "project_id",
            "remote_file_id",
            "md5_hash",
            "external_id",
            "account",
            "mime_type",
            "visibility",
            "access",
            "visibility_source",
            "access_source",
            "project_name",
            "classification",
            "rc_content_type",
            "context",
            "retention_action",
            "local_score",
            "pii_detected",
            "pii_types",
        }

        actual_keys = set(result.keys())

        # The result should NOT contain raw DB columns that aren't in our
        # explicit list (e.g., migrated_to_account, source_id, etc.)
        unexpected = actual_keys - expected_keys - {"remote_name", "remote_path"}
        assert not unexpected, f"get_file() returned unexpected keys: {unexpected}"

    def test_get_file_returns_external_id_account_mime_type(self, conventions_db):
        """get_file() must include external_id, account, and mime_type."""
        conn = sqlite3.connect(str(conventions_db))
        conn.row_factory = sqlite3.Row

        conn.execute(
            """INSERT INTO files (name, path, source, status, content_type,
                                  size_bytes, external_id, account, mime_type)
               VALUES ('report.pdf', '/tmp/report.pdf', 'workdrive', 'listed',
                       'document', 5000, 'ext-abc-123', 'work', 'application/pdf')"""
        )
        conn.commit()
        art_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from footprinter.db.files import get_file

        result = get_file(conn, art_id)
        conn.close()

        assert result is not None
        assert result["external_id"] == "ext-abc-123"
        assert result["account"] == "work"
        assert result["mime_type"] == "application/pdf"


# ---------------------------------------------------------------------------
# _make_slug import path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Help format — standard Usage: header
# ---------------------------------------------------------------------------


# All CLI modules with register() functions
ALL_CLI_MODULES = [
    "footprinter.cli.view",
    "footprinter.cli.delete",
    "footprinter.cli.connect",
    "footprinter.cli.doctor",
    "footprinter.cli.ingest",
    "footprinter.cli.setup",
    "footprinter.cli.search",
    "footprinter.cli.status",
    "footprinter.cli.update",
    "footprinter.cli.mcp_cmd",
    "footprinter.cli.uninstall",
    "footprinter.cli.permission_cmd",
]


class TestUsageFormat:
    @pytest.mark.parametrize("module_path", ALL_CLI_MODULES)
    def test_help_starts_with_usage_prefix(self, module_path):
        """Every CLI module's help output starts with Usage: and not HELP MENU:."""
        import importlib

        mod = importlib.import_module(module_path)
        root = argparse.ArgumentParser(prog="fp")
        subs = root.add_subparsers()
        mod.register(subs)

        # Get the registered subparser
        entity_name = module_path.rsplit(".", 1)[-1]
        # Normalize _cmd suffix (e.g., status_cmd -> status)
        cmd_name = entity_name.removesuffix("_cmd")

        entity_parser = subs.choices[cmd_name]
        help_text = entity_parser.format_help()

        assert "Usage:" in help_text, f"{module_path} help missing 'Usage:' header:\n{help_text[:200]}"
        assert "HELP MENU:" not in help_text, (
            f"{module_path} help still contains 'HELP MENU:' header:\n{help_text[:200]}"
        )

    def test_root_parser_usage_format(self):
        """The root 'fp' parser help starts with Usage: fp."""
        from footprinter.cli._common import FORMATTER

        parser = argparse.ArgumentParser(
            prog="fp",
            description="test",
            formatter_class=FORMATTER,
        )
        help_text = parser.format_help()

        assert "Usage: fp" in help_text, f"Root parser help missing 'Usage: fp':\n{help_text[:200]}"
        assert "HELP MENU:" not in help_text, f"Root parser help still contains 'HELP MENU:':\n{help_text[:200]}"


# ---------------------------------------------------------------------------
# Verb subparser title — "commands (one required)"
# ---------------------------------------------------------------------------


# Modules with verb-level subparsers that should have titled groups
VERB_MODULES = [
    "footprinter.cli.view",
    "footprinter.cli.delete",
    "footprinter.cli.connect",
    "footprinter.cli.setup",
    "footprinter.cli.mcp_cmd",
    "footprinter.cli.ingest",
    "footprinter.cli.permission_cmd",
]


class TestVerbSubparserTitle:
    @pytest.mark.parametrize("module_path", VERB_MODULES)
    def test_verb_subparsers_have_required_title(self, module_path):
        """All verb-level subparsers must have title containing '(one required)'."""
        import importlib

        mod = importlib.import_module(module_path)
        root = argparse.ArgumentParser(prog="fp")
        subs = root.add_subparsers()
        mod.register(subs)

        entity_name = module_path.rsplit(".", 1)[-1]
        cmd_name = entity_name.removesuffix("_cmd")
        entity_parser = subs.choices[cmd_name]

        # Collect ALL _SubParsersAction instances recursively
        found = _collect_subparser_actions(entity_parser)
        assert found, f"{module_path} has no sub-subparsers"

        for action, path in found:
            title = getattr(action.container, "title", None)
            assert title and "(one required)" in title, (
                f"{module_path} subparser at {path} has title={title!r}, expected title containing '(one required)'"
            )


def _collect_subparser_actions(
    parser: argparse.ArgumentParser,
    path: str = "",
) -> list[tuple[argparse._SubParsersAction, str]]:
    """Recursively collect all _SubParsersAction instances from a parser tree."""
    results: list[tuple[argparse._SubParsersAction, str]] = []
    if parser._subparsers is None:
        return results
    for action in parser._subparsers._actions:
        if isinstance(action, argparse._SubParsersAction):
            current = f"{path}/{action.dest}" if path else action.dest
            results.append((action, current))
            # Recurse into each sub-parser's choices
            for name, subparser in action.choices.items():
                results.extend(_collect_subparser_actions(subparser, f"{current}/{name}"))
    return results


# ---------------------------------------------------------------------------
# _make_slug import path
# ---------------------------------------------------------------------------


class TestMakeSlugImportPath:
    def test_make_slug_not_in_common(self):
        """_make_slug must NOT be importable from footprinter.cli._common."""
        import footprinter.cli._common as common

        assert not hasattr(common, "_make_slug"), "_make_slug should not be re-exported from _common"

    def test_make_slug_not_in_db_init(self):
        """_make_slug must NOT be importable from footprinter.db."""
        import footprinter.db as db_pkg

        assert not hasattr(db_pkg, "_make_slug"), "_make_slug should not be re-exported from db.__init__"
