"""
Tests for the CLI module extracted from orchestrator.

Verifies that cli.py exposes the expected functions and that
backward-compat scaffolding has been removed.
"""

import importlib
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest


class TestCliModuleExports:
    """cli.py should only export _rebuild_vectors."""

    def test_cli_module_exports_surviving(self):
        """_rebuild_vectors should be importable and callable."""
        from footprinter.ingest.cli import _rebuild_vectors

        assert callable(_rebuild_vectors)

    def test_dispatch_refresh_removed(self):
        """_dispatch_refresh should no longer exist in cli.py."""
        import importlib

        cli = importlib.import_module("footprinter.ingest.cli")
        assert not hasattr(cli, "_dispatch_refresh"), (
            "_dispatch_refresh still exists in cli.py — should be removed (dead code)"
        )

    def test_removed_exports_not_importable(self):
        """_dispatch_retention, _dispatch_chat, and main should not exist in cli.py."""
        import importlib

        cli = importlib.import_module("footprinter.ingest.cli")
        assert not hasattr(cli, "_dispatch_retention"), "_dispatch_retention still exists in cli.py"
        assert not hasattr(cli, "_dispatch_chat"), "_dispatch_chat still exists in cli.py"
        assert not hasattr(cli, "main"), "main still exists in cli.py"


class TestOrchestratorNoReexports:
    """orchestrator.py should NOT re-export cli names (scaffolding removed)."""

    def test_orchestrator_does_not_reexport_rebuild_vectors(self):
        """_rebuild_vectors should NOT be importable from orchestrator."""
        import importlib

        orch = importlib.import_module("footprinter.ingest.orchestrator")
        assert not hasattr(orch, "_rebuild_vectors"), (
            "orchestrator still re-exports _rebuild_vectors — remove __getattr__"
        )

    def test_orchestrator_does_not_reexport_dispatch_refresh(self):
        """_dispatch_refresh should NOT be importable from orchestrator."""
        import importlib

        orch = importlib.import_module("footprinter.ingest.orchestrator")
        assert not hasattr(orch, "_dispatch_refresh"), (
            "orchestrator still re-exports _dispatch_refresh — remove __getattr__"
        )


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent.parent / "scripts" / "setup" / "setup_google_auth.py").exists(),
    reason="setup_google_auth.py not present (snapshot)",
)
class TestSetupGoogleAuthNoReexports:
    """setup_google_auth.py should NOT re-export scope constants (scaffolding removed)."""

    REMOVED_NAMES = [
        "SCOPES_DRIVE_FULL",
        "SCOPES_DRIVE_READONLY",
        "SCOPES_DRIVE_WRITE",
        "SCOPES_FULL",
        "SCOPES_GMAIL_READONLY",
        "SCOPES_WRITE",
    ]

    @staticmethod
    def _load_script():
        """Load setup_google_auth, ensuring Google API stubs are in place."""
        for name in (
            "google",
            "google.auth",
            "google.auth.transport",
            "google.auth.transport.requests",
            "google.oauth2",
            "google.oauth2.credentials",
            "google_auth_oauthlib",
            "google_auth_oauthlib.flow",
            "googleapiclient",
            "googleapiclient.discovery",
        ):
            if name not in sys.modules:
                sys.modules[name] = ModuleType(name)
        sys.modules["google.auth.transport.requests"].Request = MagicMock  # type: ignore[attr-defined]
        sys.modules["google.oauth2.credentials"].Credentials = MagicMock  # type: ignore[attr-defined]
        sys.modules["google_auth_oauthlib.flow"].InstalledAppFlow = MagicMock  # type: ignore[attr-defined]
        sys.modules["googleapiclient.discovery"].build = MagicMock  # type: ignore[attr-defined]

        if "setup_google_auth" not in sys.modules:
            script_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts" / "setup")
            sys.path.insert(0, script_dir)
            try:
                importlib.import_module("setup_google_auth")
            finally:
                sys.path.pop(0)
        return sys.modules["setup_google_auth"]

    @pytest.mark.parametrize("name", REMOVED_NAMES)
    def test_no_reexport(self, name):
        mod = self._load_script()
        assert not hasattr(mod, name), f"setup_google_auth still re-exports {name} — remove from import block"


class TestNoRetentionRefsInCli:
    """cli.py must not reference non-shipped retention modules."""

    def test_no_retention_module_references(self):
        """Source of cli.py should not reference retention_classifier, retention_manager,
        purge_executor, scoring, or retention_reporter."""
        from pathlib import Path

        cli_source = Path("footprinter/ingest/cli.py").read_text()
        forbidden = [
            "retention_classifier",
            "retention_manager",
            "purge_executor",
            "scoring",
            "retention_reporter",
        ]
        for name in forbidden:
            assert name not in cli_source, f"cli.py still references '{name}'"


class TestDbProjectsExports:
    """db.projects should export list_project_files for CLI use."""

    def test_list_project_files_importable(self):
        """list_project_files should be importable from footprinter.db.projects."""
        from footprinter.db.projects import list_project_files

        assert callable(list_project_files)


class TestStatusNoRetentionDisplay:
    """get_data_counts should not return classification keys."""

    def test_no_classification_keys(self, temp_db):
        """get_data_counts should not have 'classifications' or 'classifications_v2' keys."""
        from pathlib import Path

        from footprinter.cli.status import get_data_counts

        counts = get_data_counts(Path(temp_db))
        assert "classifications" not in counts, "get_data_counts still returns 'classifications'"
        assert "classifications_v2" not in counts, "get_data_counts still returns 'classifications_v2'"


class TestDeadCodeRemoved:
    """Verify dead code has been cleaned up."""

    def test_setup_cmd_not_importable(self):
        """setup_cmd.py should no longer exist as a module."""
        import importlib

        import pytest

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("footprinter.cli.setup_cmd")

    def test_common_imports_clean(self):
        """_common.py should import without errors after removing unused re."""
        from footprinter.cli._common import connect_db, console, output_json  # noqa: F401

    def test_status_cmd_imports_clean(self):
        """status_cmd.py should import without errors after removing unused sqlite3."""
        from footprinter.cli.status_cmd import register  # noqa: F401

    def test_queries_project_imports_clean(self):
        """queries/project.py should import without errors after removing unused sqlite3."""
        from footprinter.db.projects import list_projects  # noqa: F401

    def test_queries_client_imports_clean(self):
        """queries/client.py should import without errors after removing shadowed constant."""
        from footprinter.db.clients import update_client  # noqa: F401

    def test_policy_helpers_imports_clean(self):
        """_policy_helpers.py should import without errors after docstring update."""
        from footprinter.cli._policy_helpers import get_policy_db  # noqa: F401


class TestBackwardCompatScaffoldingRemoved:
    """Verify all backward-compat scaffolding has been stripped."""

    def test_file_scanner_no_deprecated_properties(self):
        """FileScanner should not have deprecated property aliases."""
        from footprinter.ingest.file_scanner import FileScanner

        assert not hasattr(FileScanner, "exclusion_patterns"), (
            "FileScanner still has deprecated 'exclusion_patterns' property"
        )
        assert not hasattr(FileScanner, "system_exclusions"), (
            "FileScanner still has deprecated 'system_exclusions' property"
        )
        assert not hasattr(FileScanner, "client_exclusions"), (
            "FileScanner still has deprecated 'client_exclusions' property"
        )

    def test_file_scanner_no_calculate_hash(self):
        """FileScanner should not have dead _calculate_hash method."""
        from footprinter.ingest.file_scanner import FileScanner

        assert not hasattr(FileScanner, "_calculate_hash"), "FileScanner still has dead _calculate_hash method"

    def test_mcp_setup_no_main(self):
        """mcp_setup should not have dead main() entry point."""
        import importlib

        mod = importlib.import_module("footprinter.cli.mcp_setup")
        assert not hasattr(mod, "main"), "mcp_setup still has dead main() for removed fp-setup-claude"

    def test_chat_indexer_no_legacy_methods(self):
        """ChatIndexer should not have legacy import_claude/import_chatgpt methods."""
        from footprinter.ingest.chat_indexer import ChatIndexer

        assert not hasattr(ChatIndexer, "import_claude"), "ChatIndexer still has legacy import_claude method"
        assert not hasattr(ChatIndexer, "import_chatgpt"), "ChatIndexer still has legacy import_chatgpt method"

    def test_no_code_project_alias(self):
        """project_detector should not have CodeProject backward-compat alias."""
        mod = pytest.importorskip("footprinter.analysis.project_detector")
        assert not hasattr(mod, "CodeProject"), "project_detector still has CodeProject = Project alias"

    def test_no_code_project_alias_in_project_detection(self):
        """project_detection should not have CodeProject backward-compat alias."""
        mod = pytest.importorskip("footprinter.ingest.project_detection")
        assert not hasattr(mod, "CodeProject"), "project_detection still has CodeProject = Project alias"

    def test_db_init_no_database_reexport(self):
        """footprinter.ingest.db should not re-export Database via __getattr__."""
        import importlib

        mod = importlib.import_module("footprinter.ingest.db")
        assert not hasattr(mod, "Database"), "footprinter.ingest.db still re-exports Database via __getattr__"

    def test_queries_package_removed(self):
        """cli/queries/ package has been dissolved — imports should fail."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("footprinter.cli.queries")
