"""Tests for footprinter.api.status — status endpoint."""

from unittest.mock import patch


class TestStatusEndpoint:
    """Test GET /api/status."""

    def test_get_status_returns_200(self, api_client):
        """GET /api/status returns 200."""
        resp = api_client.get("/api/status")
        assert resp.status_code == 200

    def test_get_status_uses_admin_role(self, api_client):
        """Status endpoint calls status_service with Role.ADMIN."""
        from footprinter.services.roles import Role

        with patch("footprinter.api.status.status_service.get_status", return_value={}) as mock:
            resp = api_client.get("/api/status")
            assert resp.status_code == 200
            mock.assert_called_once()
            _, kwargs = mock.call_args
            assert kwargs["role"] == Role.ADMIN
