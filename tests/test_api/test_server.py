"""Tests for footprinter.api.server — app factory and core endpoints."""


class TestAppFactory:
    """Test create_app() and core endpoints."""

    def test_create_app_returns_fastapi(self):
        """create_app() returns a FastAPI instance."""
        from fastapi import FastAPI

        from footprinter.api.server import create_app

        app = create_app()
        assert isinstance(app, FastAPI)

    def test_health_endpoint(self, api_client):
        """GET /health returns {"status": "ok"}."""
        resp = api_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_openapi_json_available(self, api_client):
        """GET /openapi.json returns 200."""
        resp = api_client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "paths" in data

    def test_db_not_initialized_returns_503(self, api_client):
        """DatabaseNotInitializedError is caught and returns 503."""
        from fastapi import Depends

        from footprinter.api.db import get_conn
        from footprinter.utils.exceptions import DatabaseNotInitializedError

        app = api_client.app

        @app.get("/test-db-error")
        def trigger_error(conn=Depends(get_conn)):
            raise DatabaseNotInitializedError()

        resp = api_client.get("/test-db-error")
        assert resp.status_code == 503
        assert "not initialized" in resp.json()["detail"].lower()
