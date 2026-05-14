"""Fixtures for service-layer tests.

Provides a database populated with mixed-visibility data across all entity types.
Each entity type has a visible, hidden, and opaque row. ``load_globals()`` is
called so ``resolve_inherit_*`` helpers resolve correctly.
"""

import sqlite3

import pytest

from footprinter.ingest.database import Database


@pytest.fixture
def service_db(tmp_path):
    """Create a database with mixed-visibility data for service tests.

    Returns a raw sqlite3.Connection with Row factory — same type used in
    production service calls.
    """
    db_path = tmp_path / "service_test.db"
    db = Database(str(db_path))
    db.conn.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # -- Sources (required FK target) ----------------------------------------
    conn.execute(
        """INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled)
           VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"""
    )

    # -- Clients: visible(1), hidden(2), opaque(3) --------------------------
    conn.execute(
        """INSERT INTO clients (id, name, slug, client_type, path_pattern, status,
                                mcp_view, mcp_read)
           VALUES
               (1, 'Acme Corp',    'acme',   'external', '~/Work/clients/acme/',   'listed', 'visible', 'allow'),
               (2, 'Hidden Inc',   'hidden', 'external', '~/Work/clients/hidden/', 'listed', 'hidden',  'allow'),
               (3, 'Opaque Ltd',   'opaque', 'internal', '~/Work/clients/opaque/', 'listed', 'opaque',  'allow')"""
    )

    # -- Projects: visible(1), hidden(2), opaque(3) -------------------------
    conn.execute(
        """INSERT INTO projects (id, project_name, project_type, root_path, status,
                                 client_id, mcp_view, mcp_read)
           VALUES
               (1, 'Alpha',   'python', '/Users/u/Work/alpha',   'listed', 1, 'visible', 'allow'),
               (2, 'Beta',    'node',   '/Users/u/Work/beta',    'listed', 2, 'hidden',  'allow'),
               (3, 'Gamma',   'rust',   '/Users/u/Work/gamma',   'listed', 3, 'opaque',  'allow')"""
    )

    # -- Folders: visible(1), hidden(2), opaque(3) --------------------------
    conn.execute(
        """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                direct_file_count, total_size_bytes, mcp_view, mcp_read)
           VALUES
               (1, '/Users/u/Work/alpha/src',  '/Work/alpha/src',  'src',  'local', 1, 5, 10000, 'visible', 'allow'),
               (2, '/Users/u/Work/beta/src',   '/Work/beta/src',   'src',  'local', 2, 3, 5000,  'hidden',  'allow'),
               (3, '/Users/u/Work/gamma/src',  '/Work/gamma/src',  'src',  'local', 3, 1, 2000,  'opaque',  'allow')"""
    )

    # -- Files: visible(1), hidden(2), opaque(3) ----------------------------
    conn.execute(
        """INSERT INTO files (id, name, path, source, status, content_type, size_bytes,
                              project_id, folder_id, mcp_view, mcp_read)
           VALUES
               (1, 'readme.md', '/Users/u/Work/alpha/readme.md', 'local', 'listed', 'markdown', 1000,
                1, 1, 'visible', 'allow'),
               (2, 'secret.py', '/Users/u/Work/beta/secret.py',  'local', 'listed', 'python',   2000,
                2, 2, 'hidden',  'allow'),
               (3, 'config.rs', '/Users/u/Work/gamma/config.rs', 'local', 'listed', 'rust',     500,
                3, 3, 'opaque',  'deny')"""
    )

    # -- Chats: visible(1), hidden(2), opaque(3) ----------------------------
    conn.execute(
        """INSERT INTO chats (id, external_id, account, title, message_count,
                              mcp_view, mcp_read)
           VALUES
               (1, 'conv-vis',    'claude', 'Visible Chat', 2, 'visible', 'allow'),
               (2, 'conv-hidden', 'claude', 'Hidden Chat',  1, 'hidden',  'allow'),
               (3, 'conv-opaque', 'claude', 'Opaque Chat',  1, 'opaque',  'deny')"""
    )

    # -- Messages for chats --------------------------------------------------
    conn.execute(
        """INSERT INTO messages (chat_id, role, content)
           VALUES
               (1, 'user',      'visible message'),
               (1, 'assistant', 'visible reply'),
               (2, 'user',      'hidden message'),
               (3, 'user',      'opaque message')"""
    )

    # -- Emails: visible(1), hidden(2), opaque(3) ---------------------------
    conn.execute(
        """INSERT INTO emails (id, message_id, thread_id, account, from_address,
                               subject, received_at, status, mcp_view, mcp_read)
           VALUES
               (1, 'msg-1', 'thr-1', 'work',     'alice@example.com',
                'Visible Email', '2026-01-15T10:00:00', 'listed', 'visible', 'allow'),
               (2, 'msg-2', 'thr-2', 'work',     'bob@example.com',
                'Hidden Email',  '2026-01-15T11:00:00', 'listed', 'hidden',  'allow'),
               (3, 'msg-3', 'thr-3', 'personal', 'eve@example.com',
                'Opaque Email',  '2026-01-15T12:00:00', 'listed', 'opaque',  'deny')"""
    )

    # -- Visits: visible(1), hidden(2), opaque(3) ---------------------------
    conn.execute(
        """INSERT INTO visits (id, url, title, visit_time, browser,
                               mcp_view, mcp_read)
           VALUES
               (1, 'https://visible.example.com', 'Visible Page', '2026-01-15 10:00:00', 'safari',
                'visible', 'allow'),
               (2, 'https://hidden.example.com',  'Hidden Page',  '2026-01-15 11:00:00', 'safari',
                'hidden',  'allow'),
               (3, 'https://opaque.example.com',  'Opaque Page',  '2026-01-15 12:00:00', 'chrome',
                'opaque',  'deny')"""
    )

    # -- Global visibility/permission policies --------------------------------
    conn.execute(
        """INSERT INTO visibility_policies (scope, setting) VALUES
               ('global', 'visible')"""
    )
    conn.execute(
        """INSERT INTO permission_policies (scope, setting) VALUES
               ('global', 'allow')"""
    )

    conn.commit()

    # Warm the visibility cache
    from footprinter.services.access_service import load_globals

    load_globals(conn)

    yield conn

    # Reset global visibility cache to prevent cross-test contamination
    import footprinter.services.access_service as _vis

    _vis._global_visibility = None
    _vis._global_permission = None

    conn.close()


@pytest.fixture(autouse=True)
def _isolate_vector_store():
    """Ensure VectorStore singleton doesn't leak between service tests.

    Patches _semantic_available() to return False so FTS5 fallback tests
    don't accidentally succeed via mock chromadb stubs from other test
    modules (test_vector_store.py installs chromadb stubs at module level).
    """
    from unittest.mock import patch

    import footprinter.semantic.vector_store as vs

    vs.VectorStore.reset_instance()
    with patch.object(vs, "_semantic_available", return_value=False):
        yield
    vs.VectorStore.reset_instance()
