"""Fixtures for db-layer tests.

Provides a database populated with mixed-visibility data across all entity types,
reusing the same schema as service tests but scoped to db/ function testing.
"""

import sqlite3

import pytest

from footprinter.ingest.database import Database


@pytest.fixture
def db_conn(tmp_path):
    """Create a database with test data for db/ function tests.

    Returns a raw sqlite3.Connection with Row factory.
    """
    db_path = tmp_path / "db_test.db"
    db = Database(str(db_path))
    db.conn.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # -- Sources ---------------------------------------------------------------
    conn.execute(
        """INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled)
           VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"""
    )

    # -- Clients ---------------------------------------------------------------
    conn.execute(
        """INSERT INTO clients (id, name, slug, client_type, path_pattern, status,
                                mcp_view, mcp_read)
           VALUES
               (1, 'Acme Corp',  'acme',   'external', '~/Work/clients/acme/',   'listed', 'visible', 'allow'),
               (2, 'Hidden Inc', 'hidden', 'external', '~/Work/clients/hidden/', 'listed', 'hidden',  'allow')"""
    )

    # -- Projects --------------------------------------------------------------
    conn.execute(
        """INSERT INTO projects (id, project_name, project_type, root_path, status,
                                 client_id, mcp_view, mcp_read)
           VALUES
               (1, 'Alpha', 'python', '/Users/u/Work/alpha', 'listed', 1, 'visible', 'allow'),
               (2, 'Beta',  'node',   '/Users/u/Work/beta',  'listed', 2, 'hidden',  'allow')"""
    )

    # -- Files -----------------------------------------------------------------
    conn.execute(
        """INSERT INTO files (id, name, path, source, status, content_type, size_bytes,
                              modified_at, account, mime_type, project_id,
                              mcp_view, mcp_read, content_preview)
           VALUES
               (1, 'readme.md',  '/Users/u/Work/alpha/readme.md',  'local', 'listed', 'markdown', 1000,
                '2026-01-10', NULL, 'text/markdown', 1, 'visible', 'allow', 'This is a readme'),
               (2, 'secret.py',  '/Users/u/Work/beta/secret.py',   'local', 'listed', 'python',   2000,
                '2026-01-11', NULL, 'text/x-python', 2, 'hidden',  'allow', 'Secret content'),
               (3, 'report.pdf', '/Users/u/Work/alpha/report.pdf', 'local', 'listed', 'pdf',      5000,
                '2026-01-12', 'work', 'application/pdf', 1, 'visible', 'allow', 'Report content')"""
    )

    # -- Emails ----------------------------------------------------------------
    conn.execute(
        """INSERT INTO emails (id, message_id, thread_id, account, from_address, from_name,
                               to_addresses, subject, body_preview, received_at,
                               labels, status, mcp_view, mcp_read)
           VALUES
               (1, 'msg-1', 'thr-1', 'work',     'alice@example.com', 'Alice',
                'bob@example.com', 'Project Update', 'Here is the update...', '2026-01-15T10:00:00',
                'inbox', 'listed', 'visible', 'allow'),
               (2, 'msg-2', 'thr-2', 'work',     'bob@example.com', 'Bob',
                'alice@example.com', 'Hidden Email', 'Secret stuff', '2026-01-15T11:00:00',
                'inbox', 'listed', 'hidden', 'allow'),
               (3, 'msg-3', 'thr-3', 'personal', 'eve@example.com', 'Eve',
                'alice@example.com', 'Weekend Plans', 'Let us meet...', '2026-01-15T12:00:00',
                'inbox', 'listed', 'visible', 'allow')"""
    )

    # -- Chats -----------------------------------------------------------------
    conn.execute(
        """INSERT INTO chats (id, external_id, account, title, summary, message_count,
                              created_at, mcp_view, mcp_read, status)
           VALUES
               (1, 'conv-vis',    'claude', 'Visible Chat', 'A visible summary', 2,
                '2026-01-10', 'visible', 'allow', 'listed'),
               (2, 'conv-hidden', 'claude', 'Hidden Chat',  'A hidden summary',  1,
                '2026-01-11', 'hidden',  'allow', 'listed'),
               (3, 'conv-opaque', 'claude', 'Opaque Chat',  'An opaque summary', 1,
                '2026-01-12', 'opaque',  'deny',  'listed')"""
    )

    # -- Visits ----------------------------------------------------------------
    conn.execute(
        """INSERT INTO visits (id, url, title, visit_time, browser, mcp_view, mcp_read)
           VALUES
               (1, 'https://example.com/page1', 'Example Page',  '2026-01-15 10:00:00', 'safari',
                'visible', 'allow'),
               (2, 'https://hidden.example.com', 'Hidden Page',  '2026-01-15 11:00:00', 'safari',
                'hidden', 'allow'),
               (3, 'https://example.com/page2', 'Another Page',  '2026-01-15 12:00:00', 'chrome',
                'visible', 'allow')"""
    )

    conn.commit()
    yield conn
    conn.close()
