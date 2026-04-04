"""Tests for footprinter.api.semantic — semantic search endpoint."""

from unittest.mock import patch


class TestSemanticEndpoint:
    """Test GET /api/semantic."""

    def test_semantic_search_returns_results(self, api_client):
        """GET /api/semantic?query=authentication returns 200."""
        with patch(
            "footprinter.api.semantic.semantic_service.semantic_search",
            return_value={"results": []},
        ):
            resp = api_client.get("/api/semantic", params={"query": "authentication"})
            assert resp.status_code == 200

    def test_semantic_short_query_422(self, api_client):
        """Query shorter than 3 chars returns 422."""
        resp = api_client.get("/api/semantic", params={"query": "ab"})
        assert resp.status_code == 422

    def test_semantic_invalid_source_422(self, api_client):
        """Invalid source returns 422."""
        with patch(
            "footprinter.api.semantic.semantic_service.semantic_search",
            return_value={"results": []},
        ):
            resp = api_client.get("/api/semantic", params={"query": "test query", "source": "invalid"})
            assert resp.status_code == 422
            assert "invalid" in resp.json()["detail"].lower()

    def test_semantic_uses_admin_role(self, api_client):
        """Semantic endpoint calls semantic_service with Role.ADMIN."""
        from footprinter.services.roles import Role

        with patch(
            "footprinter.api.semantic.semantic_service.semantic_search",
            return_value={"results": []},
        ) as mock:
            api_client.get("/api/semantic", params={"query": "test query"})
            _, kwargs = mock.call_args
            assert kwargs["role"] == Role.ADMIN
