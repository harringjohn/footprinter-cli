"""Tests for logging configuration and library module logging compliance.

Merged from test_logging_config.py and test_logging_not_print.py.
"""

import ast
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import footprinter.utils.logging_config as logging_config

PROJECT_ROOT = Path(__file__).parent.parent

# Modules that must use logging instead of print().
# Filtered to only existing files so snapshot builds (which strip
# retention/classification modules) skip them automatically.
_ALL_LIBRARY_MODULES = [
    "footprinter/semantic/vector_store.py",
    "footprinter/semantic/hybrid_search.py",
    "footprinter/ingest/orchestrator.py",
    "footprinter/analysis/retention_reporter.py",
    "footprinter/analysis/project_detector.py",
    "footprinter/analysis/retention_classifier.py",
    "footprinter/analysis/purge_executor.py",
    "footprinter/analysis/retention_manager.py",
    "footprinter/ingest/folder_indexer.py",
    "footprinter/ingest/chat_indexer.py",
]
LIBRARY_MODULES = [m for m in _ALL_LIBRARY_MODULES if (PROJECT_ROOT / m).exists()]


def _find_print_calls(filepath: Path) -> list:
    """Return list of (line_number, col) for every print() call in the file."""
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))

    prints = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # bare print(...)
            if isinstance(func, ast.Name) and func.id == "print":
                prints.append((node.lineno, node.col_offset))
    return prints


def _has_logging_import(filepath: Path) -> bool:
    """Check that 'import logging' or 'from logging ...' exists."""
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    return True
        if isinstance(node, ast.ImportFrom) and node.module == "logging":
            return True
    return False


class TestSetupLogging:
    def setup_method(self):
        """Reset module state before each test."""
        logging_config._configured = False

    def teardown_method(self):
        """Reset after each test to avoid leaking state."""
        logging_config._configured = False

    def test_setup_logging_calls_basic_config(self):
        with patch("footprinter.utils.logging_config.logging.basicConfig") as mock_bc:
            logging_config.setup_logging()
        assert logging_config._configured is True
        mock_bc.assert_called_once_with(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=logging_config.sys.stderr,
        )

    def test_second_call_is_noop(self):
        with patch("footprinter.utils.logging_config.logging.basicConfig") as mock_bc:
            logging_config.setup_logging(level=logging.INFO)
            assert mock_bc.call_count == 1

            # Second call should be skipped entirely
            logging_config.setup_logging(level=logging.DEBUG)
            assert mock_bc.call_count == 1  # Still 1, not 2

    def test_custom_level_passed_through(self):
        with patch("footprinter.utils.logging_config.logging.basicConfig") as mock_bc:
            logging_config.setup_logging(level=logging.WARNING)
        mock_bc.assert_called_once_with(
            level=logging.WARNING,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=logging_config.sys.stderr,
        )

    def test_log_level_env_var_overrides_default(self):
        with patch.dict("os.environ", {"LOG_LEVEL": "DEBUG"}):
            with patch("footprinter.utils.logging_config.logging.basicConfig") as mock_bc:
                logging_config.setup_logging()
        mock_bc.assert_called_once_with(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=logging_config.sys.stderr,
        )

    def test_log_level_env_var_invalid_falls_back(self):
        with patch.dict("os.environ", {"LOG_LEVEL": "NONSENSE"}):
            with patch("footprinter.utils.logging_config.logging.basicConfig") as mock_bc:
                logging_config.setup_logging()
        mock_bc.assert_called_once_with(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=logging_config.sys.stderr,
        )

    def test_explicit_level_overrides_env_var(self):
        with patch.dict("os.environ", {"LOG_LEVEL": "DEBUG"}):
            with patch("footprinter.utils.logging_config.logging.basicConfig") as mock_bc:
                logging_config.setup_logging(level=logging.WARNING)
        mock_bc.assert_called_once_with(
            level=logging.WARNING,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=logging_config.sys.stderr,
        )


class TestAddFileHandler:
    def test_add_file_handler_lowers_root_level(self, tmp_path):
        """add_file_handler() lowers root logger level if it gates the handler."""
        from footprinter.utils.logging_config import add_file_handler

        original_level = logging.root.level
        logging.root.setLevel(logging.CRITICAL)
        try:
            handler = add_file_handler(tmp_path / "test.log", level=logging.INFO)
            assert logging.root.level <= logging.INFO
            logging.root.removeHandler(handler)
            handler.close()
        finally:
            logging.root.setLevel(original_level)

    def test_add_file_handler_does_not_raise_root_level(self, tmp_path):
        """add_file_handler() doesn't raise root level if it's already low enough."""
        from footprinter.utils.logging_config import add_file_handler

        original_level = logging.root.level
        logging.root.setLevel(logging.DEBUG)
        try:
            handler = add_file_handler(tmp_path / "test.log", level=logging.INFO)
            assert logging.root.level == logging.DEBUG
            logging.root.removeHandler(handler)
            handler.close()
        finally:
            logging.root.setLevel(original_level)


@pytest.mark.parametrize("module_path", LIBRARY_MODULES)
def test_no_print_in_library_modules(module_path):
    """Library modules must not contain print() calls."""
    filepath = PROJECT_ROOT / module_path
    assert filepath.exists(), f"File not found: {filepath}"

    prints = _find_print_calls(filepath)
    if prints:
        locations = ", ".join(f"line {line}" for line, _ in prints)
        pytest.fail(
            f"{module_path} has {len(prints)} print() call(s) at {locations}. "
            f"Use logger.info/debug/warning/error instead."
        )


@pytest.mark.parametrize("module_path", LIBRARY_MODULES)
def test_logging_imported(module_path):
    """Library modules must import logging."""
    filepath = PROJECT_ROOT / module_path
    assert filepath.exists(), f"File not found: {filepath}"
    assert _has_logging_import(filepath), f"{module_path} does not import logging"
