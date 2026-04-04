"""Shared fixtures for HTTP API tests."""

import sqlite3

import pytest


@pytest.fixture
def api_client(tool_db):
    """FastAPI TestClient with database dependency overridden to use tool_db.

    Creates a fresh connection per request to avoid SQLite's thread-safety
    restriction (tool_db is created in the test thread, but FastAPI's
    TestClient runs handlers in a worker thread).
    """
    from fastapi.testclient import TestClient

    from footprinter.api.db import get_conn
    from footprinter.api.server import create_app

    db_path = tool_db.execute("PRAGMA database_list").fetchone()[2]

    app = create_app()

    def override():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
