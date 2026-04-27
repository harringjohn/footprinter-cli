"""
Tests for the interactive setup wizard (src.cli.setup).
"""

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from rich.panel import Panel

_GOOGLE_CONNECTOR = importlib.util.find_spec("footprinter.connectors.google") is not None
_requires_google = pytest.mark.skipif(
    not _GOOGLE_CONNECTOR,
    reason="Google connector not installed",
)
_requires_darwin = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Full Disk Access / Safari is a macOS-only prerequisite",
)

from footprinter.cli.setup import (
    SAFARI_FDA_URL,
    _check_semantic_deps,
    check_existing_config,
    collect_answers,
    collect_chat_export_path,
    generate_config,
    import_chat_export,
    main,
    offer_setup_claude,
    preview_config,
    print_summary,
    run_interactive_wizard,
    run_orchestrator,
    validate_config,
    write_config,
)
from footprinter.paths import get_bundled_path


def _extract_printed_text(mock_console) -> str:
    """Extract all human-readable text from a mock console's print calls.

    Handles plain strings, Rich Rule (via .title), and Rich Panel
    (via .renderable and .title).
    """
    from rich.panel import Panel
    from rich.rule import Rule

    parts = []
    for call_obj in mock_console.print.call_args_list:
        for arg in call_obj[0]:  # positional args
            if isinstance(arg, str):
                parts.append(arg)
            elif isinstance(arg, Rule):
                if arg.title:
                    parts.append(str(arg.title))
            elif isinstance(arg, Panel):
                parts.append(str(arg.renderable))
                if arg.title:
                    parts.append(str(arg.title))
            else:
                parts.append(str(arg))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# TestConfigGeneration — 12 tests
# ---------------------------------------------------------------------------
class TestConfigGeneration:
    """Tests for generate_config()."""

    def _example_config(self):
        """Load the real config.example.yaml for reference."""
        with open(get_bundled_path("config.example.yaml"), "r") as f:
            return yaml.safe_load(f)

    def test_returns_dict(self):
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert isinstance(result, dict)

    def test_applies_directories(self):
        answers = {"directories": ["~/Work", "~/Personal"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert result["directories"] == ["~/Work", "~/Personal"]

    def test_applies_browsers(self):
        answers = {"directories": ["~/Work"], "browsers": ["chrome"]}
        result = generate_config(answers)
        assert result["browsers"] == ["chrome"]

    def test_single_directory(self):
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert result["directories"] == ["~/Work"]

    def test_single_browser(self):
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert result["browsers"] == ["safari"]

    def test_both_browsers(self):
        answers = {"directories": ["~/Work"], "browsers": ["safari", "chrome"]}
        result = generate_config(answers)
        assert result["browsers"] == ["safari", "chrome"]

    def test_includes_claude_directory(self):
        answers = {"directories": ["~/Work", "~/.claude"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert "~/.claude" in result["directories"]

    def test_preserves_exclusions(self):
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert "exclusions" in result

    def test_no_claude_section(self):
        """claude section removed — CLI uses ANTHROPIC_API_KEY env var."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers)
        assert "claude" not in result

    def test_empty_directories_returns_empty(self):
        answers = {"directories": [], "browsers": ["safari"]}
        result = generate_config(answers)
        # No fallback to hardcoded defaults — wizard enforces at-least-one
        assert result["directories"] == []

    def test_empty_browsers_uses_default(self):
        answers = {"directories": ["~/Work"], "browsers": []}
        result = generate_config(answers)
        assert result["browsers"] == []


# ---------------------------------------------------------------------------
# TestConfigValidation — 8 tests
# ---------------------------------------------------------------------------
class TestConfigValidation:
    """Tests for validate_config()."""

    def _valid_config(self, tmp_path):
        """Return a minimal valid config with real directories."""
        d = tmp_path / "scandir"
        d.mkdir()
        return {
            "directories": [str(d)],
            "browsers": ["safari"],
        }

    def test_valid_config_no_errors(self, tmp_path):
        config = self._valid_config(tmp_path)
        errors, warnings = validate_config(config)
        assert errors == []

    def test_none_config(self):
        errors, _ = validate_config(None)
        assert len(errors) == 1
        assert "empty" in errors[0].lower()

    def test_missing_directories(self):
        errors, _ = validate_config({"browsers": ["safari"]})
        assert any("directories" in e for e in errors)

    def test_empty_directories(self):
        errors, _ = validate_config({"directories": [], "browsers": ["safari"]})
        assert any("directories" in e for e in errors)

    def test_directories_not_list(self):
        errors, _ = validate_config({"directories": "~/Work", "browsers": ["safari"]})
        assert any("list" in e for e in errors)

    def test_missing_browsers(self):
        errors, _ = validate_config({"directories": ["/tmp"], "browsers": []})
        assert not any("browsers" in e for e in errors)

    def test_unknown_browser(self, tmp_path):
        d = tmp_path / "scandir"
        d.mkdir()
        errors, _ = validate_config({"directories": [str(d)], "browsers": ["opera"]})
        assert any("opera" in e.lower() for e in errors)

    def test_nonexistent_directory(self):
        # Absent directories are a warning, not an error — a Linux user
        # following the bundled example config would otherwise be rejected
        # at `fp setup --check` for dirs that don't exist on their box.
        errors, warnings = validate_config(
            {"directories": ["/nonexistent/path/abc123"], "browsers": ["safari"]}
        )
        assert errors == []
        assert any("not found" in w.lower() for w in warnings)
        assert any("/nonexistent/path/abc123" in w for w in warnings)


# ---------------------------------------------------------------------------
# TestConfigWrite — 3 tests
# ---------------------------------------------------------------------------
class TestConfigWrite:
    """Tests for write_config()."""

    def test_creates_yaml_file(self, tmp_path):
        target = tmp_path / "config.yaml"
        config = {"directories": ["~/Work"], "browsers": ["safari"]}
        write_config(config, path=target)
        assert target.exists()

    def test_written_yaml_is_valid(self, tmp_path):
        target = tmp_path / "config.yaml"
        config = {"directories": ["~/Work"], "browsers": ["safari"]}
        write_config(config, path=target)
        with open(target) as f:
            loaded = yaml.safe_load(f)
        assert loaded["directories"] == ["~/Work"]

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "subdir" / "deep" / "config.yaml"
        config = {"directories": ["~/Work"], "browsers": ["safari"]}
        write_config(config, path=target)
        assert target.exists()


# ---------------------------------------------------------------------------
# TestCheckExistingConfig — 3 tests
# ---------------------------------------------------------------------------
class TestCheckExistingConfig:
    """Tests for check_existing_config()."""

    def test_returns_1_when_no_config(self):
        from footprinter.source_registry import ConfigError

        with patch("footprinter.cli.setup.get_config", side_effect=ConfigError("missing")):
            assert check_existing_config() == 1

    def test_returns_1_for_invalid_config(self, tmp_path):
        bad_config = tmp_path / "config.yaml"
        bad_config.write_text(yaml.dump({"directories": []}))
        with patch("footprinter.cli.setup.get_config", return_value={"directories": []}):
            assert check_existing_config() == 1

    def test_returns_0_for_valid_config(self, tmp_path):
        d = tmp_path / "scandir"
        d.mkdir()
        good_config = tmp_path / "config.yaml"
        good_config.write_text(yaml.dump({"directories": [str(d)], "browsers": ["safari"]}))
        with patch("footprinter.cli.setup.get_config", return_value={"directories": [str(d)], "browsers": ["safari"]}):
            assert check_existing_config() == 0


# ---------------------------------------------------------------------------
# TestRunOrchestratorCommand — 2 tests
# ---------------------------------------------------------------------------
class TestRunOrchestratorCommand:
    """Tests for run_orchestrator() stage assembly (in-process)."""

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_orchestrator_passes_correct_stages(self, mock_console, mock_orch, mock_rwl):
        run_orchestrator({"browsers": []})
        call_kwargs = mock_rwl.call_args[1]
        assert call_kwargs["pipes"] == ["local_folders", "local_files"]

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_orchestrator_includes_browser_when_selected(self, mock_console, mock_orch, mock_rwl):
        run_orchestrator({"browsers": ["safari"]})
        stages = mock_rwl.call_args[1]["pipes"]
        assert "browser" in stages
        assert "local_folders" in stages
        assert "local_files" in stages

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_orchestrator_no_browser_when_empty(self, mock_console, mock_orch, mock_rwl):
        run_orchestrator({"browsers": []})
        stages = mock_rwl.call_args[1]["pipes"]
        assert "browser" not in stages


# ---------------------------------------------------------------------------
# Bug 2: TestConfigPreview — config preview display
# ---------------------------------------------------------------------------
class TestConfigPreview:
    """Tests for preview_config() display."""

    def test_preview_shows_directories_and_browsers(self):
        """preview_config should display the selected directories and browsers."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        answers = {
            "directories": ["~/Work", "~/Personal"],
            "browsers": ["safari"],
        }
        preview_config(answers, console=console)
        output = buf.getvalue()
        assert "~/Work" in output
        assert "~/Personal" in output
        assert "safari" in output

    def test_preview_renders_panel(self):
        """preview_config should render a Panel for the config summary."""
        mock_console = MagicMock()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        preview_config(answers, console=mock_console)
        panel_found = any(
            isinstance(arg, Panel) for call_obj in mock_console.print.call_args_list for arg in call_obj[0]
        )
        assert panel_found, "Expected a Panel object in print args"

    def test_preview_panel_contains_directories(self):
        """Panel content should include the configured directories."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        answers = {"directories": ["~/Work", "~/Personal"], "browsers": []}
        preview_config(answers, console=console)
        output = buf.getvalue()
        assert "~/Work" in output
        assert "~/Personal" in output


# ---------------------------------------------------------------------------
# Bug 3: TestGenerateConfigEdgeCases — empty/missing answers
# ---------------------------------------------------------------------------
class TestGenerateConfigEdgeCases:
    """Tests for generate_config() with missing/empty input."""

    def test_missing_directories_key(self):
        """generate_config should handle answers missing 'directories'."""
        result = generate_config({"browsers": ["safari"]})
        assert "directories" in result
        assert isinstance(result["directories"], list)

    def test_missing_browsers_key(self):
        """generate_config should handle answers missing 'browsers'."""
        result = generate_config({"directories": ["~/Work"]})
        assert "browsers" in result
        assert result["browsers"] == []

    def test_empty_dict(self):
        """generate_config should handle completely empty answers dict."""
        result = generate_config({})
        assert isinstance(result, dict)
        assert "directories" in result
        assert "browsers" in result


# ---------------------------------------------------------------------------
# TestInterruptHandling — 4 tests
# ---------------------------------------------------------------------------
class TestInterruptHandling:
    """Tests for Ctrl+C and PromptCancelled handling during interactive flows."""

    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.console")
    def test_keyboard_interrupt_during_collect_answers(self, mock_console, mock_prompt):
        """Ctrl+C during collect_answers() raises PromptCancelled (converted by SafePrompt)."""
        from footprinter.cli._prompt import PromptCancelled

        mock_prompt.side_effect = PromptCancelled("Ctrl+C")
        with pytest.raises(PromptCancelled):
            collect_answers()

    @patch("footprinter.cli.setup.Confirm")
    @patch("footprinter.cli.setup._choose_preset", return_value=None)
    @patch("footprinter.cli.setup.collect_answers", side_effect=KeyboardInterrupt)
    @patch("footprinter.cli.setup.console")
    def test_keyboard_interrupt_during_wizard(self, mock_console, mock_collect, mock_preset, mock_confirm):
        """KeyboardInterrupt during run_interactive_wizard() propagates to caller."""
        with pytest.raises(KeyboardInterrupt):
            run_interactive_wizard()

    @patch("footprinter.cli.setup.Confirm")
    @patch("footprinter.cli.setup._choose_preset", return_value=None)
    @patch("footprinter.cli.setup.collect_answers")
    @patch("footprinter.cli.setup.console")
    def test_prompt_cancelled_during_collect_answers(self, mock_console, mock_collect, mock_preset, mock_confirm):
        """PromptCancelled from collect_answers() propagates through except Exception blocks."""
        from footprinter.cli._prompt import PromptCancelled

        mock_collect.side_effect = PromptCancelled("escape pressed")
        with pytest.raises(PromptCancelled):
            run_interactive_wizard()

    @patch("footprinter.cli.setup.Confirm")
    @patch("footprinter.cli.setup._choose_preset", return_value=None)
    @patch("footprinter.cli.setup.collect_answers")
    @patch("footprinter.cli.setup.console")
    def test_prompt_cancelled_during_wizard(self, mock_console, mock_collect, mock_preset, mock_confirm):
        """PromptCancelled during wizard propagates to _handle_setup for exit(130)."""
        from footprinter.cli._prompt import PromptCancelled

        mock_collect.side_effect = PromptCancelled("escape pressed")
        with pytest.raises(PromptCancelled):
            run_interactive_wizard()


# ---------------------------------------------------------------------------
# TestRunOrchestrator — 2 tests
# ---------------------------------------------------------------------------
class TestRunOrchestrator:
    """Tests for run_orchestrator() error handling (in-process)."""

    @patch("footprinter.cli.setup._run_with_logging", side_effect=ValueError("bad config"))
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_handles_pipeline_error(self, mock_console, mock_orch, mock_rwl):
        """ValueError from pipeline prints warning, doesn't crash."""
        run_orchestrator({"browsers": []})  # Should not raise
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("error" in c.lower() for c in calls)

    @patch("footprinter.cli.setup._run_with_logging", side_effect=KeyboardInterrupt)
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_handles_keyboard_interrupt(self, mock_console, mock_orch, mock_rwl):
        """KeyboardInterrupt during pipeline handled gracefully."""
        run_orchestrator({"browsers": []})  # Should not raise
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("interrupt" in c.lower() for c in calls)


# ---------------------------------------------------------------------------
# TestCustomDirectoryInput — 2 tests
# ---------------------------------------------------------------------------
class TestCustomDirectoryInput:
    """Tests for custom directory input in collect_answers()."""

    @patch("footprinter.cli.setup.os.path.isdir")
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_custom_directory_added(self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir):
        """Custom directory path is added to the directories list."""
        # Only /tmp exists — skip optional dirs
        mock_isdir.side_effect = lambda p: p == "/tmp"
        # Prompt.ask calls:
        # 1. "/tmp" (first directory)
        # 2. "" (blank to finish directory input)
        mock_prompt.side_effect = ["/tmp", ""]
        # Confirm.ask calls:
        # 1-2. Include safari/chrome? -> No
        mock_confirm.side_effect = [False, False]
        answers = collect_answers()
        assert "/tmp" in answers["directories"]

    @patch("footprinter.cli.setup.os.path.isdir")
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_invalid_custom_directory_rejected(
        self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir
    ):
        """Invalid path prints error; valid path on retry is accepted."""
        # Only /tmp exists
        mock_isdir.side_effect = lambda p: p == "/tmp"
        # Prompt.ask calls:
        # 1. "/nonexistent/path/xyz" (invalid directory)
        # 2. "/tmp" (valid directory)
        # 3. "" (blank to finish directory input)
        mock_prompt.side_effect = ["/nonexistent/path/xyz", "/tmp", ""]
        # Confirm.ask calls:
        # 1-2. Include safari/chrome? -> No
        mock_confirm.side_effect = [False, False]
        answers = collect_answers()
        assert "/tmp" in answers["directories"]
        assert "/nonexistent/path/xyz" not in answers["directories"]
        # Verify the error message was printed
        printed = [str(c) for c in mock_console.print.call_args_list]
        assert any("not found" in p.lower() for p in printed)


# ---------------------------------------------------------------------------
# TestBrowsersOptional — 3 tests
# ---------------------------------------------------------------------------
class TestBrowsersOptional:
    """Tests for optional browsers behavior."""

    def test_no_browsers_accepted(self):
        """Empty browsers list generates valid config."""
        answers = {"directories": ["~/Work"], "browsers": []}
        result = generate_config(answers)
        assert result["browsers"] == []

    def test_no_browsers_validates(self, tmp_path):
        """Empty browsers list passes validation."""
        d = tmp_path / "scandir"
        d.mkdir()
        config = {"directories": [str(d)], "browsers": []}
        errors, _ = validate_config(config)
        assert not any("browsers" in e for e in errors)

    def test_browsers_none_is_error(self, tmp_path):
        """Missing browsers key still errors."""
        d = tmp_path / "scandir"
        d.mkdir()
        config = {"directories": [str(d)]}
        errors, _ = validate_config(config)
        assert any("browsers" in e.lower() for e in errors)


# Bug 100: TestGenerateConfigDefaults — safe defaults for fresh installs
# ---------------------------------------------------------------------------
class TestGenerateConfigDefaults:
    """generate_config() should produce safe defaults for fresh installs."""

    def _generate(self, **overrides):
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        answers.update(overrides)
        return generate_config(answers)

    def test_no_google_drive_section(self):
        """Template-generated config must not contain google_drive section."""
        config = self._generate()
        assert "google_drive" not in config

    def test_no_gmail_section(self):
        """Template-generated config must not contain gmail section."""
        config = self._generate()
        assert "gmail" not in config

    def test_no_mycompany_in_folder_classifications(self):
        """No 'mycompany' placeholder in folder_classifications."""
        config = self._generate()
        for key, val in config.get("folder_classifications", {}).items():
            assert not any("mycompany" in str(v) for v in (val or []))

    def test_semantic_section_present_without_semantic_arg(self):
        """generate_config() must always write semantic section."""
        config = self._generate()
        assert "semantic" in config
        assert config["semantic"]["file_vectorization"] is False
        assert config["semantic"]["chat_vectorization"] is False

    def test_semantic_section_present_with_none_semantic(self):
        """generate_config(semantic=None) must still write semantic section."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        config = generate_config(answers, semantic=None)
        assert "semantic" in config
        assert config["semantic"]["file_vectorization"] is False
        assert config["semantic"]["chat_vectorization"] is False

    def test_semantic_section_applies_explicit_values(self):
        """generate_config() with semantic dict applies those values."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        config = generate_config(answers, semantic={"file_vectorization": True, "chat_vectorization": False})
        assert config["semantic"]["file_vectorization"] is True
        assert config["semantic"]["chat_vectorization"] is False


# ---------------------------------------------------------------------------
# TestValidateConfigWarnings — 3 tests
# ---------------------------------------------------------------------------
class TestValidateConfigWarnings:
    """Tests for validate_config() warning behavior."""

    def test_missing_exclusions_returns_warning(self, tmp_path):
        """Config without exclusions/indexing sections produces warnings."""
        d = tmp_path / "scandir"
        d.mkdir()
        config = {"directories": [str(d)], "browsers": ["safari"]}
        errors, warnings = validate_config(config)
        assert errors == []
        assert any("exclusions" in w for w in warnings)
        assert any("indexing" in w for w in warnings)

    def test_complete_config_no_warnings(self, tmp_path):
        """Config with all sections produces no warnings."""
        d = tmp_path / "scandir"
        d.mkdir()
        config = {
            "directories": [str(d)],
            "browsers": ["safari"],
            "exclusions": {"patterns": ["*.pyc"]},
            "indexing": {"max_file_size": 1000000},
        }
        errors, warnings = validate_config(config)
        assert errors == []
        assert warnings == []

    def test_existing_callers_unaffected(self):
        """None config still returns errors as first element."""
        errors, warnings = validate_config(None)
        assert len(errors) == 1
        assert "empty" in errors[0].lower()
        assert warnings == []


class TestLaunchDashboardRemoved:
    """Verify launch_dashboard() is removed from setup.py."""

    def test_launch_dashboard_not_exported(self):
        """launch_dashboard should not exist in setup module."""
        from footprinter.cli import setup

        assert not hasattr(setup, "launch_dashboard"), "launch_dashboard should be removed from setup.py"

    def test_no_stale_python_m_refs(self):
        """No 'python -m' references in user-facing strings in setup.py."""
        import inspect

        from footprinter.cli import setup

        source = inspect.getsource(setup)
        assert "python -m" not in source


# ---------------------------------------------------------------------------
# TestOfferSetupClaude — 2 tests
# ---------------------------------------------------------------------------
class TestOfferSetupClaude:
    """Tests for offer_setup_claude() interactive flow."""

    @patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=False)
    def test_skip_does_nothing(self, mock_confirm, mock_avail):
        """Declining MCP setup should not call any mcp_setup functions."""
        offer_setup_claude()  # Should not raise

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    @patch("footprinter.cli.mcp_setup.generate_snippet", side_effect=Exception("test"))
    def test_handles_import_error(self, mock_gen, mock_confirm, mock_avail, mock_console):
        """If mcp_setup.generate_snippet fails, error is handled gracefully."""
        offer_setup_claude()  # Should not raise
        calls = [str(c) for c in mock_console.print.call_args_list]
        assert any("failed" in c.lower() or "manually" in c.lower() for c in calls)

    @patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=False)
    def test_returns_false_when_declined(self, mock_confirm, mock_avail):
        """Declining MCP setup should return False."""
        result = offer_setup_claude()
        assert result is False

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=False)
    def test_returns_false_when_mcp_unavailable(self, mock_avail, mock_console):
        """Missing mcp package should return False."""
        result = offer_setup_claude()
        assert result is False

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    @patch("footprinter.cli.mcp_setup.generate_snippet", return_value={"mcpServers": {}})
    @patch("footprinter.cli.mcp_setup.write_config", return_value=True)
    def test_returns_true_on_success(self, mock_write, mock_gen, mock_confirm, mock_avail, mock_console):
        """Successful MCP config should return True."""
        result = offer_setup_claude()
        assert result is True

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.mcp_setup.is_mcp_available", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    @patch("footprinter.cli.mcp_setup.generate_snippet", side_effect=Exception("fail"))
    def test_returns_false_on_failure(self, mock_gen, mock_confirm, mock_avail, mock_console):
        """Exception during MCP config should return False."""
        result = offer_setup_claude()
        assert result is False


# ---------------------------------------------------------------------------
# TestPrintSummaryNoFalseClaim — 1 test
# ---------------------------------------------------------------------------
class TestPrintSummaryNoFalseClaim:
    """Tests that print_summary does not make false claims."""

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_no_false_clients_claim(self, mock_console, mock_counts):
        """print_summary should NOT claim 'Clients and projects are auto-detected'."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "auto-detected" not in printed.lower()

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_no_client_project_setup_hint(self, mock_console, mock_counts):
        """print_summary should NOT include 'Client/project setup' hint."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "Client/project setup" not in printed

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_mcp_hint_removed_entirely(self, mock_console, mock_counts):
        """MCP hint should NOT appear regardless of mcp_configured value."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary(mcp_configured=False)
        printed = _extract_printed_text(mock_console)
        assert "fp setup mcp --write" not in printed


# ---------------------------------------------------------------------------
# TestSummaryPanel — hints wrapped in Panel
# ---------------------------------------------------------------------------
class TestSummaryPanel:
    """Verify print_summary no longer uses a Panel for hints."""

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_summary_no_panel_for_hints(self, mock_console, mock_counts):
        """print_summary should NOT wrap hints in a Panel anymore."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        panel_found = any(
            isinstance(arg, Panel) for call_obj in mock_console.print.call_args_list for arg in call_obj[0]
        )
        assert not panel_found, "Summary should not use a Panel for hints"

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_summary_no_mcp_hint(self, mock_console, mock_counts):
        """MCP hint should not appear in summary."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary(mcp_configured=False)
        printed = _extract_printed_text(mock_console)
        assert "fp setup mcp --write" not in printed


# ---------------------------------------------------------------------------
# TestNonTTYDegradation — output without markup in piped contexts
# ---------------------------------------------------------------------------
class TestNonTTYDegradation:
    """Verify Rich output degrades gracefully in non-TTY environments."""

    def test_preview_config_non_tty(self):
        """preview_config should not raise in non-TTY mode."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, no_color=True)
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        preview_config(answers, console=console)
        output = buf.getvalue()
        assert "~/Work" in output
        assert "safari" in output

    def test_preview_config_no_color_strips_markup(self):
        """Non-TTY output should not contain raw Rich markup tags."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, no_color=True)
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        preview_config(answers, console=console)
        output = buf.getvalue()
        assert "[bold]" not in output
        assert "[dim]" not in output


# ---------------------------------------------------------------------------
# TestUXImprovements
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TestWelcomeScreen — welcome screen content
# ---------------------------------------------------------------------------
class TestWelcomeScreen:
    """Tests for welcome screen shown before prompts."""

    def _get_welcome_text(self):
        """Run wizard with mocked console and extract all printed text including Panel content."""
        with (
            patch("footprinter.cli.setup.console") as mock_console,
            patch("footprinter.cli.setup._choose_preset", return_value=None),
            patch("footprinter.cli.setup.collect_answers", side_effect=KeyboardInterrupt),
            patch("footprinter.cli.setup.Confirm"),
        ):
            try:
                run_interactive_wizard()
            except KeyboardInterrupt:
                pass
            parts = []
            for call in mock_console.print.call_args_list:
                for arg in call[0]:
                    if isinstance(arg, Panel):
                        parts.append(str(arg.renderable))
                    else:
                        parts.append(str(arg))
            return " ".join(parts).lower()

    def test_welcome_explains_what_footprinter_does(self):
        """Welcome output should explain what Footprinter does."""
        text = self._get_welcome_text()
        assert "index" in text

    def test_welcome_previews_setup_steps(self):
        """Welcome output should preview the setup steps."""
        text = self._get_welcome_text()
        assert "director" in text
        assert "browser" in text
        assert "chat" in text

    @_requires_darwin
    def test_welcome_notes_prerequisites(self):
        """Welcome output should mention prerequisites (macOS-only)."""
        text = self._get_welcome_text()
        assert "full disk access" in text

    def test_welcome_lists_data_sources_step(self):
        """Welcome step list should include Data Sources as a setup phase (no Google)."""
        text = self._get_welcome_text()
        assert "data sources" in text
        # Google removed from wizard
        assert "google" not in text


# ---------------------------------------------------------------------------
# TestStepGuidance — per-step guidance text
# ---------------------------------------------------------------------------
class TestStepGuidance:
    """Tests for contextual guidance at each wizard step."""

    @patch("footprinter.cli.setup.os.path.isdir")
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_directory_step_has_guidance(self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir):
        """Directory step should have meaningful guidance text."""
        mock_isdir.side_effect = lambda p: p == "/tmp"
        mock_prompt.side_effect = ["/tmp", ""]
        mock_confirm.side_effect = [False, False]
        collect_answers()
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        printed_lower = printed.lower()
        # Should explain what directories are scanned for
        assert "scan" in printed_lower or "index" in printed_lower

    @_requires_darwin
    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.os.path.isdir")
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_browser_step_mentions_full_disk_access(
        self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir, mock_subprocess
    ):
        """Selecting Safari should surface a Full Disk Access warning to the user (macOS-only).

        FDA messaging moved from an inline prompt label to active guidance
        that fires after Safari is selected.
        """
        mock_isdir.side_effect = lambda p: p == "/tmp"
        mock_prompt.side_effect = ["/tmp", ""]
        # safari=yes triggers helper (open=yes, granted=yes), chrome=no
        mock_confirm.side_effect = [True, True, True, False]
        collect_answers()
        printed = _extract_printed_text(mock_console)
        assert "full disk access" in printed.lower()


# ---------------------------------------------------------------------------
# TestBrowserTDAMessaging — per-browser Full Disk Access notes
# ---------------------------------------------------------------------------
class TestBrowserTDAMessaging:
    """Browser step should show per-browser TDA messaging, not a blanket requirement."""

    def _run_collect_answers(self, confirm_responses):
        """Run collect_answers with mocked I/O, return (printed_text, confirm_call_args).

        confirm_responses: list of bools for Confirm.ask calls.
        First calls handle optional dirs (none triggered here), rest handle browsers.
        When safari=True, the FDA helper consumes 2 additional
        Confirm.ask calls (open settings, granted).
        """
        with (
            patch("footprinter.cli.setup.sys") as mock_sys,
            patch("footprinter.cli.setup.os.path.isdir") as mock_isdir,
            patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p),
            patch("footprinter.cli.setup.Prompt.ask") as mock_prompt,
            patch("footprinter.cli.setup.Confirm.ask") as mock_confirm,
            patch("footprinter.cli.setup.console") as mock_console,
            patch("footprinter.cli.setup.subprocess.run"),
        ):
            mock_sys.platform = "darwin"
            mock_isdir.side_effect = lambda p: p == "/tmp"
            mock_prompt.side_effect = ["/tmp", ""]
            mock_confirm.side_effect = confirm_responses
            collect_answers()
            printed = _extract_printed_text(mock_console)
            confirm_calls = [str(c) for c in mock_confirm.call_args_list]
            return printed, confirm_calls

    def test_safari_prompt_shows_tda_requirement(self):
        """Selecting Safari should surface a Full Disk Access warning via active guidance."""
        # safari=yes triggers helper (open=yes, granted=yes), chrome=no
        printed, _ = self._run_collect_answers([True, True, True, False])
        assert "full disk access" in printed.lower()

    def test_chrome_prompt_shows_no_tda(self):
        """Chrome's prompt should not carry permission noise (no FDA, no permissions hint)."""
        _, confirm_calls = self._run_collect_answers([False, False])
        chrome_call = confirm_calls[1]  # chrome is second in get_available_browsers()
        assert "full disk access" not in chrome_call.lower()
        assert "permission" not in chrome_call.lower()

    def test_browser_header_no_blanket_tda(self):
        """Browser section header should NOT contain a blanket TDA requirement."""
        printed, _ = self._run_collect_answers([False, False])
        assert "requires full disk access in system settings" not in printed.lower()

    def test_browser_defaults_to_yes(self):
        """Enter-through should include all available browsers in answers."""
        with (
            patch("footprinter.cli.setup.sys") as mock_sys,
            patch("footprinter.cli.setup.os.path.isdir") as mock_isdir,
            patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p),
            patch("footprinter.cli.setup.Prompt.ask") as mock_prompt,
            patch("footprinter.cli.setup.Confirm.ask") as mock_confirm,
            patch("footprinter.cli.setup.console"),
            patch("footprinter.cli.setup.subprocess.run"),
        ):
            mock_sys.platform = "darwin"
            mock_isdir.side_effect = lambda p: p == "/tmp"
            mock_prompt.side_effect = ["/tmp", ""]
            # safari yes triggers helper (open=yes, granted=yes), then chrome yes
            mock_confirm.side_effect = [True, True, True, True]
            answers = collect_answers()
            assert answers["browsers"] == ["safari", "chrome"]

    def test_welcome_panel_specifies_safari(self):
        """Welcome panel prerequisite should say 'Safari history', not 'browser history'."""
        with (
            patch("footprinter.cli.setup.sys") as mock_sys,
            patch("footprinter.cli.setup.console") as mock_console,
            patch("footprinter.cli.setup._choose_preset", return_value=None),
            patch("footprinter.cli.setup.collect_answers", side_effect=KeyboardInterrupt),
            patch("footprinter.cli.setup.Confirm"),
        ):
            mock_sys.platform = "darwin"
            try:
                run_interactive_wizard()
            except KeyboardInterrupt:
                pass
            parts = []
            for call in mock_console.print.call_args_list:
                for arg in call[0]:
                    if isinstance(arg, Panel):
                        parts.append(str(arg.renderable))
                    else:
                        parts.append(str(arg))
            text = " ".join(parts).lower()
            assert "safari history" in text
            assert "full disk access for browser history" not in text


# ---------------------------------------------------------------------------
# TestUXImprovements
# ---------------------------------------------------------------------------
class TestUXImprovements:
    """Tests for wizard UX improvements."""

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    def test_run_orchestrator_stages_calls_pipeline(self, mock_orch, mock_rwl):
        """_run_orchestrator_stages should call _run_with_logging in-process."""
        from footprinter.cli.setup import _run_orchestrator_stages

        _run_orchestrator_stages(["local_folders", "local_files"])
        mock_rwl.assert_called_once()

    def test_getting_started_commands_in_source(self):
        """print_summary source should contain Getting started commands."""
        import inspect

        source = inspect.getsource(print_summary)
        assert "fp search" in source
        assert "fp ingest status" in source
        assert "fp -h" in source

    def test_print_summary_has_no_dashboard_launched_param(self):
        """print_summary() should not accept dashboard_launched param."""
        import inspect

        sig = inspect.signature(print_summary)
        assert "dashboard_launched" not in sig.parameters


# ---------------------------------------------------------------------------
# TestExampleConfigDefaults — bundled config file regression guards
# ---------------------------------------------------------------------------
class TestExampleConfigDefaults:
    """Verify the bundled config.example.yaml has safe defaults.

    These load the actual YAML file (not generate_config output) to guard
    against comment additions breaking YAML parsing or altering defaults.
    """

    def _load_example(self):
        with open(get_bundled_path("config.example.yaml"), "r") as f:
            return yaml.safe_load(f)

    def test_example_no_google_drive_section(self):
        """Bundled config template must not contain google_drive section."""
        config = self._load_example()
        assert "google_drive" not in config

    def test_example_no_gmail_section(self):
        """Bundled config template must not contain gmail section."""
        config = self._load_example()
        assert "gmail" not in config

    def test_example_has_connector_comment(self):
        """Bundled config template should reference 'fp connect install'."""
        with open(get_bundled_path("config.example.yaml"), "r") as f:
            text = f.read()
        assert "fp connect install" in text

    def test_example_parses_cleanly(self):
        """Bundled config.example.yaml must parse without errors."""
        config = self._load_example()
        assert isinstance(config, dict)
        assert "directories" in config


# ---------------------------------------------------------------------------
# TestGenerateConfigGoogle — 3 tests
# ---------------------------------------------------------------------------
@_requires_google
class TestGenerateConfigGoogle:
    """Tests for generate_config() Google service behavior.

    The template has no google_drive/gmail sections — connector sections are
    written by ``fp connect install google``, not the template.
    """

    @patch("footprinter.connectors.is_installed", return_value=True)
    def test_drive_section_created_with_verified_account(self, _mock_inst):
        """generate_config() should create google_drive section when drive is verified."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        google = {"personal": ["drive"]}
        result = generate_config(answers, connector_results=google)
        assert result["google_drive"]["enabled"] is True

    @patch("footprinter.connectors.is_installed", return_value=True)
    def test_no_gmail_section_for_drive_only_account(self, _mock_inst):
        """generate_config() should not create gmail section for drive-only accounts."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        google = {"personal": ["drive"]}
        result = generate_config(answers, connector_results=google)
        assert "gmail" not in result

    def test_no_google_answers_has_no_connector_sections(self):
        """Empty google dict produces no connector sections."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, connector_results={})
        assert "google_drive" not in result
        assert "gmail" not in result

    @patch("footprinter.connectors.is_installed", return_value=True)
    def test_generate_config_creates_drive_section_when_template_lacks_it(self, _mock_inst):
        """generate_config() should create google_drive section even when
        config.example.yaml doesn't include it."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        google = {"personal": ["drive"]}
        result = generate_config(answers, connector_results=google)
        assert "google_drive" in result, "google_drive section should be created"
        assert result["google_drive"]["enabled"] is True

    @patch("footprinter.connectors.is_installed", return_value=True)
    def test_generate_config_creates_gmail_section_when_template_lacks_it(self, _mock_inst):
        """generate_config() should create gmail section with accounts even when
        config.example.yaml doesn't include it."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        google = {"personal": ["gmail"]}
        result = generate_config(answers, connector_results=google)
        assert "gmail" in result, "gmail section should be created"
        assert result["gmail"]["enabled"] is True
        accounts = result["gmail"].get("accounts", [])
        assert len(accounts) == 1
        assert accounts[0]["name"] == "personal"


# ---------------------------------------------------------------------------
# TestRunOrchestratorGoogle — 3 tests
# ---------------------------------------------------------------------------
@_requires_google
class TestRunOrchestratorGoogle:
    """Tests for run_orchestrator() with connector results."""

    @patch("footprinter.connectors.is_installed", return_value=True)
    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_includes_connector_pipes_when_results_present(self, mock_console, mock_orch, mock_rwl, _mock_inst):
        """Connector pipes should be added when connector_results is non-empty."""
        run_orchestrator({"browsers": []}, connector_results={"personal": ["drive"]})
        stages = mock_rwl.call_args[1]["pipes"]
        assert "drive_folders" in stages
        assert "drive_files" in stages
        assert "gmail" in stages  # all pipes from installed connector spec

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_no_connector_pipes_when_results_empty(self, mock_console, mock_orch, mock_rwl):
        """No connector pipes when connector_results is empty."""
        run_orchestrator({"browsers": []}, connector_results={})
        stages = mock_rwl.call_args[1]["pipes"]
        assert "drive_folders" not in stages
        assert "gmail" not in stages


# ---------------------------------------------------------------------------
# TestPrintSummaryGoogle — 1 test
# ---------------------------------------------------------------------------
class TestPrintSummaryGoogle:
    """Tests for print_summary() cloud integration hint."""

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_google_hint_updated(self, mock_console, mock_counts):
        """Cloud hint should reference fp connect, not setup_google_auth."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "requires Google auth" not in printed
        assert "setup_google_auth" not in printed
        # now points to fp connect
        assert "fp connect" in printed


# ---------------------------------------------------------------------------
# TestGoogleSubcommand — 3 tests
# ---------------------------------------------------------------------------
class TestGoogleSubcommand:
    """Tests that `fp setup google` subcommand has been removed."""

    def test_google_subcommand_not_recognized(self):
        """main() with ['google'] should raise SystemExit (argparse unknown command)."""
        with patch("sys.argv", ["fp", "google"]):
            with pytest.raises(SystemExit):
                main()

    def test_setup_help_no_google(self, capsys):
        """'fp setup --help' output should not mention 'google'."""
        with pytest.raises(SystemExit):
            with patch("sys.argv", ["fp", "--help"]):
                main()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "google" not in combined.lower()

    @patch("footprinter.cli.setup.run_interactive_wizard")
    def test_wizard_unchanged(self, mock_wizard):
        """main() with no subcommand should still call run_interactive_wizard()."""
        with patch("sys.argv", ["fp"]):
            main()
        mock_wizard.assert_called_once()


# ---------------------------------------------------------------------------
# Shared helper for wizard flow tests
# Moved to conftest.py as run_wizard_mocked()
# ---------------------------------------------------------------------------
from tests.conftest import run_wizard_mocked


# ---------------------------------------------------------------------------
# RED 1 — TestPhaseProgression
# ---------------------------------------------------------------------------
class TestPhaseProgression:
    """Verify step indicators appear at each phase transition."""

    def test_all_seven_phases_have_step_indicators(self):
        """run_interactive_wizard() should print 'Step N of 7' for N=1..7."""
        mocks = run_wizard_mocked()
        printed = _extract_printed_text(mocks["console"])
        for n in range(1, 8):
            assert f"Step {n} of 7" in printed, f"Missing 'Step {n} of 7'"

    def test_phase_names_appear(self):
        """Each phase indicator should include a descriptive name."""
        mocks = run_wizard_mocked()
        printed = _extract_printed_text(mocks["console"])
        for name in [
            "Welcome",
            "Data Sources",
            "Content",
            "Confirm",
            "Claude Desktop",
            "Populate",
            "Summary",
        ]:
            assert name in printed, f"Missing phase name '{name}'"

    def test_organization_phase_removed(self):
        """'Organization' should NOT appear as a phase name."""
        mocks = run_wizard_mocked()
        printed = _extract_printed_text(mocks["console"])
        assert "Organization" not in printed, "Organization phase should be removed"

    def test_visible_connect_phase_removed(self):
        """No 'Step N of 7 — Connect' rule — access policies seed silently in Confirm & Write."""
        mocks = run_wizard_mocked()
        printed = _extract_printed_text(mocks["console"])
        # The visible "Connect" phase rule should not be emitted.
        # ("Claude Desktop" replaces it as a separate phase, so simple "Connect"
        # in a Step rule is the marker.)
        for n in range(1, 8):
            assert f"Step {n} of 7 — [bold]Connect[/bold]" not in printed
            assert f"Step {n} of 7 — Connect" not in printed


# ---------------------------------------------------------------------------
# TestPhaseOrdering — major calls happen in the right phase order
# ---------------------------------------------------------------------------
class TestPhaseOrdering:
    """Verify the new 7-phase ordering: data sources -> content -> write ->
    Claude Desktop -> populate."""

    def test_claude_desktop_runs_before_populate(self):
        """offer_setup_claude must be called before run_orchestrator so the user
        can restart Claude Desktop while indexing finishes."""
        order = []
        offer_mock = MagicMock(side_effect=lambda *a, **kw: order.append("claude") or False)
        orch_mock = MagicMock(side_effect=lambda *a, **kw: order.append("populate"))
        run_wizard_mocked(offer_setup_claude=offer_mock, run_orchestrator=orch_mock)
        assert order == ["claude", "populate"], (
            f"Expected Claude Desktop before Populate, got: {order}"
        )

    def test_csv_import_runs_after_orchestrator(self):
        """_offer_csv_import_wizard runs after run_orchestrator, when the DB exists.

        Asking earlier (in Data Sources) would silently skip on fresh installs
        because the SQLite DB isn't created until the orchestrator runs.
        """
        order = []
        csv_mock = MagicMock(side_effect=lambda *a, **kw: order.append("csv"))
        orch_mock = MagicMock(side_effect=lambda *a, **kw: order.append("populate"))
        run_wizard_mocked(_offer_csv_import_wizard=csv_mock, run_orchestrator=orch_mock)
        assert "csv" in order and "populate" in order
        assert order.index("populate") < order.index("csv"), (
            f"CSV import must run after run_orchestrator (DB must exist); got {order}"
        )

    def test_access_policies_seeded_silently_in_confirm_write(self):
        """seed_access_policies runs after write_config and before offer_setup_claude
        — silently inside Confirm & Write, not as a separate visible phase."""
        order = []
        write_mock = MagicMock(side_effect=lambda *a, **kw: order.append("write"))
        seed_mock = MagicMock(side_effect=lambda *a, **kw: order.append("seed"))
        claude_mock = MagicMock(side_effect=lambda *a, **kw: order.append("claude") or False)
        run_wizard_mocked(
            write_config=write_mock,
            seed_access_policies=seed_mock,
            offer_setup_claude=claude_mock,
        )
        assert order == ["write", "seed", "claude"], (
            f"Expected write -> seed -> claude, got: {order}"
        )

    def test_content_phase_runs_after_data_sources(self):
        """collect_vectorization_answers runs after collect_chat_export_path so the
        Content & Search phase comes after Data Sources."""
        order = []
        chat_mock = MagicMock(side_effect=lambda *a, **kw: order.append("chat") or None)
        vec_mock = MagicMock(
            side_effect=lambda *a, **kw: order.append("vec")
            or {"file_vectorization": False, "chat_vectorization": False, "content_snippets": False}
        )
        run_wizard_mocked(collect_chat_export_path=chat_mock, collect_vectorization_answers=vec_mock)
        assert order == ["chat", "vec"], f"Expected chat -> vec, got: {order}"


# ---------------------------------------------------------------------------
# TestPhaseRule — _print_phase renders Rich Rule
# ---------------------------------------------------------------------------
class TestPhaseRule:
    """Verify _print_phase renders a Rule with step text."""

    @patch("footprinter.cli.setup.console")
    def test_print_phase_renders_rule(self, mock_console):
        """_print_phase should print a Rule instance."""
        from rich.rule import Rule

        from footprinter.cli.setup import _print_phase

        _print_phase(1, 7, "Welcome")
        rule_found = any(isinstance(arg, Rule) for call_obj in mock_console.print.call_args_list for arg in call_obj[0])
        assert rule_found, "Expected a Rule object in print args"

    @patch("footprinter.cli.setup.console")
    def test_print_phase_rule_contains_step_text(self, mock_console):
        """Rule title should contain step number and phase name."""
        from footprinter.cli.setup import _print_phase

        _print_phase(3, 7, "Confirm")
        text = _extract_printed_text(mock_console)
        assert "Step 3 of 7" in text
        assert "Confirm" in text


# ---------------------------------------------------------------------------
# RED 2 — TestConfigWrittenOnce
# ---------------------------------------------------------------------------
class TestConfigWrittenOnce:
    """Config should be written exactly once, even when Google OAuth succeeds."""

    def test_write_config_called_once_without_google(self):
        """write_config called once when Google returns empty."""
        mocks = run_wizard_mocked()
        assert mocks["write_config"].call_count == 1


# ---------------------------------------------------------------------------
# RED 3 — TestGoogleOAuthBeforeConfigWrite
# ---------------------------------------------------------------------------
class TestGoogleOAuthBeforeConfigWrite:
    """Wizard passes google={} to generate_config — no Google setup in wizard."""

    def test_generate_config_receives_empty_connector_results(self):
        """generate_config() should receive connector_results={} from wizard."""
        mocks = run_wizard_mocked()
        gen_call = mocks["generate_config"].call_args
        cr_arg = gen_call[1].get("connector_results", {})
        assert cr_arg == {}, f"Expected connector_results={{}}, got {cr_arg}"


# ---------------------------------------------------------------------------
# RED 4 — TestConsolidatedDataLoad
# ---------------------------------------------------------------------------
class TestConsolidatedDataLoad:
    """Data-load should be a single prompt, not three separate ones."""

    def test_single_index_and_analyze_prompt(self):
        """Wizard should show 'Index and analyze' prompt, not separate ones."""
        confirm_calls = []
        original_return = True

        def track_confirm(prompt, **kwargs):
            confirm_calls.append(str(prompt))
            return original_return

        mocks = run_wizard_mocked(
            **{"Confirm.ask": track_confirm},
        )
        # Should have ONE data-load prompt containing "Index and analyze"
        data_prompts = [c for c in confirm_calls if "index" in c.lower() or "analys" in c.lower()]
        assert len(data_prompts) == 1, (
            f"Expected 1 consolidated data-load prompt, got {len(data_prompts)}: {data_prompts}"
        )
        assert "index" in data_prompts[0].lower() and "analyz" in data_prompts[0].lower()

    def test_yes_runs_orchestrator(self):
        """Answering Yes to consolidated prompt runs orchestrator."""
        mocks = run_wizard_mocked()  # Confirm.ask returns True by default
        mocks["run_orchestrator"].assert_called_once()

    def test_no_skips_orchestrator(self):
        """Answering No to consolidated prompt skips orchestrator."""

        def _confirm_by_prompt(prompt, **kw):
            if "Write" in prompt:
                return True
            return False  # decline indexing, sample seeding, and any future prompts

        mocks = run_wizard_mocked(**{"Confirm.ask": _confirm_by_prompt})
        mocks["run_orchestrator"].assert_not_called()

    def test_banner_no_project_detection(self):
        """Phase 5 banner should NOT mention 'project detection' or 'classification'."""
        mocks = run_wizard_mocked()
        # Inspect all console.print calls for the banner text
        printed = " ".join(str(call.args[0]) if call.args else "" for call in mocks["console"].print.call_args_list)
        assert "project detection" not in printed.lower(), f"Banner still mentions 'project detection': {printed}"
        assert "classification" not in printed.lower(), f"Banner still mentions 'classification': {printed}"


# ---------------------------------------------------------------------------
# RED 5 — TestWizardResilience
# ---------------------------------------------------------------------------
class TestWizardResilience:
    """Wizard should continue to Summary even when populate steps raise."""

    def test_wizard_continues_when_chat_import_raises(self):
        """Wizard reaches Summary even when import_chat_export() raises."""
        mocks = run_wizard_mocked(
            collect_chat_export_path=MagicMock(return_value="/tmp/export.zip"),
            import_chat_export=MagicMock(side_effect=Exception("chat import failed")),
        )
        mocks["print_summary"].assert_called_once()


# ---------------------------------------------------------------------------
# RED 6 — TestPresetProfiles
# ---------------------------------------------------------------------------
class TestPresetProfiles:
    """Preset profiles at the start of Data Sources phase."""

    def test_quick_start_includes_existing_common_dirs(self):
        """Quick start includes all common directories that exist on disk."""
        from footprinter.cli.setup import _choose_preset

        existing = {"~/Documents", "~/Work", "~/Desktop"}

        def isdir_side_effect(path):
            return path.replace(os.path.expanduser("~"), "~") in existing

        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", side_effect=isdir_side_effect),
            patch("footprinter.cli.setup.console"),
        ):
            result = _choose_preset()
        assert result is not None
        assert set(result["directories"]) == {"~/Documents", "~/Work", "~/Desktop"}

    def test_quick_start_filters_to_existing_dirs_only(self):
        """Quick start only includes directories that actually exist."""
        from footprinter.cli.setup import _choose_preset

        def isdir_side_effect(path):
            return path.replace(os.path.expanduser("~"), "~") == "~/Desktop"

        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", side_effect=isdir_side_effect),
            patch("footprinter.cli.setup.console"),
        ):
            result = _choose_preset()
        assert result is not None
        assert result["directories"] == ["~/Desktop"]

    def test_quick_start_fallback_when_no_common_dirs_exist(self):
        """Quick start falls back to full setup when no common directories exist."""
        from footprinter.cli.setup import _choose_preset

        mock_console = MagicMock()
        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", return_value=False),
            patch("footprinter.cli.setup.console", mock_console),
        ):
            result = _choose_preset()
        assert result is None
        printed = " ".join(str(call.args[0]) for call in mock_console.print.call_args_list if call.args)
        assert "No common directories found" in printed

    def test_quick_start_sets_no_browsers(self):
        """Quick start preset should have empty browsers list."""
        from footprinter.cli.setup import _choose_preset

        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", return_value=True),
            patch("footprinter.cli.setup.console"),
        ):
            result = _choose_preset()
        assert result["browsers"] == []

    def test_full_setup_returns_none(self):
        """Choosing 'Full setup' should return None (run per-item prompts)."""
        from footprinter.cli.setup import _choose_preset

        with patch("footprinter.cli.setup.Prompt.ask", return_value="full"), patch("footprinter.cli.setup.console"):
            result = _choose_preset()
        assert result is None

    def test_quick_start_returns_dirs_and_browsers(self):
        """Quick start preset should return only directories and browsers keys."""
        from footprinter.cli.setup import _choose_preset

        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", return_value=True),
            patch("footprinter.cli.setup.console"),
        ):
            result = _choose_preset()
        assert set(result.keys()) == {"directories", "browsers"}

    def test_quick_start_prompt_text_mentions_common_directories(self):
        """Quick start prompt text should mention common directories, not just ~/Documents."""
        from footprinter.cli.setup import _choose_preset

        mock_console = MagicMock()
        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", return_value=True),
            patch("footprinter.cli.setup.console", mock_console),
        ):
            _choose_preset()
        printed = " ".join(str(call.args[0]) for call in mock_console.print.call_args_list if call.args)
        assert "common directories" in printed.lower()
        assert "add more later" in printed

    def test_quick_start_with_no_dirs_falls_back_to_full(self):
        """Quick start falls back to full setup when no common dirs exist."""
        from footprinter.cli.setup import _choose_preset

        mock_console = MagicMock()
        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", return_value=False),
            patch("footprinter.cli.setup.console", mock_console),
        ):
            result = _choose_preset()
        assert result is None
        printed = " ".join(str(call.args[0]) for call in mock_console.print.call_args_list if call.args)
        assert "No common directories found" in printed

    def test_quick_preset_announces_skipped_steps(self):
        """Quick mode tells the user what it skips so they aren't surprised
        later when browser/chat/CSV results are empty."""
        from footprinter.cli.setup import _choose_preset

        mock_console = MagicMock()
        with (
            patch("footprinter.cli.setup.Prompt.ask", return_value="quick"),
            patch("footprinter.cli.setup.os.path.isdir", return_value=True),
            patch("footprinter.cli.setup.console", mock_console),
        ):
            _choose_preset()
        printed = _extract_printed_text(mock_console).lower()
        # Each major skipped concern named explicitly
        assert "browser" in printed, "Quick mode should name browsers as a skipped step"
        assert "chat" in printed, "Quick mode should name chat history as a skipped step"
        assert "csv" in printed, "Quick mode should name CSV import as a skipped step"

    def test_wizard_skips_collect_answers_for_quick_preset(self):
        """Full wizard run with quick preset should not call collect_answers."""
        # Mock _choose_preset to return quick preset directly
        preset_result = {
            "directories": ["~/Documents"],
            "browsers": [],
        }
        prefix = "footprinter.cli.setup."
        collect_mock = MagicMock()
        with (
            patch(prefix + "console"),
            patch(prefix + "_load_existing_config", return_value=None),
            patch(prefix + "_choose_preset", return_value=preset_result),
            patch(prefix + "collect_chat_export_path", return_value=None),
            patch(prefix + "preview_config"),
            patch(prefix + "Confirm.ask", return_value=True),
            patch(prefix + "generate_config", return_value={"directories": ["~/Work"]}),
            patch(prefix + "write_config"),
            patch(prefix + "collect_answers", collect_mock),
            patch(prefix + "run_orchestrator"),
            patch(prefix + "import_chat_export", return_value={}),
            patch(prefix + "_get_indexing_counts", return_value={}),
            patch(prefix + "seed_access_policies"),
            patch(prefix + "offer_setup_claude", return_value=False),
            patch(prefix + "print_summary"),
            patch(prefix + "get_log_path", return_value=MagicMock()),
            patch(prefix + "_offer_csv_import_wizard"),
        ):
            run_interactive_wizard()
        collect_mock.assert_not_called()


# ---------------------------------------------------------------------------
# TestNoClientSeeding — client seeding removed from wizard
# ---------------------------------------------------------------------------
class TestNoClientSeeding:
    """Verify client seeding has been removed from wizard flow."""

    def test_offer_seed_clients_removed_from_module(self):
        """offer_seed_clients should no longer exist in setup module."""
        import footprinter.cli.setup as setup_mod

        assert not hasattr(setup_mod, "offer_seed_clients")

    def test_no_web_ui_reference(self):
        """'Web UI' should not appear in wizard output."""
        mocks = run_wizard_mocked()
        printed = _extract_printed_text(mocks["console"])
        assert "Web UI" not in printed, "Setup should not reference 'Web UI'"


# ---------------------------------------------------------------------------
# TestProjectsInSummary — project detection in summary phase
# ---------------------------------------------------------------------------
class TestProjectsInSummary:
    """Summary phase should display detected projects and CLI hints."""

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_projects_shown_in_summary(self, mock_console, mock_counts):
        """When projects exist, summary prints project count and CLI hints."""
        mock_counts.return_value = {"projects": 3, "folders": 10, "files": 50}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "Projects detected:" in printed
        assert "3" in printed
        assert "fp project" in printed
        assert "fp client" in printed

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_no_projects_in_summary_when_zero(self, mock_console, mock_counts):
        """When no projects exist, summary omits project display."""
        mock_counts.return_value = {"projects": 0, "folders": 10, "files": 50}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "Projects detected" not in printed


# ---------------------------------------------------------------------------
# Google Drive and Gmail indexing produces 0 results
# ---------------------------------------------------------------------------
@_requires_google
class TestSWE526DriveGmailIndexing:
    """Tests for generate_config() populating source_seeds and gmail.accounts."""

    def test_google_drive_adds_source_seeds(self):
        """generate_config() should add Drive source_seeds for each verified Drive account."""
        answers = {"directories": ["~/Work"], "browsers": []}
        google = {"personal": ["drive"], "work": ["drive"]}
        config = generate_config(answers, connector_results=google)

        seeds = config.get("source_seeds", [])
        drive_seeds = [s for s in seeds if s.get("source_type") == "remote"]
        assert len(drive_seeds) >= 2, f"Expected 2 drive seeds, got {len(drive_seeds)}: {drive_seeds}"

        account_names = {s["account"] for s in drive_seeds}
        assert "personal" in account_names
        assert "work" in account_names

    def test_gmail_populated_when_gmail_verified(self):
        """generate_config() creates gmail section with accounts when gmail is verified."""
        answers = {"directories": ["~/Work"], "browsers": []}
        google = {"work": ["drive", "gmail"]}
        config = generate_config(answers, connector_results=google)

        assert config["gmail"]["enabled"] is True
        accounts = config["gmail"].get("accounts", [])
        assert len(accounts) == 1
        assert accounts[0]["name"] == "work"

    # ── Dedup source_seeds and gmail.accounts on re-run ──

    def test_rerun_does_not_duplicate_source_seeds(self):
        """Re-running generate_config() with pre-existing seeds must not duplicate them."""
        answers = {"directories": ["~/Work"], "browsers": []}
        google = {"personal": ["drive"]}

        # Load the real template and pre-populate a drive_personal seed
        with open(get_bundled_path("config.example.yaml")) as f:
            base_config = yaml.safe_load(f)
        base_config["source_seeds"].append(
            {
                "name": "gdrive_personal",
                "source_type": "remote",
                "account": "personal",
                "label": "Drive (personal)",
                "icon": "cloud",
                "enabled": True,
            }
        )

        # Mock the template load to return config with existing seed
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            with patch("yaml.safe_load", return_value=base_config):
                config = generate_config(answers, connector_results=google)

        seeds = config.get("source_seeds", [])
        personal_seeds = [s for s in seeds if s.get("account") == "personal"]
        assert len(personal_seeds) == 1, f"Expected 1 personal seed, got {len(personal_seeds)}: {personal_seeds}"

    def test_legacy_drive_seed_not_matched_on_upsert(self):
        """generate_config() must NOT match a pre-existing seed with source_type='drive'.
        A new 'remote' seed should be appended, leaving the old one untouched."""
        answers = {"directories": ["~/Work"], "browsers": []}
        google = {"personal": ["drive"]}

        with open(get_bundled_path("config.example.yaml")) as f:
            base_config = yaml.safe_load(f)
        base_config["source_seeds"].append(
            {
                "name": "gdrive_personal",
                "source_type": "drive",
                "account": "personal",
                "label": "Old Label",
                "icon": "cloud",
                "enabled": True,
            }
        )

        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            with patch("yaml.safe_load", return_value=base_config):
                config = generate_config(answers, connector_results=google)

        seeds = config.get("source_seeds", [])
        personal_seeds = [s for s in seeds if s.get("account") == "personal"]
        assert len(personal_seeds) == 2, (
            f"Old 'drive' seed should remain and new 'remote' seed appended, "
            f"got {len(personal_seeds)}: {personal_seeds}"
        )
        old = [s for s in personal_seeds if s["source_type"] == "drive"]
        new = [s for s in personal_seeds if s["source_type"] == "remote"]
        assert len(old) == 1, "Old 'drive' seed should be untouched"
        assert len(new) == 1, "New 'remote' seed should be appended"

    def test_rerun_updates_existing_entries(self):
        """Re-running generate_config() should update existing disabled entries to enabled."""
        answers = {"directories": ["~/Work"], "browsers": []}
        google = {"personal": ["drive"]}

        with open(get_bundled_path("config.example.yaml")) as f:
            base_config = yaml.safe_load(f)
        base_config["source_seeds"].append(
            {
                "name": "gdrive_personal",
                "source_type": "remote",
                "account": "personal",
                "label": "Drive (personal)",
                "icon": "cloud",
                "enabled": False,  # previously disabled
            }
        )

        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            with patch("yaml.safe_load", return_value=base_config):
                config = generate_config(answers, connector_results=google)

        seeds = config.get("source_seeds", [])
        personal_seed = next(s for s in seeds if s.get("account") == "personal")
        assert personal_seed["enabled"] is True, f"Expected enabled=True after re-run, got {personal_seed}"


# ---------------------------------------------------------------------------
# Chat import moved to Data Sources phase
# ---------------------------------------------------------------------------
class TestChatImportPhaseMove:
    """Tests for moving chat export collection to Phase 2 and import to Phase 5."""

    PREFIX = "footprinter.cli.setup."

    def test_chat_prompt_in_phase_2(self):
        """collect_chat_export_path is called in Phase 2, before preview_config."""
        call_order = []

        def track_collect(*a, **kw):
            call_order.append("collect_chat_export_path")
            return None

        def track_preview(*a, **kw):
            call_order.append("preview_config")

        mocks = run_wizard_mocked(
            collect_chat_export_path=MagicMock(side_effect=track_collect),
            preview_config=MagicMock(side_effect=track_preview),
        )
        assert "collect_chat_export_path" in call_order, "collect_chat_export_path was not called"
        assert "preview_config" in call_order, "preview_config was not called"
        idx_collect = call_order.index("collect_chat_export_path")
        idx_preview = call_order.index("preview_config")
        assert idx_collect < idx_preview, (
            f"collect_chat_export_path ({idx_collect}) should be called before preview_config ({idx_preview})"
        )

    def test_preview_shows_chat_export_path(self):
        """preview_config displays the chat export path when provided."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        preview_config(answers, console=console, chat_export_path="~/Downloads/export.zip")
        output = buf.getvalue()
        assert "~/Downloads/export.zip" in output

    def test_preview_shows_none_when_no_chat_export(self):
        """preview_config shows 'none' for chat export when no path provided."""
        import io

        from rich.console import Console

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        preview_config(answers, console=console)
        output = buf.getvalue()
        assert "none" in output.lower()

    def test_phase_5_imports_chat_without_reprompt(self):
        """When chat path collected in Phase 2, Phase 5 calls import_chat_export."""
        import_mock = MagicMock(return_value={"chats_added": 5})
        mocks = run_wizard_mocked(
            collect_chat_export_path=MagicMock(return_value="/tmp/export.zip"),
            import_chat_export=import_mock,
        )
        import_mock.assert_called_once_with("/tmp/export.zip")

    def test_phase_5_skips_chat_when_no_path(self):
        """When no chat path collected, import_chat_export is not called."""
        import_mock = MagicMock(return_value={})
        mocks = run_wizard_mocked(
            collect_chat_export_path=MagicMock(return_value=None),
            import_chat_export=import_mock,
        )
        import_mock.assert_not_called()

    def test_collect_chat_export_path_returns_path(self, tmp_path):
        """User says yes and provides a valid path — returns expanded path."""
        export_file = tmp_path / "claude-export.zip"
        export_file.touch()
        with (
            patch(self.PREFIX + "Confirm.ask", return_value=True),
            patch(self.PREFIX + "Prompt.ask", return_value=str(export_file)),
            patch(self.PREFIX + "console"),
        ):
            result = collect_chat_export_path()
        assert result == str(export_file)

    def test_collect_chat_export_path_returns_none_on_decline(self):
        """User says no — returns None."""
        with patch(self.PREFIX + "Confirm.ask", return_value=False), patch(self.PREFIX + "console"):
            result = collect_chat_export_path()
        assert result is None

    def test_collect_chat_export_path_prints_sub_heading(self):
        """Chat export prompt displays a '3. Chat history' sub-heading."""
        with patch(self.PREFIX + "Confirm.ask", return_value=False), patch(self.PREFIX + "console") as mock_console:
            collect_chat_export_path()
        printed = " ".join(str(call) for call in mock_console.print.call_args_list)
        assert "3. Chat history" in printed

    def test_import_chat_export_returns_result(self, tmp_path):
        """import_chat_export calls ChatIndexer.upload and returns result dict."""
        export_file = tmp_path / "claude-export.zip"
        export_file.touch()
        expected = {"chats_added": 3, "messages_imported": 10}
        mock_indexer = MagicMock()
        mock_indexer.upload.return_value = expected
        with (
            patch("footprinter.ingest.chat_indexer.ChatIndexer", return_value=mock_indexer),
            patch("footprinter.ingest.database.Database"),
            patch(self.PREFIX + "get_db_path", return_value="/tmp/test.db"),
            patch(self.PREFIX + "console"),
        ):
            result = import_chat_export(str(export_file))
        assert result == expected
        mock_indexer.upload.assert_called_once()


# ---------------------------------------------------------------------------
# Strip Google from wizard, redirect to fp connect
# ---------------------------------------------------------------------------
class TestWizardGoogleRemoval:
    """Wizard should not mention Google or pass Google config to orchestrator."""

    def test_wizard_welcome_no_google_mention(self):
        """Welcome panel should NOT mention Google or OAuth."""
        with (
            patch("footprinter.cli.setup.console") as mock_console,
            patch("footprinter.cli.setup._choose_preset", return_value=None),
            patch("footprinter.cli.setup.collect_answers", side_effect=KeyboardInterrupt),
            patch("footprinter.cli.setup.Confirm"),
        ):
            try:
                run_interactive_wizard()
            except KeyboardInterrupt:
                pass
            parts = []
            for call in mock_console.print.call_args_list:
                for arg in call[0]:
                    if isinstance(arg, Panel):
                        parts.append(str(arg.renderable))
                    else:
                        parts.append(str(arg))
            text = " ".join(parts).lower()
        assert "google" not in text, f"Welcome panel still mentions Google: {text}"

    def test_wizard_orchestrator_no_drive_stages(self):
        """Orchestrator always gets google={} — wizard never passes Google config."""
        orchestrator_mock = MagicMock()
        mocks = run_wizard_mocked(
            run_orchestrator=orchestrator_mock,
        )
        orchestrator_mock.assert_called_once()
        call_kwargs = orchestrator_mock.call_args[1]
        google_arg = call_kwargs.get("google", {})
        assert google_arg == {}, f"Expected google={{}}, got google={google_arg}"

    def test_wizard_summary_mentions_fp_connect(self):
        """Summary hints should reference 'fp connect' for cloud integrations."""
        mock_console = MagicMock()
        with (
            patch("footprinter.cli.setup._get_indexing_counts") as mock_counts,
            patch("footprinter.cli.setup.console", mock_console),
        ):
            mock_counts.return_value = {"folders": 10, "files": 100}
            print_summary()
        printed = _extract_printed_text(mock_console)
        assert "fp connect" in printed, f"Summary should mention 'fp connect', got: {printed}"

    def test_setup_google_subcommand_rejected(self):
        """'fp setup google' should raise SystemExit (subcommand removed)."""
        with patch("sys.argv", ["fp", "google"]):
            with pytest.raises(SystemExit):
                main()


# ---------------------------------------------------------------------------
# TestInProcessPipeline — subprocess -> in-process migration
# ---------------------------------------------------------------------------
class TestRunWithLoggingErrorBehavior:
    """_run_with_logging should raise, not sys.exit."""

    def test_run_with_logging_raises_on_value_error(self):
        """_run_with_logging should re-raise ValueError, not call sys.exit."""
        from footprinter.cli.ingest import _run_with_logging

        mock_orch = MagicMock()
        mock_orch.run_pipes.side_effect = ValueError("bad stage")

        with pytest.raises(ValueError, match="bad stage"):
            _run_with_logging(
                mock_orch,
                pipes=["nonexistent"],
                mode="test",
                quiet=True,
            )


class TestInProcessPipeline:
    """Tests for in-process pipeline calls replacing subprocess.run."""

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_run_orchestrator_uses_in_process_pipeline(
        self,
        mock_console,
        mock_orch_cls,
        mock_rwl,
    ):
        """run_orchestrator should use DataPipelineOrchestrator + _run_with_logging."""
        run_orchestrator({"browsers": []})
        mock_orch_cls.assert_called_once()
        mock_rwl.assert_called_once()
        call_kwargs = mock_rwl.call_args[1]
        assert call_kwargs["pipes"] == ["local_folders", "local_files"]

    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_run_orchestrator_no_subprocess(
        self,
        mock_console,
        mock_orch_cls,
        mock_rwl,
    ):
        """run_orchestrator must not call subprocess.run."""
        with patch("footprinter.cli.setup.subprocess.run") as mock_sp:
            run_orchestrator({"browsers": []})
            mock_sp.assert_not_called()

    @patch("footprinter.connectors.is_installed", return_value=True)
    @_requires_google
    @patch("footprinter.cli.setup._run_with_logging")
    @patch("footprinter.cli.setup.DataPipelineOrchestrator")
    @patch("footprinter.cli.setup.console")
    def test_run_orchestrator_connector_stages(
        self,
        mock_console,
        mock_orch_cls,
        mock_rwl,
        _mock_inst,
    ):
        """run_orchestrator should include browser and connector stages."""
        run_orchestrator(
            {"browsers": ["safari"]},
            connector_results={"work": ["drive", "gmail"]},
        )
        call_kwargs = mock_rwl.call_args[1]
        stages = call_kwargs["pipes"]
        assert "browser" in stages
        assert "drive_folders" in stages
        assert "drive_files" in stages
        assert "gmail" in stages


# ---------------------------------------------------------------------------
# TestSWE866SetupSummaryOverhaul — summary display improvements
# ---------------------------------------------------------------------------
class TestSWE866SetupSummaryOverhaul:
    """Tests for setup summary overhaul."""

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_summary_includes_db_path(self, mock_console, mock_counts):
        """print_summary should show the database file path."""
        from rich.table import Table

        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        # Extract Table content from mock console calls
        table_texts = []
        for call_obj in mock_console.print.call_args_list:
            for arg in call_obj[0]:
                if isinstance(arg, Table):
                    for col in arg.columns:
                        for cell in col._cells:
                            table_texts.append(str(cell))
        table_text = " ".join(table_texts)
        assert "footprinter.db" in table_text

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_no_fda_hint_in_summary(self, mock_console, mock_counts):
        """print_summary should NOT mention Full Disk Access."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "Full Disk Access" not in printed

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_no_mcp_write_hint_in_summary(self, mock_console, mock_counts):
        """print_summary should NOT mention fp setup mcp --write, even when not configured."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary(mcp_configured=False)
        printed = _extract_printed_text(mock_console)
        assert "fp setup mcp --write" not in printed

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_summary_shows_getting_started(self, mock_console, mock_counts):
        """print_summary should show a Getting started section with common commands."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "fp search" in printed
        assert "fp ingest status" in printed
        assert "fp -h" in printed or "fp <command> --help" in printed

    @patch("footprinter.cli.setup._get_indexing_counts")
    @patch("footprinter.cli.setup.console")
    def test_no_additional_setup_panel(self, mock_console, mock_counts):
        """print_summary should NOT show an 'Additional setup' panel."""
        mock_counts.return_value = {"folders": 10, "files": 100}
        print_summary()
        printed = _extract_printed_text(mock_console)
        assert "Additional setup" not in printed

    @patch("footprinter.ingest.status.print_results")
    @patch("footprinter.ingest.run_record.save_run_record")
    @patch("footprinter.utils.logging_config.add_file_handler")
    @patch("footprinter.paths.prune_run_logs")
    @patch("footprinter.paths.get_run_logs_dir")
    def test_populate_no_next_steps_in_setup(
        self,
        mock_logs_dir,
        mock_prune,
        mock_fh,
        mock_record,
        mock_print_results,
    ):
        """_run_with_logging with show_next_steps=False should pass it to print_results."""
        from pathlib import Path

        from footprinter.cli.ingest import _run_with_logging

        mock_logs_dir.return_value = Path("/tmp/fp-test-logs")
        mock_record.return_value = Path("/tmp/fp-test-record.json")

        mock_orch = MagicMock()
        mock_orch.run_pipes.return_value = None

        _run_with_logging(
            mock_orch,
            pipes=["local_folders"],
            mode="test",
            show_next_steps=False,
        )
        mock_print_results.assert_called_once()
        call_kwargs = mock_print_results.call_args
        # show_next_steps should be passed through
        assert call_kwargs[1].get("show_next_steps") is False or (
            len(call_kwargs[0]) > 2 and call_kwargs[0][2] is False
        )


# ---------------------------------------------------------------------------
# TestSemanticOptIn — semantic search wizard flow
# ---------------------------------------------------------------------------
class TestSemanticOptIn:
    """Tests for the semantic config written via generate_config()."""

    def test_semantic_config_written(self):
        """generate_config() includes semantic section when semantic answers provided."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        semantic = {"file_vectorization": True, "chat_vectorization": False}
        config = generate_config(answers, semantic=semantic)
        assert config["semantic"]["file_vectorization"] is True
        assert config["semantic"]["chat_vectorization"] is False


# ---------------------------------------------------------------------------
# Dead-code regression: PRESETS dict removed
# ---------------------------------------------------------------------------
class TestPresetsRemoved:
    """PRESETS dict was dead code — ensure it stays gone."""

    def test_presets_not_in_module(self):
        """PRESETS should not be exported from the setup module."""
        from footprinter.cli import setup

        assert "PRESETS" not in vars(setup), "PRESETS dict has been re-introduced in footprinter.cli.setup"


# ---------------------------------------------------------------------------
# Config preservation tests
# ---------------------------------------------------------------------------
class TestConfigPreservation:
    """generate_config(answers, existing=...) preserves untouched sections."""

    def _existing_config(self):
        """Return a realistic existing config with all sections populated."""
        return {
            "directories": ["~/Work", "~/Personal"],
            "browsers": ["safari", "chrome"],
            "google_drive": {
                "enabled": True,
                "accounts": [
                    {
                        "name": "personal",
                        "root_folder_id": "abc123",
                        "token_path": "~/.footprinter/tokens/personal.json",
                    },
                ],
            },
            "gmail": {
                "enabled": True,
                "accounts": [
                    {"name": "personal", "token_path": "~/.footprinter/tokens/personal_gmail.json"},
                ],
            },
            "source_seeds": [
                {
                    "name": "gdrive_personal",
                    "source_type": "remote",
                    "account": "personal",
                    "label": "Drive (personal)",
                    "icon": "cloud",
                    "enabled": True,
                },
            ],
            "exclusions": {"patterns": ["*.pyc", "__pycache__", "node_modules"]},
            "semantic": {"file_vectorization": True, "chat_vectorization": False},
            "domain": {"labels": {"consulting": ["~/Work/clients"]}},
        }

    def test_generate_config_existing_preserves_google_drive(self):
        """Existing google_drive section survives when not overridden."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        assert result["google_drive"]["enabled"] is True
        assert len(result["google_drive"]["accounts"]) == 1

    def test_generate_config_existing_preserves_gmail(self):
        """Existing gmail section survives when not overridden."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        assert result["gmail"]["enabled"] is True
        assert len(result["gmail"]["accounts"]) == 1

    def test_generate_config_existing_preserves_source_seeds(self):
        """Existing source_seeds with drive entries survive."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        seeds = result.get("source_seeds", [])
        remote_seeds = [s for s in seeds if s.get("source_type") == "remote"]
        assert len(remote_seeds) >= 1

    def test_generate_config_existing_partial_seeds_keeps_template_seeds(self):
        """Template seeds survive even when existing config has a subset."""
        existing = {
            "directories": ["~/Work"],
            "browsers": ["safari"],
            "source_seeds": [
                {
                    "name": "gdrive_personal",
                    "source_type": "remote",
                    "account": "personal",
                    "label": "Drive",
                    "icon": "cloud",
                    "enabled": True,
                },
            ],
        }
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        seeds = result.get("source_seeds", [])
        seed_names = {s["name"] for s in seeds}
        # Template seeds (local, browser, email) must survive
        assert "local" in seed_names, f"Template seed 'local' missing: {seed_names}"
        assert "browser" in seed_names, f"Template seed 'browser' missing: {seed_names}"
        assert "email" in seed_names, f"Template seed 'email' missing: {seed_names}"
        # Existing seed must also survive
        assert "gdrive_personal" in seed_names

    def test_generate_config_existing_preserves_exclusions(self):
        """Custom exclusion patterns survive."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        assert "node_modules" in result["exclusions"]["patterns"]

    def test_generate_config_existing_preserves_semantic(self):
        """Semantic settings survive when not overridden by semantic arg."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        assert result["semantic"]["file_vectorization"] is True

    def test_generate_config_existing_preserves_domain(self):
        """Domain labels survive."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        assert "domain" in result
        assert "labels" in result["domain"]

    def test_generate_config_existing_overwrites_directories(self):
        """New directories from answers replace existing."""
        existing = self._existing_config()
        answers = {"directories": ["~/NewDir"], "browsers": ["safari"]}
        result = generate_config(answers, existing=existing)
        assert result["directories"] == ["~/NewDir"]

    def test_generate_config_existing_overwrites_browsers(self):
        """New browsers from answers replace existing."""
        existing = self._existing_config()
        answers = {"directories": ["~/Work"], "browsers": ["chrome"]}
        result = generate_config(answers, existing=existing)
        assert result["browsers"] == ["chrome"]

    def test_generate_config_existing_none_uses_template(self):
        """existing=None gives identical behavior to current code."""
        answers = {"directories": ["~/Work"], "browsers": ["safari"]}
        result_no_existing = generate_config(answers, existing=None)
        result_default = generate_config(answers)
        assert result_no_existing == result_default

    def test_generate_config_existing_not_mutated(self):
        """Input existing dict is not mutated."""
        import copy

        existing = self._existing_config()
        original = copy.deepcopy(existing)
        answers = {"directories": ["~/NewDir"], "browsers": ["chrome"]}
        generate_config(answers, existing=existing)
        assert existing == original


# ---------------------------------------------------------------------------
# Wizard reconfigure-mode tests
# ---------------------------------------------------------------------------
class TestWizardReconfigureMode:
    """Wizard detects existing config and passes it through."""

    def test_wizard_detects_existing_config(self):
        """When get_config() returns a dict, generate_config is called with existing= set."""
        existing = {"directories": ["~/Work"], "browsers": ["safari"]}
        mocks = run_wizard_mocked(
            _load_existing_config=MagicMock(return_value=existing),
        )
        gen_call = mocks["generate_config"].call_args
        assert gen_call[1].get("existing") == existing

    def test_wizard_fresh_install_uses_template(self):
        """When _load_existing_config returns None, generate_config has existing=None."""
        mocks = run_wizard_mocked(
            _load_existing_config=MagicMock(return_value=None),
        )
        gen_call = mocks["generate_config"].call_args
        assert gen_call[1].get("existing") is None

    def test_wizard_cancel_preserves_config(self):
        """Cancel at Phase 3 does not call write_config."""
        confirm_responses = iter([False])  # decline "Write this configuration?"
        mocks = run_wizard_mocked(
            **{"Confirm.ask": lambda prompt, **kw: next(confirm_responses)},
        )
        mocks["write_config"].assert_not_called()


# ---------------------------------------------------------------------------
# collect_answers with existing config
# ---------------------------------------------------------------------------
class TestCollectAnswersWithExisting:
    """collect_answers(existing=...) shows current values and offers to keep them."""

    @patch("footprinter.cli.setup.os.path.isdir", return_value=True)
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_keep_current_directories(self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir):
        """User says Yes to 'Keep current directories?', existing dirs are returned."""
        existing = {"directories": ["~/Work", "~/Personal"], "browsers": ["safari"]}
        # Confirm.ask calls:
        # 1. Keep current directories? -> Yes
        # 2. Add another directory? prompt -> blank (finish)
        # 3-4. Keep current browsers? -> Yes
        mock_confirm.side_effect = [True, True]
        mock_prompt.side_effect = [""]  # blank to finish adding more dirs
        answers = collect_answers(existing=existing)
        assert "~/Work" in answers["directories"]
        assert "~/Personal" in answers["directories"]

    @patch("footprinter.cli.setup.os.path.isdir")
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_change_directories(self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir):
        """User says No to keep, enters new dirs via prompt."""
        existing = {"directories": ["~/Work"], "browsers": []}
        mock_isdir.side_effect = lambda p: p in ("/tmp", "~/Work")
        # Confirm.ask calls:
        # 1. Keep current directories? -> No
        # 2-3. Include safari/chrome? -> No, No
        mock_confirm.side_effect = [False, False, False]
        # Prompt.ask calls (directory input):
        # 1. "/tmp" (new dir)
        # 2. "" (finish)
        mock_prompt.side_effect = ["/tmp", ""]
        answers = collect_answers(existing=existing)
        assert "/tmp" in answers["directories"]
        assert "~/Work" not in answers["directories"]

    @patch("footprinter.cli.setup.os.path.isdir", return_value=True)
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_keep_current_browsers(self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir):
        """User keeps current browser selection."""
        existing = {"directories": ["~/Work"], "browsers": ["safari", "chrome"]}
        # Confirm.ask calls:
        # 1. Keep current directories? -> Yes
        # 2. Keep current browsers? -> Yes
        mock_confirm.side_effect = [True, True]
        mock_prompt.side_effect = [""]  # blank to finish adding more dirs
        answers = collect_answers(existing=existing)
        assert answers["browsers"] == ["safari", "chrome"]

    @patch("footprinter.cli.setup.os.path.isdir")
    @patch("footprinter.cli.setup.os.path.expanduser", side_effect=lambda p: p)
    @patch("footprinter.cli.setup.Prompt.ask")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    def test_no_existing_unchanged(self, mock_console, mock_confirm, mock_prompt, mock_expanduser, mock_isdir):
        """existing=None gives identical behavior to current tests."""
        mock_isdir.side_effect = lambda p: p == "/tmp"
        mock_prompt.side_effect = ["/tmp", ""]
        mock_confirm.side_effect = [False, False]  # No browsers
        answers = collect_answers(existing=None)
        assert "/tmp" in answers["directories"]


# ---------------------------------------------------------------------------
# Vectorization wizard step
# ---------------------------------------------------------------------------


class TestVectorizationConstants:
    """Verify vectorization constants exist with expected values."""

    def test_default_file_types(self):
        from footprinter.cli.setup import DEFAULT_FILE_TYPES

        assert DEFAULT_FILE_TYPES == [".md", ".txt", ".pdf", ".docx"]

    def test_known_junk_patterns_is_list_of_tuples(self):
        from footprinter.cli.setup import KNOWN_JUNK_PATTERNS

        assert isinstance(KNOWN_JUNK_PATTERNS, list)
        assert len(KNOWN_JUNK_PATTERNS) >= 9  # at least 9 known patterns
        for item in KNOWN_JUNK_PATTERNS:
            assert isinstance(item, tuple) and len(item) == 2
            assert isinstance(item[0], str)  # fnmatch pattern
            assert isinstance(item[1], str)  # description

    def test_scan_file_limit(self):
        from footprinter.cli.setup import _SCAN_FILE_LIMIT

        assert _SCAN_FILE_LIMIT == 50_000


class TestScanDirectoriesForVectorization:
    """Tests for _scan_directories_for_vectorization."""

    def test_counts_matching_extensions(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        (tmp_path / "readme.md").write_text("hello")
        (tmp_path / "notes.txt").write_text("world")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".md", ".txt"])
        assert result["total"] == 2
        assert result["by_extension"][".md"] == 1
        assert result["by_extension"][".txt"] == 1

    def test_skips_non_matching_extensions(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        (tmp_path / "image.png").write_text("binary")
        (tmp_path / "code.py").write_text("print()")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".md", ".txt"])
        assert result["total"] == 0

    def test_empty_directory(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        result = _scan_directories_for_vectorization([str(tmp_path)], [".md", ".txt"])
        assert result["total"] == 0
        assert result["by_extension"] == {}

    def test_skips_nonexistent_directory(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        fake = str(tmp_path / "does_not_exist")
        result = _scan_directories_for_vectorization([fake], [".md"])
        assert result["total"] == 0

    def test_expands_tilde_in_paths(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        (tmp_path / "doc.md").write_text("content")
        with patch("os.path.expanduser", return_value=str(tmp_path)):
            result = _scan_directories_for_vectorization(["~/fake"], [".md"])
        assert result["total"] == 1

    def test_recurses_subdirectories(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.md").write_text("deep")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".md"])
        assert result["total"] == 1

    def test_multiple_directories(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "one.md").write_text("a")
        (dir_b / "two.md").write_text("b")
        result = _scan_directories_for_vectorization([str(dir_a), str(dir_b)], [".md"])
        assert result["total"] == 2

    def test_truncated_when_exceeding_limit(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        # Create a few files, patch limit to 2
        for i in range(5):
            (tmp_path / f"file{i}.md").write_text(f"content {i}")
        with patch("footprinter.cli.setup._SCAN_FILE_LIMIT", 2):
            result = _scan_directories_for_vectorization([str(tmp_path)], [".md"])
        assert result["truncated"] is True
        assert result["total"] == 2  # stopped at limit

    def test_does_not_follow_symlinks(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "doc.md").write_text("real content")

        external = tmp_path / "external"
        external.mkdir()
        (external / "secret.md").write_text("behind symlink")

        (real_dir / "link").symlink_to(external)

        result = _scan_directories_for_vectorization([str(real_dir)], [".md"])
        assert result["total"] == 1

    def test_skips_symlinked_root_directory(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        target = tmp_path / "target"
        target.mkdir()
        (target / "file.md").write_text("content")

        link = tmp_path / "link_root"
        link.symlink_to(target)

        result = _scan_directories_for_vectorization([str(link)], [".md"])
        assert result["total"] == 0


class TestScanJunkDetection:
    """Tests for junk pattern detection in the scanner."""

    def test_detects_claude_debug_junk(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        junk_dir = tmp_path / ".claude" / "debug"
        junk_dir.mkdir(parents=True)
        (junk_dir / "log.txt").write_text("debug output")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".txt"])
        assert result["total"] == 1
        assert len(result["junk_hits"]) > 0
        # At least one pattern should have matched
        assert sum(result["junk_hits"].values()) == 1

    def test_total_after_exclusions(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        # One real file, one junk file
        (tmp_path / "real.txt").write_text("real content")
        junk_dir = tmp_path / ".claude" / "debug"
        junk_dir.mkdir(parents=True)
        (junk_dir / "log.txt").write_text("debug junk")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".txt"])
        assert result["total"] == 2
        assert result["total_after_exclusions"] == 1

    def test_no_junk_hits_when_clean(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        (tmp_path / "clean.md").write_text("clean content")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".md"])
        assert result["junk_hits"] == {}

    def test_detects_photos_library(self, tmp_path):
        from footprinter.cli.setup import _scan_directories_for_vectorization

        photos = tmp_path / "Photos Library.photoslibrary" / "resources"
        photos.mkdir(parents=True)
        (photos / "index.txt").write_text("spotlight data")
        result = _scan_directories_for_vectorization([str(tmp_path)], [".txt"])
        assert len(result["junk_hits"]) > 0
        assert sum(result["junk_hits"].values()) == 1


class TestCollectVectorizationQuick:
    """Tests for collect_vectorization_answers in quick mode."""

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_quick_accept_returns_full_dict(self, mock_confirm, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        junk = d / ".claude" / "debug"
        junk.mkdir(parents=True)
        (junk / "log.txt").write_text("junk")
        (d / "real.md").write_text("real")

        result = collect_vectorization_answers(directories=[str(d)], quick=True)
        assert result["file_vectorization"] is True
        assert result["chat_vectorization"] is True
        assert result["file_types"] == [".md", ".txt", ".pdf", ".docx"]
        assert isinstance(result["exclude_patterns"], list)
        # Should include the detected junk exclusion
        assert len(result["exclude_patterns"]) > 0

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.Confirm.ask", return_value=False)
    def test_quick_decline_returns_disabled(self, mock_confirm, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        result = collect_vectorization_answers(directories=[str(d)], quick=True)
        assert result["file_vectorization"] is False
        assert result["chat_vectorization"] is False

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_quick_no_junk_still_valid(self, mock_confirm, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "clean"
        d.mkdir()
        (d / "doc.md").write_text("content")
        result = collect_vectorization_answers(directories=[str(d)], quick=True)
        assert result["file_vectorization"] is True
        assert result["exclude_patterns"] == []


class TestCollectVectorizationFull:
    """Tests for collect_vectorization_answers in full mode."""

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_full_defaults_accepted(self, mock_confirm, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        junk = d / ".claude" / "debug"
        junk.mkdir(parents=True)
        (junk / "log.txt").write_text("junk")
        (d / "real.md").write_text("real")

        result = collect_vectorization_answers(directories=[str(d)])
        assert result["file_types"] == [".md", ".txt", ".pdf", ".docx"]
        assert isinstance(result["exclude_patterns"], list)
        assert result["file_vectorization"] is True

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_full_custom_file_types(self, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        (d / "code.py").write_text("print()")

        # Confirm.ask order (enable-first):
        # snippets? No, enable files? Yes, enable chats? Yes,
        # keep defaults? No, accept exclusions? Yes
        # Prompt.ask: custom extensions
        with patch("footprinter.cli.setup.Confirm.ask", side_effect=[False, True, True, False, True]):
            with patch("footprinter.cli.setup.Prompt.ask", return_value=".py, .rs"):
                result = collect_vectorization_answers(directories=[str(d)])

        assert ".py" in result["file_types"]
        assert ".rs" in result["file_types"]

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_full_decline_bulk_exclusions_toggle_individually(self, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        junk1 = d / ".claude" / "debug"
        junk1.mkdir(parents=True)
        (junk1 / "log.txt").write_text("junk1")
        junk2 = d / ".cci"
        junk2.mkdir()
        (junk2 / "cache.txt").write_text("junk2")

        # Confirm.ask order (enable-first):
        # snippets? No, enable files? Yes, enable chats? Yes,
        # keep defaults? Yes, accept bulk exclusions? No,
        # then individual toggles (True for first, False for second)
        with patch(
            "footprinter.cli.setup.Confirm.ask",
            side_effect=[False, True, True, True, False, True, False],
        ):
            result = collect_vectorization_answers(directories=[str(d)])

        # Should have some exclusions but not all
        assert isinstance(result["exclude_patterns"], list)


# ---------------------------------------------------------------------------
# TestContentSnippetsDefault — flip default to ON with security language
# ---------------------------------------------------------------------------
class TestContentSnippetsDefault:
    """Content snippets prompt should default to ON for fresh installs and
    explain the local-only security posture."""

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_content_snippets_default_on_fresh_install(self, mock_importable, mock_console, tmp_path):
        """Fresh install: snippet Confirm.ask is invoked with default=True."""
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        seen_defaults = []

        def capture(prompt, **kwargs):
            if "snippet" in prompt.lower():
                seen_defaults.append(kwargs.get("default"))
            return False  # decline everything else to keep flow short

        with patch("footprinter.cli.setup.Confirm.ask", side_effect=capture):
            collect_vectorization_answers(directories=[str(d)])

        assert seen_defaults == [True], (
            f"Expected snippet prompt with default=True on fresh install; got {seen_defaults}"
        )

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_content_snippets_existing_choice_preserved(self, mock_importable, mock_console, tmp_path):
        """Reconfigure: existing choice (False) is preserved as the default."""
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        seen_defaults = []
        existing = {"indexing": {"content_snippets": False}}

        def capture(prompt, **kwargs):
            if "snippet" in prompt.lower():
                seen_defaults.append(kwargs.get("default"))
            return False

        with patch("footprinter.cli.setup.Confirm.ask", side_effect=capture):
            collect_vectorization_answers(directories=[str(d)], existing=existing)

        assert seen_defaults == [False], (
            f"Expected snippet prompt with default=False (existing choice); got {seen_defaults}"
        )

    @patch("footprinter.cli.setup._is_importable", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=False)
    def test_snippets_prompt_uses_local_only_security_language(self, mock_confirm, mock_importable, tmp_path):
        """The snippet prompt should explain content stays local on the user's machine."""
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        mock_console = MagicMock()
        with patch("footprinter.cli.setup.console", mock_console):
            collect_vectorization_answers(directories=[str(d)])

        printed = _extract_printed_text(mock_console).lower()
        assert "local" in printed, "Snippets language should mention 'local'"
        assert "your machine" in printed or "on your" in printed, (
            "Snippets language should reassure the data stays on the user's machine"
        )
        # Reassurance that nothing escapes without permission
        assert (
            "never shared" in printed
            or "never exposed" in printed
            or "explicit permission" in printed
            or "without your permission" in printed
        ), "Snippets language should state nothing leaves without explicit permission"
        assert "stores" in printed or "stored copy" in printed or "preview" in printed, (
            "Snippets language should disclose that file content is stored"
        )


# ---------------------------------------------------------------------------
# TestSemanticEnableFirst — full mode asks enable before file types/exclusions
# ---------------------------------------------------------------------------
class TestSemanticEnableFirst:
    """Full-mode semantic search must ask the enable decision before any
    file-type or exclusion configuration."""

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_full_mode_asks_enable_before_file_types_when_declined(
        self, mock_importable, mock_console, tmp_path
    ):
        """Decline path: only the enable Confirm runs — no file-type or exclusion prompts."""
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        (d / "doc.md").write_text("content")
        # Add a junk hit so we can prove exclusion prompts are skipped on decline
        junk = d / ".claude" / "debug"
        junk.mkdir(parents=True)
        (junk / "log.txt").write_text("junk")

        prompts = []

        def capture(prompt, **kwargs):
            prompts.append(prompt)
            # snippets=No, enable files=No, enable chats=No
            return False

        with patch("footprinter.cli.setup.Confirm.ask", side_effect=capture):
            with patch("footprinter.cli.setup.Prompt.ask") as mock_prompt:
                collect_vectorization_answers(directories=[str(d)])
                mock_prompt.assert_not_called()

        joined = " | ".join(prompts).lower()
        # Snippets prompt + 2 enable prompts + nothing else.
        # Most importantly, no file-type or exclusion prompts after decline.
        assert "file types" not in joined, f"file-type prompt shown after decline: {prompts}"
        assert "exclude" not in joined and "exclusions" not in joined, (
            f"exclusion prompt shown after decline: {prompts}"
        )

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_full_mode_asks_details_when_enabled(
        self, mock_importable, mock_console, tmp_path
    ):
        """Enable path: file-type and exclusion prompts DO appear."""
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        (d / "doc.md").write_text("content")
        junk = d / ".claude" / "debug"
        junk.mkdir(parents=True)
        (junk / "log.txt").write_text("junk")

        prompts = []

        def capture(prompt, **kwargs):
            prompts.append(prompt)
            # snippets=No, enable files=Yes, enable chats=Yes,
            # keep file types=Yes, accept bulk exclusions=Yes
            return prompt.lower().startswith(("  enable", "  keep", "  accept"))

        with patch("footprinter.cli.setup.Confirm.ask", side_effect=capture):
            collect_vectorization_answers(directories=[str(d)])

        joined = " | ".join(prompts).lower()
        assert "enable semantic search for files" in joined
        assert "enable semantic search for chats" in joined
        assert "file types" in joined or "keep these file types" in joined
        # Find positions to verify enable came first
        first_enable = next(
            (i for i, p in enumerate(prompts) if "enable semantic search for files" in p.lower()), -1
        )
        first_filetypes = next(
            (i for i, p in enumerate(prompts) if "file types" in p.lower() or "keep these file types" in p.lower()),
            -1,
        )
        assert first_enable < first_filetypes, (
            f"enable prompt ({first_enable}) must come before file-types prompt ({first_filetypes}); got {prompts}"
        )


class TestCollectVectorizationExisting:
    """Tests for collect_vectorization_answers with existing config."""

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_existing_file_types_used_as_default(self, mock_confirm, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        existing = {"vectorization": {"file_types": [".py", ".md"]}}
        result = collect_vectorization_answers(directories=[str(d)], existing=existing)
        assert result["file_types"] == [".py", ".md"]

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_existing_exclude_patterns_preserved(self, mock_confirm, mock_importable, mock_console, tmp_path):
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        existing = {
            "vectorization": {
                "exclude_patterns": ["**/custom/**"],
            }
        }
        result = collect_vectorization_answers(directories=[str(d)], existing=existing)
        assert "**/custom/**" in result["exclude_patterns"]


class TestCheckSemanticDeps:
    """Tests for _check_semantic_deps pip install paths."""

    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup._is_importable", return_value=True)
    def test_deps_already_installed(self, mock_importable, mock_run):
        result = _check_semantic_deps()
        assert result is True
        mock_run.assert_not_called()
        assert mock_importable.call_count == 2
        mock_importable.assert_any_call("chromadb")
        mock_importable.assert_any_call("onnxruntime")

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    @patch("footprinter.cli.setup._is_importable", return_value=False)
    def test_deps_missing_user_accepts_install_succeeds(self, mock_importable, mock_confirm, mock_run, mock_console):
        mock_run.return_value = MagicMock(returncode=0)
        result = _check_semantic_deps()
        assert result is True
        mock_run.assert_called_once_with(
            [sys.executable, "-m", "pip", "install", "footprinter-cli[semantic]"],
            capture_output=True,
            text=True,
        )
        print_args = [call.args[0] for call in mock_console.print.call_args_list]
        assert any("requires chromadb and onnxruntime" in arg for arg in print_args)
        assert any("Semantic dependencies installed" in arg for arg in print_args)

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    @patch("footprinter.cli.setup._is_importable", return_value=False)
    def test_deps_missing_user_accepts_install_fails(self, mock_importable, mock_confirm, mock_run, mock_console):
        mock_run.return_value = MagicMock(returncode=1, stderr="pkg build failed")
        result = _check_semantic_deps()
        assert result is False
        print_args = [call.args[0] for call in mock_console.print.call_args_list]
        assert any("requires chromadb and onnxruntime" in arg for arg in print_args)
        assert any("Install failed" in arg and "pkg build failed" in arg for arg in print_args)
        assert any("enable semantic search later" in arg for arg in print_args)

    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask", return_value=False)
    @patch("footprinter.cli.setup._is_importable", return_value=False)
    def test_deps_missing_user_declines(self, mock_importable, mock_confirm, mock_run, mock_console):
        result = _check_semantic_deps()
        assert result is False
        mock_run.assert_not_called()
        print_args = [call.args[0] for call in mock_console.print.call_args_list]
        assert any("requires chromadb and onnxruntime" in arg for arg in print_args)
        assert any("enable semantic search later" in arg for arg in print_args)


class TestGenerateConfigVectorization:
    """Tests for vectorization keys in generate_config()."""

    def test_file_types_applied(self):
        answers = {"directories": ["~/Work"], "browsers": []}
        semantic = {
            "file_vectorization": True,
            "chat_vectorization": False,
            "file_types": [".md", ".txt"],
            "exclude_patterns": [],
        }
        config = generate_config(answers, semantic=semantic)
        assert config["vectorization"]["file_types"] == [".md", ".txt"]

    def test_exclude_patterns_applied(self):
        answers = {"directories": ["~/Work"], "browsers": []}
        semantic = {
            "file_vectorization": True,
            "chat_vectorization": False,
            "file_types": [".md"],
            "exclude_patterns": ["**/.claude/debug/**"],
        }
        config = generate_config(answers, semantic=semantic)
        assert config["vectorization"]["exclude_patterns"] == ["**/.claude/debug/**"]

    def test_template_defaults_when_no_vectorization_keys(self):
        answers = {"directories": ["~/Work"], "browsers": []}
        semantic = {
            "file_vectorization": True,
            "chat_vectorization": False,
        }
        config = generate_config(answers, semantic=semantic)
        # Template defaults from config.example.yaml should survive
        assert "vectorization" in config
        assert config["vectorization"]["file_types"] == [".md", ".txt", ".pdf", ".docx"]

    def test_semantic_none_leaves_vectorization_untouched(self):
        answers = {"directories": ["~/Work"], "browsers": []}
        config = generate_config(answers, semantic=None)
        # Template vectorization section should still be present
        assert "vectorization" in config
        assert config["vectorization"]["file_types"] == [".md", ".txt", ".pdf", ".docx"]


class TestPreviewConfigVectorization:
    """Tests for vectorization display in preview_config()."""

    def test_preview_shows_file_types_and_exclusions(self):
        from rich.console import Console

        mock_console = MagicMock(spec=Console)
        answers = {"directories": ["~/Work"], "browsers": []}
        semantic = {
            "file_vectorization": True,
            "chat_vectorization": False,
            "file_types": [".md", ".txt", ".pdf", ".docx"],
            "exclude_patterns": ["**/.claude/debug/**", "**/.cci/**", "**/Photos Library.photoslibrary/**"],
        }
        preview_config(answers, console=mock_console, semantic=semantic)
        printed = _extract_printed_text(mock_console)
        assert ".md" in printed
        assert "3" in printed  # 3 exclusion patterns

    def test_preview_shows_disabled(self):
        from rich.console import Console

        mock_console = MagicMock(spec=Console)
        answers = {"directories": ["~/Work"], "browsers": []}
        semantic = {
            "file_vectorization": False,
            "chat_vectorization": False,
        }
        preview_config(answers, console=mock_console, semantic=semantic)
        printed = _extract_printed_text(mock_console)
        assert "disabled" in printed.lower()


class TestWizardVectorizationIntegration:
    """Tests for vectorization integration in run_interactive_wizard."""

    @patch("footprinter.cli.setup.print_summary")
    @patch("footprinter.cli.setup.offer_setup_claude", return_value=False)
    @patch("footprinter.cli.setup.seed_access_policies")
    @patch("footprinter.cli.setup._offer_csv_import_wizard")
    @patch("footprinter.cli.setup.run_orchestrator")
    @patch("footprinter.cli.setup.get_log_path")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.write_config")
    @patch("footprinter.cli.setup.generate_config")
    @patch("footprinter.cli.setup.preview_config")
    @patch("footprinter.cli.setup.collect_vectorization_answers")
    @patch("footprinter.cli.setup._choose_preset")
    @patch("footprinter.cli.setup._load_existing_config", return_value=None)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_quick_preset_calls_with_quick_true(
        self,
        mock_confirm,
        mock_load,
        mock_preset,
        mock_vec,
        mock_preview,
        mock_gen,
        mock_write,
        mock_console,
        mock_log,
        mock_orch,
        mock_csv,
        mock_seed,
        mock_claude,
        mock_summary,
        tmp_path,
    ):
        mock_preset.return_value = {
            "directories": ["~/Work"],
            "browsers": ["safari"],
        }
        mock_vec.return_value = {
            "file_vectorization": True,
            "chat_vectorization": True,
            "file_types": [".md"],
            "exclude_patterns": [],
        }
        mock_gen.return_value = {}
        mock_log.return_value = tmp_path / "setup.log"

        run_interactive_wizard()

        mock_vec.assert_called_once()
        call_kwargs = mock_vec.call_args
        assert call_kwargs[1].get("quick") is True

    @patch("footprinter.cli.setup.print_summary")
    @patch("footprinter.cli.setup.offer_setup_claude", return_value=False)
    @patch("footprinter.cli.setup.seed_access_policies")
    @patch("footprinter.cli.setup._offer_csv_import_wizard")
    @patch("footprinter.cli.setup.run_orchestrator")
    @patch("footprinter.cli.setup.get_log_path")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.write_config")
    @patch("footprinter.cli.setup.generate_config")
    @patch("footprinter.cli.setup.preview_config")
    @patch("footprinter.cli.setup.collect_vectorization_answers")
    @patch("footprinter.cli.setup.collect_chat_export_path", return_value=None)
    @patch("footprinter.cli.setup.collect_answers")
    @patch("footprinter.cli.setup._choose_preset", return_value=None)
    @patch("footprinter.cli.setup._load_existing_config", return_value=None)
    @patch("footprinter.cli.setup.Confirm.ask", return_value=True)
    def test_full_setup_calls_with_quick_false(
        self,
        mock_confirm,
        mock_load,
        mock_preset,
        mock_collect,
        mock_chat,
        mock_vec,
        mock_preview,
        mock_gen,
        mock_write,
        mock_console,
        mock_log,
        mock_orch,
        mock_csv,
        mock_seed,
        mock_claude,
        mock_summary,
        tmp_path,
    ):
        mock_collect.return_value = {
            "directories": ["~/Work"],
            "browsers": [],
        }
        mock_vec.return_value = {
            "file_vectorization": False,
            "chat_vectorization": False,
        }
        mock_gen.return_value = {}
        mock_log.return_value = tmp_path / "setup.log"

        run_interactive_wizard()

        mock_vec.assert_called_once()
        call_kwargs = mock_vec.call_args
        # Full mode: quick should be False or not provided (default)
        assert call_kwargs[1].get("quick", False) is False


class TestVectorizationCancellation:
    """Test that PromptCancelled propagates from collect_vectorization_answers."""

    @patch("footprinter.cli.setup.console")
    def test_prompt_cancelled_propagates(self, mock_console, tmp_path):
        from footprinter.cli._prompt import PromptCancelled
        from footprinter.cli.setup import collect_vectorization_answers

        d = tmp_path / "work"
        d.mkdir()
        with patch(
            "footprinter.cli.setup.Confirm.ask",
            side_effect=PromptCancelled("Escape"),
        ):
            with pytest.raises(PromptCancelled):
                collect_vectorization_answers(directories=[str(d)])


class TestOrchestratorContaminationGuard:
    """Regression guard: orchestrator imports must not contaminate wizard tests.

    The contamination bug (fixed in ed171f5) occurred when orchestrator module
    imports left side-effects that caused run_wizard_mocked() to miss patches,
    leading to real functions attempting stdin reads under pytest.
    """

    def test_wizard_passes_after_orchestrator_imports(self):
        """Importing orchestrator modules must not break run_wizard_mocked()."""
        # These imports mirror what test_orchestrator.py does at test time.
        # If any mock target in run_wizard_mocked() is stale or missing,
        # this will hang (real function tries stdin) or error.
        from footprinter.ingest.orchestrator import DataPipelineOrchestrator  # noqa: F401
        from footprinter.ingest.registry import (  # noqa: F401
            CORE_PIPES,
            get_all_pipes,
            get_pipelines,
        )

        mocks = run_wizard_mocked()
        mocks["write_config"].assert_called_once()

    def test_run_wizard_mocked_patches_are_valid(self):
        """Every mock target in run_wizard_mocked() must exist in footprinter.cli.setup."""
        import inspect
        import re

        import footprinter.cli.setup as setup_module

        source = inspect.getsource(run_wizard_mocked)
        # Match keys like "key_name": MagicMock or "Confirm.ask": MagicMock
        keys = re.findall(r'"([^"]+)":\s*MagicMock', source)
        assert keys, "Could not extract any mock targets from run_wizard_mocked()"

        # console and Confirm.ask are special — not simple module attributes
        skip = {"console", "Confirm.ask"}

        for key in keys:
            if key in skip:
                continue
            assert hasattr(setup_module, key), (
                f"Mock target '{key}' in run_wizard_mocked() does not exist "
                f"in footprinter.cli.setup — stale patch target?"
            )


# ---------------------------------------------------------------------------
# TestSafariFullDiskAccessGuidance
# ---------------------------------------------------------------------------
@_requires_darwin
class TestFDATimingAfterAllBrowsers:
    """FDA guidance must fire once after all browser selections are collected,
    not mid-loop between Safari and Chrome confirmations."""

    @patch("footprinter.cli.setup._guide_safari_full_disk_access")
    @patch("footprinter.cli.setup.get_available_browsers", return_value=["safari", "chrome"])
    def test_fda_fires_once_after_all_browsers_selected(self, mock_browsers, mock_guide):
        """FDA helper runs after the final per-browser Confirm.ask, not between them."""
        from footprinter.cli.setup import _collect_browsers_from_scratch

        order = []

        def confirm_recorder(prompt, **kwargs):
            order.append(("confirm", prompt))
            return True

        def guide_recorder(*args, **kwargs):
            order.append(("guide", "fda"))

        mock_guide.side_effect = guide_recorder
        with patch("footprinter.cli.setup.Confirm.ask", side_effect=confirm_recorder):
            result = _collect_browsers_from_scratch()

        assert "safari" in result and "chrome" in result
        # Exactly one FDA invocation
        guide_calls = [step for step in order if step[0] == "guide"]
        assert len(guide_calls) == 1, f"FDA helper should fire exactly once; got {guide_calls}"
        # FDA must come after the LAST browser confirm (no inline mid-loop call)
        last_confirm_idx = max(i for i, step in enumerate(order) if step[0] == "confirm")
        guide_idx = next(i for i, step in enumerate(order) if step[0] == "guide")
        assert guide_idx > last_confirm_idx, (
            f"FDA must fire after all browser confirms; got order {order}"
        )

    @patch("footprinter.cli.setup._guide_safari_full_disk_access")
    @patch("footprinter.cli.setup.get_available_browsers", return_value=["safari", "chrome"])
    @patch("footprinter.cli.setup.Confirm.ask")
    def test_fda_not_called_when_safari_declined(self, mock_confirm, mock_browsers, mock_guide):
        """Safari declined → FDA helper is not called even if other browsers selected."""
        from footprinter.cli.setup import _collect_browsers_from_scratch

        # safari? -> No, chrome? -> Yes
        mock_confirm.side_effect = [False, True]
        result = _collect_browsers_from_scratch()
        assert "safari" not in result
        assert "chrome" in result
        mock_guide.assert_not_called()


@_requires_darwin
class TestSafariFullDiskAccessGuidance:
    """Tests for _guide_safari_full_disk_access() and its integration into
    _collect_browsers_from_scratch().

    When the user says yes to Safari, the wizard must pause,
    explain the macOS Full Disk Access requirement, offer to open System
    Settings, and verify access — instead of just showing an inline hint.
    """

    @patch("footprinter.cli.setup._guide_safari_full_disk_access")
    @patch("footprinter.cli.setup.get_available_browsers", return_value=["safari", "chrome"])
    @patch("footprinter.cli.setup.Confirm.ask")
    def test_safari_yes_triggers_guidance(self, mock_confirm, mock_browsers, mock_guide):
        """Selecting Safari should invoke the FDA guidance helper exactly once."""
        from footprinter.cli.setup import _collect_browsers_from_scratch

        # Confirm.ask order: include safari? -> yes, include chrome? -> no
        mock_confirm.side_effect = [True, False]
        result = _collect_browsers_from_scratch()
        assert "safari" in result
        assert "chrome" not in result
        mock_guide.assert_called_once()

    @patch("footprinter.cli.setup._guide_safari_full_disk_access")
    @patch("footprinter.cli.setup.get_available_browsers", return_value=["safari", "chrome"])
    @patch("footprinter.cli.setup.Confirm.ask")
    def test_safari_no_skips_guidance(self, mock_confirm, mock_browsers, mock_guide):
        """Declining Safari must not call the FDA guidance helper."""
        from footprinter.cli.setup import _collect_browsers_from_scratch

        # Confirm.ask order: include safari? -> no, include chrome? -> yes
        mock_confirm.side_effect = [False, True]
        result = _collect_browsers_from_scratch()
        assert "safari" not in result
        assert "chrome" in result
        mock_guide.assert_not_called()

    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.sys.platform", "darwin")
    def test_guidance_offers_to_open_system_settings(
        self, mock_console, mock_confirm, mock_subprocess
    ):
        """When user opts in, helper must shell out to `open` with the FDA URL."""
        from footprinter.cli.setup import _guide_safari_full_disk_access

        # Open settings? -> yes; Granted? -> yes (skip verify branch via no file)
        mock_confirm.side_effect = [True, True]
        with patch("footprinter.cli.setup.Path") as mock_path:
            # Make verification raise FileNotFoundError so we don't depend on real DB
            mock_path.return_value.open.side_effect = FileNotFoundError()
            _guide_safari_full_disk_access()

        # subprocess.run called with `open` and the FDA URL
        run_calls = mock_subprocess.call_args_list
        assert any(
            "open" in (call.args[0] if call.args else [])
            and SAFARI_FDA_URL in (call.args[0] if call.args else [])
            for call in run_calls
        ), f"Expected subprocess.run with `open {SAFARI_FDA_URL}`, got {run_calls}"

    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.sys.platform", "darwin")
    def test_guidance_skip_open_settings(
        self, mock_console, mock_confirm, mock_subprocess
    ):
        """Declining the open-settings prompt must not invoke `open`."""
        from footprinter.cli.setup import _guide_safari_full_disk_access

        # Open settings? -> no; Granted? -> no (user skips entirely)
        mock_confirm.side_effect = [False, False]
        _guide_safari_full_disk_access()

        for call in mock_subprocess.call_args_list:
            cmd = call.args[0] if call.args else []
            assert SAFARI_FDA_URL not in cmd, (
                f"subprocess.run should not have been called with `open {SAFARI_FDA_URL}`"
            )

    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.sys.platform", "darwin")
    def test_guidance_verifies_safari_history_db_when_readable(
        self, mock_console, mock_confirm, mock_subprocess, tmp_path
    ):
        """When History.db is readable, helper prints a success line."""
        from footprinter.cli.setup import _guide_safari_full_disk_access

        fake_db = tmp_path / "History.db"
        fake_db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

        # Open settings? -> no; Granted? -> yes (proceed to verify)
        mock_confirm.side_effect = [False, True]
        with patch("footprinter.cli.setup.Path") as mock_path_cls:
            mock_path_cls.return_value = fake_db
            _guide_safari_full_disk_access()

        printed = _extract_printed_text(mock_console)
        assert "readable" in printed.lower() or "✓" in printed or "success" in printed.lower(), (
            f"Expected a readable/✓/success confirmation line, printed: {printed!r}"
        )

    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.sys.platform", "darwin")
    def test_guidance_warns_when_safari_history_db_unreadable(
        self, mock_console, mock_confirm, mock_subprocess
    ):
        """When read raises PermissionError, helper warns but does not raise."""
        from footprinter.cli.setup import _guide_safari_full_disk_access

        # Open settings? -> no; Granted? -> yes (proceed to verify)
        mock_confirm.side_effect = [False, True]
        with patch("footprinter.cli.setup.Path") as mock_path_cls:
            fake_path = MagicMock()
            fake_path.open.side_effect = PermissionError("Operation not permitted")
            mock_path_cls.return_value = fake_path
            _guide_safari_full_disk_access()  # must not raise

        printed = _extract_printed_text(mock_console).lower()
        assert any(token in printed for token in ("denied", "permission", "not readable", "still")), (
            f"Expected a permission/denied/not-readable warning, printed: {printed!r}"
        )

    @patch("footprinter.cli.setup.subprocess.run")
    @patch("footprinter.cli.setup.Confirm.ask")
    @patch("footprinter.cli.setup.console")
    @patch("footprinter.cli.setup.sys.platform", "linux")
    def test_guidance_no_op_on_non_macos(
        self, mock_console, mock_confirm, mock_subprocess
    ):
        """On non-macOS the helper must return immediately — no prompts, no `open`."""
        from footprinter.cli.setup import _guide_safari_full_disk_access

        _guide_safari_full_disk_access()

        mock_confirm.assert_not_called()
        for call in mock_subprocess.call_args_list:
            cmd = call.args[0] if call.args else []
            assert SAFARI_FDA_URL not in cmd, (
                "subprocess.run should not be invoked with the FDA URL on non-macOS"
            )


