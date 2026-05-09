"""Tests for footprinter.api.search — search endpoint."""

from unittest.mock import patch


class TestSearchEndpoint:
    """Test GET /api/search."""

    def test_search_default_sources(self, api_client):
        """GET /api/search?query=test returns 200."""
        with patch("footprinter.api.search.search_service.search", return_value={"results": []}) as mock:
            resp = api_client.get("/api/search", params={"query": "test"})
            assert resp.status_code == 200
            _, kwargs = mock.call_args
            assert kwargs["sources"] is None

    def test_search_with_source_filter(self, api_client):
        """Comma-separated sources param is split into a list."""
        with patch("footprinter.api.search.search_service.search", return_value={"results": []}) as mock:
            resp = api_client.get("/api/search", params={"query": "test", "sources": "files,emails"})
            assert resp.status_code == 200
            _, kwargs = mock.call_args
            assert kwargs["sources"] == ["files", "emails"]

    def test_search_empty_query(self, api_client):
        """No query param returns recent items (empty string default)."""
        with patch("footprinter.api.search.search_service.search", return_value={"results": []}) as mock:
            resp = api_client.get("/api/search")
            assert resp.status_code == 200
            _, kwargs = mock.call_args
            assert kwargs["query"] == ""

    def test_search_uses_admin_role(self, api_client):
        """Search endpoint calls search_service with Role.ADMIN."""
        from footprinter.services.roles import Role

        with patch("footprinter.api.search.search_service.search", return_value={"results": []}) as mock:
            api_client.get("/api/search", params={"query": "test"})
            _, kwargs = mock.call_args
            assert kwargs["role"] == Role.ADMIN

    def test_search_limit_above_cap_returns_422(self, api_client):
        """limit > MAX_LIMIT (200) returns 422."""
        resp = api_client.get("/api/search", params={"query": "test", "limit": 201})
        assert resp.status_code == 422
