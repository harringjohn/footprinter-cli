"""Tests for examples/ starter scripts."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
SCRIPTS = [
    "list_recent_files.py",
    "search_across_sources.py",
    "export_chat_history.py",
]
PYTHON = sys.executable


@pytest.fixture()
def empty_db_home(tmp_path):
    """Create a FOOTPRINTER_HOME with an initialized but empty database."""
    from footprinter.ingest.database import Database

    db_path = tmp_path / "footprinter.db"
    db = Database(str(db_path))
    db.conn.close()
    return tmp_path


def _run_example(script_name, home_dir, extra_args=None):
    """Run an example script as a subprocess with isolated FOOTPRINTER_HOME."""
    env = {**os.environ, "FOOTPRINTER_HOME": str(home_dir)}
    cmd = [PYTHON, str(EXAMPLES_DIR / script_name)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


class TestExampleScriptsRunCleanly:
    """Each example script should exit 0 against an empty database."""

    def test_list_recent_files(self, empty_db_home):
        result = _run_example("list_recent_files.py", empty_db_home)
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr

    def test_search_across_sources(self, empty_db_home):
        result = _run_example("search_across_sources.py", empty_db_home, ["test"])
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr

    def test_export_chat_history(self, empty_db_home):
        result = _run_example("export_chat_history.py", empty_db_home)
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr


class TestExampleScriptQuality:
    """Structural checks: imports, line count, docstrings."""

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_only_imports_public_api(self, script):
        """Scripts should only import from footprinter.db or footprinter.paths (+ stdlib)."""
        source = (EXAMPLES_DIR / script).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("footprinter"):
                    assert node.module.startswith(("footprinter.db", "footprinter.paths")), (
                        f"{script} imports {node.module} — only footprinter.db and footprinter.paths allowed"
                    )

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_under_50_lines(self, script):
        lines = (EXAMPLES_DIR / script).read_text().splitlines()
        assert len(lines) < 50, f"{script} has {len(lines)} lines (limit: 50)"

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_has_docstring(self, script):
        source = (EXAMPLES_DIR / script).read_text()
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        assert docstring, f"{script} missing module-level docstring"
