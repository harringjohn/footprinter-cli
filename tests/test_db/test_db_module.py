"""Tests for the footprinter.db public API module.

Validates imports, signatures, module isolation, and the new messages module.
"""

import ast
import inspect
import pathlib
import sqlite3

import pytest

# ---------------------------------------------------------------------------
# 1. test_db_imports — all 11 entity modules + sql_utils importable
# ---------------------------------------------------------------------------


def test_db_imports():
    """All entity modules are importable from footprinter.db."""
    from footprinter.db import (
        browser,
        chats,
        clients,
        emails,
        files,
        folders,
        messages,
        policies,
        projects,
        search,
        sql_utils,
        status,
    )

    # Verify they're actual modules, not None
    for mod in [
        files,
        folders,
        chats,
        clients,
        browser,
        emails,
        projects,
        status,
        search,
        sql_utils,
        messages,
        policies,
    ]:
        assert mod is not None
        assert hasattr(mod, "__name__")


# ---------------------------------------------------------------------------
# 2. test_no_app_scope_imports — AST scan for restricted imports
# ---------------------------------------------------------------------------


FORBIDDEN_MODULES = {
    "footprinter.permissions",
    "footprinter.visibility",
    "footprinter.dashboard",
    "footprinter.analysis",
    "footprinter.source_registry",
}


def test_no_app_scope_imports():
    """No db/ module imports restricted packages."""
    db_dir = pathlib.Path("footprinter/db")
    violations = []

    for f in sorted(db_dir.glob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(fb) for fb in FORBIDDEN_MODULES):
                    violations.append(f"{f.name}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(fb) for fb in FORBIDDEN_MODULES):
                        violations.append(f"{f.name}: imports {alias.name}")

    assert violations == [], "Restricted imports found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# 3. test_function_signatures — expected public functions exist with type hints
# ---------------------------------------------------------------------------


EXPECTED_FUNCTIONS = {
    "files": ["list_files", "get_file", "update_file_status", "update_file_relationships", "list_file_ids_under_path"],
    "folders": [
        "list_folders",
        "resolve_folder",
        "get_folder",
        "update_folder_relationships",
        "cascade_project_id",
        "cascade_client_id",
    ],
    "chats": ["list_chats", "get_chat_detail", "detect_duplicates", "update_chat_relationships"],
    "policies": [
        "list_visibility_policies",
        "list_permission_policies",
        "set_visibility_policy",
        "set_permission_policy",
        "delete_visibility_policy",
        "delete_permission_policy",
        "clear_visibility_policies",
        "clear_permission_policies",
        "seed_visibility_defaults",
        "seed_permission_defaults",
        "seed_access_policies",
    ],
    "clients": ["list_clients", "update_client", "create_client", "get_client"],
    "browser": ["list_visits", "get_visit", "update_visit_relationships"],
    "emails": ["list_emails", "get_email", "update_email_relationships"],
    "projects": [
        "list_projects",
        "get_project_detail",
        "create_project",
        "update_project",
        "link_files",
        "unlink_files",
    ],
    "status": ["get_system_status"],
    "search": [
        "search_files",
        "search_files_keyword",
        "search_emails_keyword",
        "search_chats_keyword",
        "search_browser_keyword",
        "chat_fts5_fallback",
        "file_fts5_fallback",
        "enrich_chat_visibility",
        "enrich_file_metadata",
    ],
    "messages": ["list_messages", "get_message", "search_messages"],
}


def test_function_signatures():
    """Each module exposes expected public functions with type-hinted params."""
    import footprinter.db as db

    for module_name, func_names in EXPECTED_FUNCTIONS.items():
        mod = getattr(db, module_name)
        for fn_name in func_names:
            assert hasattr(mod, fn_name), f"{module_name}.{fn_name} not found"
            fn = getattr(mod, fn_name)
            assert callable(fn), f"{module_name}.{fn_name} is not callable"

            # Check type hints exist on the function signature
            sig = inspect.signature(fn)
            hints = fn.__annotations__
            # At minimum, the first parameter should be annotated
            params = list(sig.parameters.values())
            if params:
                first = params[0]
                assert first.name in hints or first.annotation != inspect.Parameter.empty, (
                    f"{module_name}.{fn_name}: first param '{first.name}' has no type hint"
                )


# ---------------------------------------------------------------------------
# 4. test_messages_module — specific message functions with correct signatures
# ---------------------------------------------------------------------------


def test_messages_module():
    """messages module exposes list_messages, get_message, search_messages
    with correct parameter signatures."""
    from footprinter.db import messages

    # list_messages
    sig = inspect.signature(messages.list_messages)
    params = list(sig.parameters.keys())
    assert params[0] == "conn"
    # Should accept keyword-only filters
    for kw in ["role", "account", "chat_id", "limit", "page"]:
        assert kw in params, f"list_messages missing param '{kw}'"

    # get_message
    sig = inspect.signature(messages.get_message)
    params = list(sig.parameters.keys())
    assert params[0] == "conn"
    assert "message_id" in params

    # search_messages
    sig = inspect.signature(messages.search_messages)
    params = list(sig.parameters.keys())
    assert params[0] == "conn"
    assert "query" in params


# ---------------------------------------------------------------------------
# 5. test_all_functions_take_connection — first param is sqlite3.Connection
# ---------------------------------------------------------------------------


def test_all_functions_take_connection():
    """Every public function's first parameter is typed sqlite3.Connection."""
    import footprinter.db as db

    for module_name, func_names in EXPECTED_FUNCTIONS.items():
        mod = getattr(db, module_name)
        for fn_name in func_names:
            fn = getattr(mod, fn_name)
            sig = inspect.signature(fn)
            params = list(sig.parameters.values())
            assert len(params) > 0, f"{module_name}.{fn_name} has no params"

            first = params[0]
            annotation = first.annotation
            assert annotation is sqlite3.Connection, (
                f"{module_name}.{fn_name}: first param '{first.name}' "
                f"annotated as {annotation}, expected sqlite3.Connection"
            )


# ---------------------------------------------------------------------------
# 6. test_paginate / test_paginated_response — shared pagination helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection with a small test table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany(
        "INSERT INTO items (name) VALUES (?)",
        [("a",), ("b",), ("c",), ("d",), ("e",)],
    )
    conn.commit()
    return conn


class TestPaginate:
    def test_paginate_basic(self, mem_conn):
        from footprinter.db.sql_utils import paginate

        rows, pag = paginate(
            mem_conn,
            "SELECT COUNT(*) FROM items",
            "SELECT * FROM items ORDER BY id LIMIT ? OFFSET ?",
            [],
            page=1,
            limit=2,
        )
        assert len(rows) == 2
        assert pag["total"] == 5
        assert pag["total_pages"] == 3
        assert pag["page"] == 1
        assert pag["limit"] == 2

    def test_paginate_empty_result(self, mem_conn):
        from footprinter.db.sql_utils import paginate

        rows, pag = paginate(
            mem_conn,
            "SELECT COUNT(*) FROM items WHERE name = 'zzz'",
            "SELECT * FROM items WHERE name = 'zzz' LIMIT ? OFFSET ?",
            [],
            page=1,
            limit=10,
        )
        assert len(rows) == 0
        assert pag["total"] == 0
        assert pag["total_pages"] == 1

    def test_paginate_with_params(self, mem_conn):
        from footprinter.db.sql_utils import paginate

        rows, pag = paginate(
            mem_conn,
            "SELECT COUNT(*) FROM items WHERE name > ?",
            "SELECT * FROM items WHERE name > ? ORDER BY id LIMIT ? OFFSET ?",
            ("c",),
            page=1,
            limit=10,
        )
        assert pag["total"] == 2  # d, e
        assert len(rows) == 2


class TestPaginatedResponse:
    def test_paginated_response_basic(self):
        from footprinter.db.sql_utils import paginated_response

        result = paginated_response("items", [1, 2, 3], {"page": 1, "limit": 10, "total": 3, "total_pages": 1})
        assert result["items"] == [1, 2, 3]
        assert result["pagination"]["total"] == 3
        assert len(result) == 2  # items + pagination

    def test_paginated_response_with_extras(self):
        from footprinter.db.sql_utils import paginated_response

        result = paginated_response(
            "projects",
            [],
            {"page": 1, "limit": 10, "total": 0, "total_pages": 1},
            types=["python"],
            clients=["Acme"],
        )
        assert result["projects"] == []
        assert result["types"] == ["python"]
        assert result["clients"] == ["Acme"]
        assert len(result) == 4  # projects + pagination + types + clients


# ---------------------------------------------------------------------------
# 7. test_list_project_files — list_project_files from db.projects
# ---------------------------------------------------------------------------


@pytest.fixture
def project_conn():
    """In-memory SQLite connection with projects and files tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            project_name TEXT,
            project_type TEXT,
            root_path TEXT,
            status TEXT,
            description TEXT,
            client TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            source TEXT,
            account TEXT,
            name TEXT,
            path TEXT,
            content_type TEXT,
            size_bytes INTEGER,
            modified_at TEXT,
            status TEXT DEFAULT 'listed',
            status_reason TEXT,
            project_id INTEGER
        )"""
    )
    conn.execute(
        "INSERT INTO projects (id, project_name, project_type, root_path, status) "
        "VALUES (1, 'EmptyProj', 'python', '/tmp/empty', 'listed')"
    )
    conn.commit()
    return conn


class TestListProjectFiles:
    def test_empty_total_pages(self, project_conn):
        """Empty project should have total_pages=1, matching the paginate() helper."""
        from footprinter.db.projects import list_project_files

        result = list_project_files(project_conn, 1)
        assert result is not None
        assert result["pagination"]["total_pages"] == 1

    def test_key_names_match_list_files(self, project_conn):
        """File dicts must use content_type/modified_at, matching list_files() convention."""
        from footprinter.db.projects import list_project_files

        project_conn.execute(
            "INSERT INTO files (id, source, account, name, path, content_type, "
            "size_bytes, modified_at, status, project_id) "
            "VALUES (1, 'local', 'work', 'readme.md', '/tmp/empty/readme.md', "
            "'text/markdown', 1024, '2026-01-15', 'listed', 1)"
        )
        project_conn.commit()

        result = list_project_files(project_conn, 1)
        files = result["files"]
        assert len(files) == 1

        f = files[0]
        # Must use list_files() key names
        assert "content_type" in f, "Expected 'content_type', got 'type'"
        assert "modified_at" in f, "Expected 'modified_at', got 'modified'"
        # Old key names must NOT be present
        assert "type" not in f, "Legacy key 'type' should not appear"
        assert "modified" not in f, "Legacy key 'modified' should not appear"


# ---------------------------------------------------------------------------
# 8. test_default_limits_consistent — all list_* functions default limit=50
# ---------------------------------------------------------------------------

# Modules and their list functions expected to have limit=50.
# search functions (search_files, search_messages) are excluded — different concern.
_LIST_FUNCTIONS = {
    "browser": ["list_visits"],
    "chats": ["list_chats"],
    "clients": ["list_clients"],
    "emails": ["list_emails"],
    "files": ["list_files"],
    "folders": ["list_folders"],
    "messages": ["list_messages"],
    "projects": ["list_projects", "list_project_files"],
}


def test_default_limits_consistent():
    """Every entity list function in footprinter.db defaults limit to 50."""
    import footprinter.db as db

    violations = []
    for module_name, func_names in _LIST_FUNCTIONS.items():
        mod = getattr(db, module_name)
        for fn_name in func_names:
            fn = getattr(mod, fn_name)
            sig = inspect.signature(fn)
            param = sig.parameters.get("limit")
            assert param is not None, f"{module_name}.{fn_name} has no 'limit' param"
            if param.default != 50:
                violations.append(f"{module_name}.{fn_name}: limit default={param.default!r}, expected 50")

    assert violations == [], "Inconsistent limit defaults:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# 9. TestPaginationShape — consistent pagination envelope
# ---------------------------------------------------------------------------

PAGINATION_KEYS = {"page", "limit", "total", "total_pages"}


@pytest.fixture
def pagination_conn():
    """In-memory SQLite with chats, messages, files, and files_fts tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute(
        """CREATE TABLE chats (
            id INTEGER PRIMARY KEY, external_id TEXT, title TEXT,
            account TEXT, created_at TEXT, updated_at TEXT, message_count INTEGER,
            mcp_view TEXT DEFAULT 'inherit'
        )"""
    )
    conn.execute(
        """CREATE TABLE messages (
            id INTEGER PRIMARY KEY, chat_id INTEGER, message_id TEXT,
            role TEXT, content TEXT, created_at TEXT,
            mcp_read TEXT DEFAULT 'inherit',
            mcp_view TEXT DEFAULT 'inherit'
        )"""
    )
    conn.execute(
        """CREATE TABLE files (
            id INTEGER PRIMARY KEY, source TEXT, name TEXT, path TEXT,
            content_type TEXT, size_bytes INTEGER, modified_at TEXT,
            status TEXT DEFAULT 'listed'
        )"""
    )
    conn.execute("CREATE VIRTUAL TABLE files_fts USING fts5(name, path, content=files, content_rowid=id)")

    # Seed data
    conn.execute(
        "INSERT INTO chats (id, title, account, created_at, message_count) "
        "VALUES (1, 'Test chat', 'claude', '2026-01-01', 3)"
    )
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO messages (id, chat_id, message_id, role, content, created_at) "
            "VALUES (?, 1, ?, 'user', ?, '2026-01-01')",
            (i, f"msg-{i}", f"test content message {i}"),
        )
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO files (id, source, name, path, size_bytes, modified_at, status) "
            "VALUES (?, 'local', ?, ?, 100, '2026-01-01', 'listed')",
            (i, f"report_{i}.txt", f"/home/user/report_{i}.txt"),
        )
        conn.execute(
            "INSERT INTO files_fts (rowid, name, path) VALUES (?, ?, ?)",
            (i, f"report_{i}.txt", f"/home/user/report_{i}.txt"),
        )
    conn.commit()
    return conn


class TestPaginationShape:
    """Every list/search function must return a 'pagination' dict with the four standard keys."""

    def test_list_messages_shape(self, pagination_conn):
        from footprinter.db.messages import list_messages

        result = list_messages(pagination_conn)
        assert "messages" in result
        assert "pagination" in result
        assert set(result["pagination"].keys()) == PAGINATION_KEYS

    def test_search_messages_shape(self, pagination_conn):
        from footprinter.db.messages import search_messages

        result = search_messages(pagination_conn, "test content")
        assert "results" in result
        assert "pagination" in result
        assert set(result["pagination"].keys()) == PAGINATION_KEYS

    def test_search_files_shape(self, pagination_conn):
        from footprinter.db.search import search_files

        result = search_files(pagination_conn, "report")
        assert "results" in result
        assert "pagination" in result
        assert set(result["pagination"].keys()) == PAGINATION_KEYS

    def test_search_files_pagination_pages(self, pagination_conn):
        """search_files() supports page parameter and returns correct total_pages."""
        from footprinter.db.search import search_files

        result = search_files(pagination_conn, "report", limit=2, page=1)
        assert len(result["results"]) == 2
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["total_pages"] == 3
        assert result["pagination"]["page"] == 1

        result_p2 = search_files(pagination_conn, "report", limit=2, page=2)
        assert len(result_p2["results"]) == 2
        assert result_p2["pagination"]["page"] == 2

    def test_search_files_drive_source_not_accepted(self, pagination_conn):
        """source='drive' must not be treated as 'remote' — it should fall through to 'all'."""
        from footprinter.db.search import search_files

        # With the compat shim, source="drive" hits the remote branch which
        # queries the non-existent 'sources' table → OperationalError.
        # After the fix, it falls through to the unfiltered "all" branch.
        result = search_files(pagination_conn, "report", source="drive")
        assert result["pagination"]["total"] == 5, "source='drive' should fall through to 'all' and return all files"

    def test_search_messages_pagination_pages(self, pagination_conn):
        """search_messages() supports page parameter and returns correct pagination metadata."""
        from footprinter.db.messages import search_messages

        result = search_messages(pagination_conn, "test content", limit=2, page=1)
        assert len(result["results"]) == 2
        assert result["pagination"]["total"] == 3
        assert result["pagination"]["total_pages"] == 2
        assert result["pagination"]["page"] == 1

        result_p2 = search_messages(pagination_conn, "test content", limit=2, page=2)
        assert len(result_p2["results"]) == 1
        assert result_p2["pagination"]["page"] == 2


def test_merge_projects_removed():
    """merge_projects was removed when chat-merge functionality was stripped."""
    with pytest.raises(ImportError):
        from footprinter.db.projects import merge_projects  # noqa: F401
