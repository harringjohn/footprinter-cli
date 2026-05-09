"""Tests for footprinter.api.entities — entity read endpoints."""

from unittest.mock import patch

# Service module paths for patching
_SVC = "footprinter.api.entities"


class TestFileEndpoints:
    def test_files_list(self, api_client):
        with patch(f"{_SVC}.file_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/files")
            assert resp.status_code == 200

    def test_files_get_found(self, api_client):
        with patch(f"{_SVC}.file_service.get", return_value={"id": 1, "name": "test.txt"}):
            resp = api_client.get("/api/files/1")
            assert resp.status_code == 200

    def test_files_get_not_found(self, api_client):
        with patch(f"{_SVC}.file_service.get", return_value=None):
            resp = api_client.get("/api/files/9999")
            assert resp.status_code == 404


class TestEmailEndpoints:
    def test_emails_list(self, api_client):
        with patch(f"{_SVC}.email_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/emails")
            assert resp.status_code == 200

    def test_emails_get_found(self, api_client):
        with patch(f"{_SVC}.email_service.get", return_value={"id": 1, "subject": "Test"}):
            resp = api_client.get("/api/emails/1")
            assert resp.status_code == 200

    def test_emails_get_not_found(self, api_client):
        with patch(f"{_SVC}.email_service.get", return_value=None):
            resp = api_client.get("/api/emails/9999")
            assert resp.status_code == 404


class TestChatEndpoints:
    def test_chats_list(self, api_client):
        with patch(f"{_SVC}.chat_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/chats")
            assert resp.status_code == 200

    def test_chats_get_found(self, api_client):
        with patch(f"{_SVC}.chat_service.get", return_value={"id": 1, "title": "Test"}):
            resp = api_client.get("/api/chats/1")
            assert resp.status_code == 200

    def test_chats_get_not_found(self, api_client):
        with patch(f"{_SVC}.chat_service.get", return_value=None):
            resp = api_client.get("/api/chats/9999")
            assert resp.status_code == 404


class TestProjectEndpoints:
    def test_projects_list(self, api_client):
        with patch(f"{_SVC}.project_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/projects")
            assert resp.status_code == 200

    def test_projects_get_found(self, api_client):
        with patch(f"{_SVC}.project_service.get", return_value={"id": 1, "name": "Test"}):
            resp = api_client.get("/api/projects/1")
            assert resp.status_code == 200

    def test_projects_get_not_found(self, api_client):
        with patch(f"{_SVC}.project_service.get", return_value=None):
            resp = api_client.get("/api/projects/9999")
            assert resp.status_code == 404

    def test_projects_list_with_include(self, api_client):
        """?include=files,folders is split into a list."""
        with patch(f"{_SVC}.project_service.list_", return_value={"items": [], "total": 0}) as mock:
            resp = api_client.get("/api/projects", params={"include": "files,folders"})
            assert resp.status_code == 200
            _, kwargs = mock.call_args
            assert kwargs["include"] == ["files", "folders"]


class TestClientEndpoints:
    def test_clients_list(self, api_client):
        with patch(f"{_SVC}.client_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/clients")
            assert resp.status_code == 200

    def test_clients_get_found(self, api_client):
        with patch(f"{_SVC}.client_service.get", return_value={"id": 1, "name": "Test"}):
            resp = api_client.get("/api/clients/1")
            assert resp.status_code == 200

    def test_clients_get_not_found(self, api_client):
        with patch(f"{_SVC}.client_service.get", return_value=None):
            resp = api_client.get("/api/clients/9999")
            assert resp.status_code == 404

    def test_clients_list_with_include(self, api_client):
        """?include=projects,aggregates is split into a list."""
        with patch(f"{_SVC}.client_service.list_", return_value={"items": [], "total": 0}) as mock:
            resp = api_client.get("/api/clients", params={"include": "projects,aggregates"})
            assert resp.status_code == 200
            _, kwargs = mock.call_args
            assert kwargs["include"] == ["projects", "aggregates"]


class TestFolderEndpoints:
    def test_folders_list(self, api_client):
        with patch(f"{_SVC}.folder_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/folders")
            assert resp.status_code == 200

    def test_folders_get_found(self, api_client):
        with patch(f"{_SVC}.folder_service.get", return_value={"id": 1, "path": "/test"}):
            resp = api_client.get("/api/folders/1")
            assert resp.status_code == 200

    def test_folders_get_not_found(self, api_client):
        with patch(f"{_SVC}.folder_service.get", return_value=None):
            resp = api_client.get("/api/folders/9999")
            assert resp.status_code == 404

    def test_folder_by_path(self, api_client):
        """GET /api/folders/by-path?path=... returns folder."""
        with patch(f"{_SVC}.folder_service.get_by_path", return_value={"id": 1, "path": "/test"}):
            resp = api_client.get("/api/folders/by-path", params={"path": "/test"})
            assert resp.status_code == 200

    def test_folder_by_path_not_found(self, api_client):
        with patch(f"{_SVC}.folder_service.get_by_path", return_value=None):
            resp = api_client.get("/api/folders/by-path", params={"path": "/nonexistent"})
            assert resp.status_code == 404


class TestVisitEndpoints:
    def test_visits_list(self, api_client):
        with patch(f"{_SVC}.visit_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/visits")
            assert resp.status_code == 200

    def test_visits_get_found(self, api_client):
        with patch(f"{_SVC}.visit_service.get", return_value={"id": 1, "url": "https://example.com"}):
            resp = api_client.get("/api/visits/1")
            assert resp.status_code == 200

    def test_visits_get_not_found(self, api_client):
        with patch(f"{_SVC}.visit_service.get", return_value=None):
            resp = api_client.get("/api/visits/9999")
            assert resp.status_code == 404


class TestLimitCap:
    """Pagination limit must be capped at MAX_LIMIT (200) on list endpoints."""

    def test_files_limit_above_cap_returns_422(self, api_client):
        resp = api_client.get("/api/files", params={"limit": 201})
        assert resp.status_code == 422
        body = resp.text.lower()
        assert "limit" in body and "less than or equal" in body

    def test_files_limit_at_cap_ok(self, api_client):
        with patch(f"{_SVC}.file_service.list_", return_value={"items": [], "total": 0}):
            resp = api_client.get("/api/files", params={"limit": 200})
            assert resp.status_code == 200

    def test_files_limit_zero_returns_422(self, api_client):
        resp = api_client.get("/api/files", params={"limit": 0})
        assert resp.status_code == 422

    def test_projects_limit_above_cap_returns_422(self, api_client):
        resp = api_client.get("/api/projects", params={"limit": 201})
        assert resp.status_code == 422
