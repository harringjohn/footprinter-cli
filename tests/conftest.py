"""
Pytest fixtures for Footprinter tests.
"""

import json
import os
import sqlite3
import tempfile
import types
from pathlib import Path

import pytest

# Skip API tests when the [api] extra (FastAPI) is not installed
collect_ignore = []
try:
    import fastapi  # noqa: F401
except ImportError:
    collect_ignore.append("test_api")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_bundled_config():
    """Return the path to bundled config.example.yaml, or raise if missing."""
    config_path = REPO_ROOT / "footprinter" / "bundled" / "config.example.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Bundled config not found at {config_path}. "
            f"_repo_local_paths resolves config via Path(__file__).parent.parent; "
            f"if conftest.py moved, update REPO_ROOT."
        )
    return config_path


@pytest.fixture(autouse=True, scope="session")
def _repo_local_paths(tmp_path_factory):
    """Point footprinter.paths at the bundled config and a temp DB for tests.

    Sets FOOTPRINTER_HOME to a session-scoped temp directory so all derived
    paths (chroma, DB) resolve there instead of ~/.footprinter. Config uses
    the bundled config.example.yaml (the shipped single source of truth).
    Restores original env on teardown.
    """
    config_path = _resolve_bundled_config()
    session_tmp = tmp_path_factory.mktemp("footprinter_test_db")
    env = {
        "FOOTPRINTER_HOME": str(session_tmp),
        "FOOTPRINTER_CONFIG": str(config_path),
        "FOOTPRINTER_DB_PATH": str(session_tmp / "footprinter.db"),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_files(temp_dir):
    """Create sample files for testing."""
    # Create some test files
    (temp_dir / "test.txt").write_text("Hello, world!")
    (temp_dir / "test.py").write_text("print('hello')")
    (temp_dir / "test.md").write_text("# Test\n\nSome content")

    # Create a subdirectory with files
    subdir = temp_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("Nested file")

    # Create a hidden directory (should be excluded)
    hidden = temp_dir / ".hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("Secret content")

    return temp_dir


@pytest.fixture
def tool_db(tmp_path):
    """Create a database with tool-scope schema only (no app tables).

    Uses Database.init_db() — no init_app_schema() or init_retention_schema(),
    so app-only tables and columns are absent. Returns a raw
    sqlite3.Connection so callers get the same type used in production.
    """
    db_path = tmp_path / "test.db"
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    db.conn.close()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


@pytest.fixture
def access_db(tmp_path, monkeypatch):
    """Materialized unified fixture + per-test baseline anchors.

    Runs ``create_fixture`` into a pytest tmp dir, reloads the
    :class:`SeedManifest` persisted by the seeder, and inserts a handful of
    test-only rows the shipped fixture does not anchor (see below). Primes
    ``load_globals`` so visibility/permission resolution is hot. Yields
    ``(conn, data)`` where ``data`` is a namespace of IDs keyed by the
    semantic role each entity plays — every test can address a concrete
    matrix cell (``data.opaque_project_file_id`` → ``F10``) or a baseline
    anchor (``data.visible_file_id`` → a fresh row outside every policy).

    Why baseline anchors exist: the unified fixture blankets ``Work/`` and
    ``Personal/`` with opaque or hidden cascades (``folder:~/Work/clients/``,
    ``folder:~/Work/sample-tool/``, ``project:Hidden Standalone``), so no
    corpus file resolves to ``visible`` in the cached ``resolved_state``. The
    matrix tests still need ``visible+allow`` and ``visible+deny`` files to
    exercise ``gate_access`` status transitions end-to-end, plus a visible
    chat to exercise message inheritance from a non-hidden parent. These
    anchors are rows inserted at a path outside every folder prefix — they
    resolve to ``global=visible`` naturally.
    """
    from footprinter.fixture import create_fixture, load_seed_manifest

    from footprinter.access_stamper import recalculate_access
    from footprinter.db.chats import insert_chat
    from footprinter.db.policies import set_permission_policy
    from footprinter.paths import get_bundled_path
    from footprinter.services.access_service import load_globals

    home = create_fixture(tmp_path / "fixture")
    manifest = load_seed_manifest(home)

    # Route path-dependent code at the materialized fixture for this test.
    monkeypatch.setenv("FOOTPRINTER_HOME", str(home))
    monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(home / "footprinter.db"))

    conn = sqlite3.connect(str(home / "footprinter.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Baseline anchors live under an isolated path that no folder-prefix
    # policy covers, so recalculate_access stamps them visible+allow.
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir(exist_ok=True)
    conn.execute(
        "INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled) "
        "VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"
    )

    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('baseline-visible.txt', ?, 'local', 'listed', 'text', 100)",
        (f"{baseline_dir}/visible.txt",),
    )
    baseline_visible_file_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('baseline-visible-denied.txt', ?, 'local', 'listed', 'text', 100)",
        (f"{baseline_dir}/visible-denied.txt",),
    )
    baseline_visible_denied_file_id = cursor.lastrowid
    set_permission_policy(conn, f"file:{baseline_visible_denied_file_id}", "deny")

    # Opaque-folder-prefix anchor: path matches ``folder:<home>/Work/clients/``
    # but no longer-prefix override, so query-time resolution lands opaque.
    # F07 in the shipped fixture sits under ``visible-client-corp/``, which
    # has an explicit visible override — it can't anchor this assertion.
    # Per ``expected.json`` F09's note, ``recalculate_access`` caches the
    # enclosing opaque for folder-prefix-only files; assertions against this
    # anchor must go through query-time resolution (``get_visibility``,
    # ``gate_access``, ``resolve_visibility_with_source``) rather than reading
    # ``files.visibility`` directly — longest-prefix override is applied at
    # query time, not stamp time.
    cursor.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('opaque-prefix-anchor.txt', ?, 'local', 'listed', 'text', 100)",
        (f"{home}/Work/clients/shared-docs/opaque-prefix-anchor.txt",),
    )
    opaque_folder_prefix_file_id = cursor.lastrowid

    baseline_visible_chat_id = insert_chat(
        conn,
        {
            "external_id": "baseline-visible-chat",
            "account": "personal",
            "title": "Baseline Visible Chat",
            "message_count": 1,
        },
    )
    cursor.execute(
        "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
        (baseline_visible_chat_id, "Baseline visible message."),
    )
    baseline_visible_message_id = cursor.lastrowid

    # C08 is synthesized with message_count=0; a message under it is needed
    # so the inheritance test can assert the hidden-cascade path.
    c08_id = manifest.chat_ids["C08"]
    cursor.execute(
        "INSERT INTO messages (chat_id, role, content) VALUES (?, 'user', ?)",
        (c08_id, "Message under hidden-cascade chat."),
    )
    baseline_hidden_message_id = cursor.lastrowid

    conn.commit()
    recalculate_access(conn, "global")
    load_globals(conn)

    expected = json.loads(
        (Path(str(get_bundled_path("fixture"))) / "expected.json").read_text()
    )

    data = types.SimpleNamespace(
        manifest=manifest,
        expected=expected,
        # Clients / projects come straight from the manifest.
        visible_client_id=manifest.visible_client_id,
        hidden_client_id=manifest.hidden_client_id,
        visible_project_id=manifest.visible_project_id,
        opaque_project_id=manifest.opaque_project_id,
        hidden_child_project_id=manifest.hidden_child_project_id,
        # Files: cell-ID lookups, with baseline anchors for visible states
        # and the opaque-folder-prefix case that the shipped fixture covers
        # only via a longer-prefix override.
        visible_file_id=baseline_visible_file_id,
        denied_file_id=baseline_visible_denied_file_id,
        opaque_folder_file_id=opaque_folder_prefix_file_id,
        opaque_project_file_id=manifest.file_ids["F10"],
        hidden_client_file_id=manifest.file_ids["F12"],
        hidden_override_file_id=manifest.file_ids["F14"],
        # Emails / visits: direct manifest lookups.
        visible_email_id=manifest.email_ids["E01"],
        opaque_account_email_id=manifest.email_ids["E05"],
        denied_email_id=manifest.email_ids["E11"],
        denied_visit_id=manifest.visit_ids["V03"],
        # Folders: FD03 anchors the opaque-project cascade.
        opaque_project_folder_id=manifest.folder_ids["FD03"],
        # Chats + messages: C08 is the hidden cascade; visible side is baseline.
        visible_chat_id=baseline_visible_chat_id,
        hidden_client_chat_id=c08_id,
        visible_message_id=baseline_visible_message_id,
        hidden_message_id=baseline_hidden_message_id,
    )

    try:
        yield conn, data
    finally:
        conn.close()


@pytest.fixture
def mock_config():
    """Return a minimal test configuration."""
    return {
        "directories": ["/tmp/test"],
        "browsers": ["safari"],
        "exclusions": {
            "always": [
                r".*/\.Trash/.*",
                r".*/\.git/.*",
                r".*/node_modules/.*",
                r".*/__pycache__/.*",
            ]
        },
        "indexing": {
            "supported_extensions": [],
            "max_file_size_mb": 0,
        },
    }


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def populate_minimal_db(db_path):
    """Insert minimal test data: sources + 1 project + 1 file."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled)
           VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"""
    )
    conn.execute(
        """INSERT INTO projects (name, status)
           VALUES ('test-project', 'listed')"""
    )
    conn.execute(
        """INSERT INTO files (name, path, source, status, content_type, size_bytes)
           VALUES ('readme.md', '/Users/testuser/Work/test-project/readme.md', 'local', 'listed', 'markdown', 1000)"""
    )
    conn.commit()
    conn.close()


def populate_access_control_db(db_path):
    """Insert test data covering visibility/permission bypass scenarios."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Source registry
    conn.execute(
        """INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled)
           VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"""
    )

    # Projects
    conn.execute(
        """INSERT INTO projects (id, name, status)
           VALUES
               (1, 'Visible Project', 'listed'),
               (2, 'Hidden Project', 'listed'),
               (3, 'Opaque Project', 'listed')"""
    )

    # Clients
    conn.execute(
        """INSERT INTO clients (id, name, slug, client_type, status)
           VALUES
               (1, 'Visible Client', 'full', 'external', 'listed'),
               (2, 'Hidden Client', 'hidden', 'external', 'listed'),
               (3, 'Opaque Client', 'opaque', 'external', 'listed')"""
    )

    # Chats: visible(1), hidden(2), opaque(3), permission-denied(4)
    conn.execute(
        """INSERT INTO chats (id, external_id, account, title, message_count)
           VALUES
               (1, 'conv-visible', 'personal', 'Visible Chat', 2),
               (2, 'conv-hidden', 'personal', 'Hidden Chat', 1),
               (3, 'conv-opaque', 'personal', 'Opaque Chat', 1),
               (4, 'conv-denied', 'personal', 'Denied Chat', 1)"""
    )

    # Messages for chats
    conn.execute(
        """INSERT INTO messages (chat_id, role, content)
           VALUES
               (1, 'user', 'visible message 1'),
               (1, 'assistant', 'visible reply'),
               (2, 'user', 'hidden message'),
               (3, 'user', 'opaque message'),
               (4, 'user', 'denied message')"""
    )

    # Files: visible(1), hidden(2)
    conn.execute(
        """INSERT INTO files (id, name, path, source, status, content_type, size_bytes)
           VALUES
               (1, 'visible.txt', '/Users/testuser/Work/visible.txt', 'local', 'listed', 'text', 100),
               (2, 'hidden.txt', '/Users/testuser/Work/hidden.txt', 'local', 'listed', 'text', 200)"""
    )

    # Browser history
    conn.execute(
        """INSERT INTO visits (id, url, title, visit_time, browser)
           VALUES (1, 'https://example.com', 'Example', datetime('now'), 'safari')"""
    )

    # Visibility policies
    conn.execute(
        """INSERT INTO visibility_policies (scope, setting) VALUES
               ('chat:1', 'full'),
               ('chat:2', 'hidden'),
               ('chat:3', 'opaque'),
               ('chat:4', 'full'),
               ('file:1', 'full'),
               ('file:2', 'hidden'),
               ('project:1', 'full'),
               ('project:2', 'hidden'),
               ('project:3', 'opaque'),
               ('client:1', 'full'),
               ('client:2', 'hidden'),
               ('client:3', 'opaque')"""
    )

    # Permission policies
    conn.execute(
        """INSERT INTO permission_policies (scope, setting) VALUES
               ('chat:1', 'allow'),
               ('chat:4', 'deny')"""
    )

    # Stamp cached columns to match policies (simulates access_resolution stage)
    conn.execute("UPDATE chats SET visibility = 'full' WHERE id = 1")
    conn.execute("UPDATE chats SET visibility = 'hidden' WHERE id = 2")
    conn.execute("UPDATE chats SET visibility = 'opaque' WHERE id = 3")
    conn.execute("UPDATE chats SET visibility = 'full', access = 'deny' WHERE id = 4")
    conn.execute("UPDATE files SET visibility = 'full' WHERE id = 1")
    conn.execute("UPDATE files SET visibility = 'hidden' WHERE id = 2")
    conn.execute("UPDATE projects SET visibility = 'full' WHERE id = 1")
    conn.execute("UPDATE projects SET visibility = 'hidden' WHERE id = 2")
    conn.execute("UPDATE projects SET visibility = 'opaque' WHERE id = 3")
    conn.execute("UPDATE clients SET visibility = 'full' WHERE id = 1")
    conn.execute("UPDATE clients SET visibility = 'hidden' WHERE id = 2")
    conn.execute("UPDATE clients SET visibility = 'opaque' WHERE id = 3")

    conn.commit()
    conn.close()


def get_json(client, path):
    """GET an endpoint, assert 200 and valid JSON, return parsed data."""
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    assert data is not None, f"{path} did not return valid JSON"
    return data


# ---------------------------------------------------------------------------
# CLI test helper
# ---------------------------------------------------------------------------


def run_wizard_mocked(**overrides):
    """Run run_interactive_wizard() with all side-effects mocked.

    Returns a dict of all mocks used so callers can inspect call counts/order.
    Override individual mocks by passing keyword arguments.
    By default, _choose_preset returns None (full setup path).
    """
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    from footprinter.cli.setup import run_interactive_wizard

    defaults = {
        "console": MagicMock(),
        "_load_existing_config": MagicMock(return_value=None),
        "_choose_preset": MagicMock(return_value=None),
        "collect_answers": MagicMock(return_value={"directories": ["~/Work"], "browsers": ["safari"]}),
        "collect_chat_export_path": MagicMock(return_value=None),
        "collect_vectorization_answers": MagicMock(
            return_value={"file_vectorization": False, "chat_vectorization": False, "content_snippets": False}
        ),
        "preview_config": MagicMock(),
        "Confirm.ask": MagicMock(return_value=True),
        "generate_config": MagicMock(return_value={"directories": ["~/Work"]}),
        "write_config": MagicMock(),
        "run_orchestrator": MagicMock(),
        "import_chat_export": MagicMock(return_value={}),
        "_get_indexing_counts": MagicMock(return_value={}),
        "seed_access_policies": MagicMock(),
        "offer_setup_claude": MagicMock(return_value=False),
        "print_summary": MagicMock(),
        "get_log_path": MagicMock(return_value=Path("/tmp/test-setup.log")),
        "_offer_csv_import_wizard": MagicMock(),
    }
    defaults.update(overrides)

    prefix = "footprinter.cli.setup."
    with (
        patch(prefix + "console", defaults["console"]),
        patch(prefix + "_load_existing_config", defaults["_load_existing_config"]),
        patch(prefix + "_choose_preset", defaults["_choose_preset"]),
        patch(prefix + "collect_answers", defaults["collect_answers"]),
        patch(prefix + "collect_chat_export_path", defaults["collect_chat_export_path"]),
        patch(prefix + "collect_vectorization_answers", defaults["collect_vectorization_answers"]),
        patch(prefix + "preview_config", defaults["preview_config"]),
        patch(prefix + "Confirm.ask", defaults["Confirm.ask"]),
        patch(prefix + "generate_config", defaults["generate_config"]),
        patch(prefix + "write_config", defaults["write_config"]),
        patch(prefix + "run_orchestrator", defaults["run_orchestrator"]),
        patch(prefix + "import_chat_export", defaults["import_chat_export"]),
        patch(prefix + "_get_indexing_counts", defaults["_get_indexing_counts"]),
        patch(prefix + "seed_access_policies", defaults["seed_access_policies"]),
        patch(prefix + "offer_setup_claude", defaults["offer_setup_claude"]),
        patch(prefix + "print_summary", defaults["print_summary"]),
        patch(prefix + "get_log_path", defaults["get_log_path"]),
        patch(prefix + "_offer_csv_import_wizard", defaults["_offer_csv_import_wizard"]),
    ):
        run_interactive_wizard()

    return defaults


def run_fp(*argv: str) -> tuple[str, str, int]:
    """Run ``footprinter.cli.main(list(argv))`` and capture output.

    Returns (stdout, stderr, exit_code).  ``SystemExit`` is caught so
    callers can assert on the exit code without crashing the test.
    """
    import io
    import sys

    from footprinter.cli import main

    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        main(list(argv))
        code = 0
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
    finally:
        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
        sys.stdout, sys.stderr = old_out, old_err
    return stdout, stderr, code
