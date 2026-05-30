"""
End-to-end tests for the Footprinter install-to-value journey.

Simulates a new user running:
    pip install -e . → fp setup → fp ingest → fp setup mcp.
All tests use isolated temp directories so they don't touch real config.

Run:
    ./venv/bin/python3 -m pytest tests/test_e2e_install.py -v --tb=short
"""

import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from rich.console import Console

# ── Project root (needed for config.example.yaml and other fixtures) ──

PROJECT_ROOT = Path(__file__).parent.parent


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Entry points resolve
# ═══════════════════════════════════════════════════════════════════════


class TestEntryPointsResolve:
    """Verify single fp entry point is importable and lists subcommands."""

    def test_fp_entry_point_importable(self):
        """footprinter.cli.main should be importable and callable."""
        from footprinter.cli import main

        assert callable(main), "footprinter.cli:main is not callable"

    def test_fp_help_lists_subcommands(self):
        """fp --help should list key subcommands."""
        from conftest import run_fp

        stdout, stderr, code = run_fp("--help")
        assert code == 0
        output = stdout + stderr
        for sub in ["ingest", "search", "status", "setup", "mcp"]:
            assert sub in output, f"'{sub}' not in fp --help output"


LEGACY_ENTRY_POINTS = [
    "fp-setup",
    "fp-orchestrator",
    "fp-status",
    "fp-search",
    "fp-mcp",
    "fp-dashboard",
    "fp-app",
    "fp-chat",
    "fp-analyze",
    "fp-setup-claude",
]


class TestLegacyEntryPointsAbsent:
    """All 10 legacy entry points must not exist in pyproject.toml scripts."""

    @pytest.fixture()
    def scripts(self):
        import tomllib

        toml_text = (PROJECT_ROOT / "pyproject.toml").read_text()
        data = tomllib.loads(toml_text)
        return data.get("project", {}).get("scripts", {})

    @pytest.mark.parametrize("name", LEGACY_ENTRY_POINTS)
    def test_legacy_entry_point_absent(self, scripts, name):
        assert name not in scripts, f"Legacy entry point '{name}' should not exist in [project.scripts]"


class TestSemanticExtraPin:
    """The [semantic] extra must pin chromadb to the 1.x line.

    The 0.x line can't read stores written by 1.x (KeyError on `_type`)
    and breaks posthog 7.x's capture() signature.
    """

    @pytest.fixture()
    def semantic_extra(self):
        import tomllib

        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
        return data["project"]["optional-dependencies"]["semantic"]

    def test_chromadb_pin_is_1x(self, semantic_extra):
        chroma_pin = next((p for p in semantic_extra if p.startswith("chromadb")), None)
        assert chroma_pin is not None, "chromadb missing from [semantic] extra"
        assert ">=1." in chroma_pin, (
            f"chromadb pin must require >=1.x (got {chroma_pin!r}); 0.x cannot read 1.x stores"
        )
        assert "<1.0" not in chroma_pin, f"chromadb upper bound still excludes 1.x: {chroma_pin!r}"


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: fp setup wizard — config generation
# ═══════════════════════════════════════════════════════════════════════


class TestSetupWizardE2E:
    """End-to-end tests for the setup wizard's config pipeline."""

    def _make_workspace(self, tmp_path):
        """Create a minimal workspace mimicking a fresh clone."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Copy real config.example.yaml from its single-source-of-truth home
        # at footprinter/bundled/.
        shutil.copy2(
            PROJECT_ROOT / "footprinter" / "bundled" / "config.example.yaml",
            config_dir / "config.example.yaml",
        )
        # Create scannable directories
        (tmp_path / "Work").mkdir()
        (tmp_path / "Personal").mkdir()
        (tmp_path / "Work" / "project-a").mkdir()
        (tmp_path / "Work" / "project-a" / "README.md").write_text("# Project A")
        return tmp_path

    def test_generate_config_from_answers(self, tmp_path):
        """Full pipeline: answers → generate_config → write → validate."""
        from footprinter.cli.diagnostics import validate_config
        from footprinter.cli.setup import generate_config, write_config

        workspace = self._make_workspace(tmp_path)

        answers = {
            "directories": [str(workspace / "Work"), str(workspace / "Personal")],
            "browsers": ["safari"],
        }

        config = generate_config(answers)

        # Write to disk
        config_path = workspace / "config" / "config.yaml"
        write_config(config, path=config_path)
        assert config_path.exists()

        # Read back and validate
        with open(config_path) as f:
            loaded = yaml.safe_load(f)

        assert loaded["directories"] == answers["directories"]
        assert loaded["browsers"] == ["safari"]

        # Validate should pass (directories exist)
        errors, _ = validate_config(loaded)
        assert errors == [], f"Validation errors: {errors}"

    def test_preview_config_output(self):
        """preview_config should show directories and browsers."""
        from footprinter.cli.setup import preview_config

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        answers = {
            "directories": ["~/Work", "~/Personal"],
            "browsers": ["safari", "chrome"],
        }
        preview_config(answers, console=console)
        output = buf.getvalue()

        assert "~/Work" in output
        assert "~/Personal" in output
        assert "safari" in output
        assert "chrome" in output

    def test_doctor_validates_written_config(self, tmp_path, monkeypatch):
        """fp doctor should validate a config written by fp setup."""
        from footprinter.cli.diagnostics import validate_config
        from footprinter.cli.setup import generate_config, write_config

        workspace = self._make_workspace(tmp_path)
        config_path = workspace / "config" / "config.yaml"

        answers = {
            "directories": [str(workspace / "Work")],
            "browsers": ["safari"],
        }
        config = generate_config(answers)
        write_config(config, path=config_path)

        with open(config_path) as f:
            loaded = yaml.safe_load(f)

        errors, _ = validate_config(loaded)
        assert errors == [], f"Validation errors: {errors}"


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: fp ingest — pipeline and status
# ═══════════════════════════════════════════════════════════════════════


class TestOrchestratorE2E:
    """End-to-end tests for orchestrator output formatting and status."""

    def test_print_results_all_statuses(self):
        """print_results should handle completed, warn, error, info, and skipped."""
        from footprinter.ingest.status import print_results

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)

        results = [
            {
                "stage": "local_folders",
                "status": "completed",
                "elapsed_seconds": 1.0,
                "folders_found": 25,
                "inserted": 10,
                "updated": 5,
            },
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 3.5,
                "files_indexed": 500,
            },
            {
                "stage": "browser",
                "status": "completed_with_errors",
                "elapsed_seconds": 0.8,
                "urls_indexed": 200,
            },
            {
                "stage": "gmail",
                "status": "error",
                "elapsed_seconds": 0.1,
                "error": "OAuth token expired for user@example.com",
            },
            {"stage": "chat", "status": "info", "elapsed_seconds": 0.0},
            {"stage": "drive_folders", "status": "skipped", "elapsed_seconds": 0.0},
        ]

        print_results(results, console=console)
        output = buf.getvalue()

        assert "local_folders" in output
        assert "local_files" in output
        assert "OK" in output
        assert "WARN" in output
        assert "FAIL" in output
        assert "info" in output
        assert "Pipeline" in output

    def test_quiet_mode_suppresses_everything(self):
        """With quiet=True, print_results emits nothing."""
        from footprinter.ingest.status import print_results

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)

        print_results(
            [{"stage": "test", "status": "completed", "elapsed_seconds": 1.0}],
            quiet=True,
            console=console,
        )
        assert buf.getvalue() == ""

    def test_error_messages_preserve_context(self):
        """Error messages longer than 60 chars should not be truncated away."""
        from footprinter.ingest.status import print_results

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=300)

        long_error = (
            "PermissionError: [Errno 13] Permission denied: "
            "'/Users/username/Work/client-project/src/auth/OAuthHandler.py'"
        )
        results = [
            {
                "stage": "local_files",
                "status": "error",
                "elapsed_seconds": 0.1,
                "error": long_error,
            },
        ]
        print_results(results, console=console)
        output = buf.getvalue()
        assert "OAuthHandler.py" in output

    def test_completion_summary_correct_counts(self):
        """Completion summary should count stages accurately."""
        from footprinter.ingest.status import _print_completion_summary

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)

        results = [
            {"stage": "a", "status": "completed", "elapsed_seconds": 2.0},
            {"stage": "b", "status": "completed_with_errors", "elapsed_seconds": 1.0},
            {"stage": "c", "status": "error", "elapsed_seconds": 0.5, "error": "fail"},
        ]
        _print_completion_summary(console, results)
        output = buf.getvalue()

        # 2 completed (a + b count as completed), 1 error
        assert "1 error" in output
        assert "2 OK" in output or "2 stages" in output or "1 warning" in output


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: MCP configuration (fp setup mcp)
# ═══════════════════════════════════════════════════════════════════════


class TestMCPSetupE2E:
    """End-to-end tests for the MCP config helper."""

    def test_full_write_cycle(self, tmp_path):
        """generate → write → read-back should succeed end-to-end."""
        from footprinter.cli.mcp_setup import (
            generate_snippet,
            has_footprinter_entry,
            write_config,
        )

        config_path = tmp_path / "claude_desktop_config.json"

        # Generate snippet
        snippet = generate_snippet()
        assert "mcpServers" in snippet
        assert "footprinter" in snippet["mcpServers"]

        # Write
        ok = write_config(snippet, config_path=config_path)
        assert ok is True
        assert config_path.exists()

        # Read back and verify valid JSON with footprinter entry
        config = json.loads(config_path.read_text())
        assert "footprinter" in config["mcpServers"]
        assert has_footprinter_entry(config)

    def test_merge_preserves_existing_servers(self, tmp_path):
        """Writing footprinter config should not clobber other MCP servers."""
        from footprinter.cli.mcp_setup import generate_snippet, write_config

        config_path = tmp_path / "claude_desktop_config.json"
        existing = {
            "mcpServers": {
                "other-tool": {"command": "/usr/bin/other", "args": ["--serve"]},
            },
            "theme": "dark",
        }
        config_path.write_text(json.dumps(existing))

        snippet = generate_snippet()
        write_config(snippet, config_path=config_path)

        config = json.loads(config_path.read_text())
        assert "other-tool" in config["mcpServers"]
        assert "footprinter" in config["mcpServers"]
        assert config["theme"] == "dark"

    def test_backup_created_on_write(self, tmp_path):
        """Writing to existing config should create a .backup_ file."""
        from footprinter.cli.mcp_setup import generate_snippet, write_config

        config_path = tmp_path / "claude_desktop_config.json"
        config_path.write_text(json.dumps({"mcpServers": {}}))

        write_config(generate_snippet(), config_path=config_path)
        backups = list(tmp_path.glob("*.backup_*.json"))
        assert len(backups) >= 1

    def test_snippet_uses_sys_executable(self):
        """MCP snippet command should be sys.executable when no run_mcp.sh exists."""
        from unittest.mock import patch as _patch

        from footprinter.cli.mcp_setup import generate_snippet

        # Use a tmp_path with no run_mcp.sh, and mock fp-mcp not on PATH
        with (
            tempfile.TemporaryDirectory() as tmp,
            _patch("footprinter.cli.mcp_setup.shutil.which", return_value=None),
        ):
            snippet = generate_snippet(project_root=Path(tmp))

        command = snippet["mcpServers"]["footprinter"]["command"]
        assert command == sys.executable

    def test_has_footprinter_entry_false_for_missing(self, tmp_path):
        """has_footprinter_entry returns False when footprinter is not configured."""
        from footprinter.cli.mcp_setup import has_footprinter_entry

        config = {"mcpServers": {"other": {}}}
        assert has_footprinter_entry(config) is False


# ═══════════════════════════════════════════════════════════════════════
# Phase 6: Database schema — init_db creates expected tables
# ═══════════════════════════════════════════════════════════════════════


class TestDatabaseSchemaE2E:
    """Verify init_db creates the expected tables in a fresh database."""

    EXPECTED_TABLES = [
        "files",
        "folders",
        "visits",
        "projects",
        "emails",
        "chats",
        "messages",
    ]

    def test_init_db_creates_tables(self, tmp_path):
        """A fresh Database should create all expected tables."""
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        db.conn.close()

        for table in self.EXPECTED_TABLES:
            assert table in tables, f"Missing table: {table}"

    def test_files_has_indexed_at(self, tmp_path):
        """files table should have indexed_at column (needed by /api/status)."""
        from footprinter.ingest.database import Database

        db = Database(str(tmp_path / "test.db"))
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(files)")
        columns = {row[1] for row in cursor.fetchall()}
        db.conn.close()

        assert "indexed_at" in columns


# ═══════════════════════════════════════════════════════════════════════
# Phase 7: Security model — safe defaults
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityDefaults:
    """Verify the security model has safe defaults for new installs."""

    def test_visibility_module_importable(self):
        """Visibility module should import and have core functions."""
        import footprinter.visibility

        assert hasattr(footprinter.visibility, "get_visibility")
        assert hasattr(footprinter.visibility, "batch_resolve_visibility")

    def test_permissions_module_importable(self):
        """Permissions module should import and have core functions."""
        import footprinter.permissions

        assert hasattr(footprinter.permissions, "can_read")
        assert hasattr(footprinter.permissions, "batch_resolve_permissions")


# ═══════════════════════════════════════════════════════════════════════
# Phase 8: MCP server — tools registered
# ═══════════════════════════════════════════════════════════════════════


class TestMCPServerE2E:
    """Verify MCP server module can be imported and has expected tools."""

    def test_mcp_server_importable(self):
        """MCP server module should import and expose main()."""
        from footprinter.mcp import server

        assert hasattr(server, "main")

    def test_mcp_tools_directory_exists(self):
        """MCP tools/ directory should contain tool modules."""
        tools_dir = PROJECT_ROOT / "footprinter" / "mcp" / "tools"
        assert tools_dir.is_dir()
        tool_files = list(tools_dir.glob("*.py"))
        # Should have multiple tool modules
        assert len(tool_files) >= 3, f"Expected 3+ tool files, found {len(tool_files)}"


# ═══════════════════════════════════════════════════════════════════════
# Phase 9: No hardcoded personal data (acceptance test)
# ═══════════════════════════════════════════════════════════════════════


class TestNoPersonalData:
    """Verify no personal data leaked into tracked files."""

    def test_config_example_has_no_real_paths(self):
        """config.example.yaml should not contain real user home directories."""
        import re

        config_example = PROJECT_ROOT / "footprinter" / "bundled" / "config.example.yaml"
        content = config_example.read_text()
        # Regex exclusion patterns like /Users/[^/]+/ are fine — check for
        # literal usernames (e.g., /Users/john/) which would leak real paths.
        real_user_paths = re.findall(r"/Users/[a-zA-Z]\w+/", content)
        assert not real_user_paths, f"Real user paths found: {real_user_paths}"
        real_home_paths = re.findall(r"/home/[a-zA-Z]\w+/", content)
        assert not real_home_paths, f"Real home paths found: {real_home_paths}"


# ═══════════════════════════════════════════════════════════════════════
# Phase 10: Cross-module integration — setup → orchestrator
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# Phase 10a: Dependency checking
# ═══════════════════════════════════════════════════════════════════════


class TestDependencyCheck:
    """Verify diagnostic dependency verification."""

    def test_check_core_deps_returns_list(self):
        from footprinter.cli.diagnostics import check_core_deps

        results = check_core_deps()
        assert isinstance(results, list)
        assert len(results) >= 2
        for name, available in results:
            assert isinstance(name, str)
            assert isinstance(available, bool)

    def test_core_deps_always_available(self):
        from footprinter.cli.diagnostics import check_core_deps

        results = check_core_deps()
        for name, available in results:
            assert available, f"Core dep {name} should be available"

    def test_check_optional_features_returns_list(self):
        from footprinter.cli.diagnostics import check_optional_features

        results = check_optional_features({})
        assert isinstance(results, list)
        assert len(results) >= 1
        for name, installed, enabled, hint in results:
            assert isinstance(name, str)
            assert isinstance(installed, bool)


# ═══════════════════════════════════════════════════════════════════════
# Phase 10b: Orchestrator ImportError handling
# ═══════════════════════════════════════════════════════════════════════


class TestOrchestratorImportErrorHandling:
    """Verify stages with missing deps are skipped, not errored."""

    def test_orchestrator_skips_stage_on_import_error(self):
        """Stages with missing deps should be skipped, not error.

        Adapters handle missing deps internally (checking _GOOGLE_AVAILABLE)
        and return PipeResult.skipped() rather than raising ImportError.
        The orchestrator's outer try/except is a safety net for unexpected
        ImportErrors (e.g., adapter construction failure).
        """
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator

        orch = DataPipelineOrchestrator()
        # Simulate an unexpected ImportError escaping adapter dispatch
        # by injecting a failing adapter into the runner's registry.
        mock_cls = MagicMock(side_effect=ImportError("test"))
        orch.runner.adapter_registry["gmail"] = mock_cls
        result = orch.run_pipe("gmail")
        assert result["status"] == "skipped"
        assert result.get("error_type") == "missing_dependency"


# ═══════════════════════════════════════════════════════════════════════
# Phase 10c: Client seeding removed from wizard
# ═══════════════════════════════════════════════════════════════════════


class TestClientSeedingRemoved:
    """Verify offer_seed_clients was removed from setup wizard."""

    def test_offer_seed_clients_not_in_module(self):
        """offer_seed_clients should no longer exist in setup module."""
        import footprinter.cli.setup as setup_mod

        assert not hasattr(setup_mod, "offer_seed_clients")


class TestCrossModuleIntegration:
    """Verify modules can talk to each other correctly."""

    def test_setup_run_orchestrator_uses_correct_args(self):
        """run_orchestrator should pass correct stages to in-process pipeline."""
        from footprinter.cli.setup import run_orchestrator

        with patch("footprinter.cli.setup._run_orchestrator_stages") as mock_stages:
            run_orchestrator({"browsers": ["safari"]})

            mock_stages.assert_called_once()
            stages = mock_stages.call_args[0][0]
            assert "local_folders" in stages
            assert "local_files" in stages
            assert "browser" in stages
