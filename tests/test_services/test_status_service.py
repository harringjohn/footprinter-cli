"""Tests for status_service — visibility-aware system status aggregates."""

from footprinter.services import Role, status_service


class TestStatusService:
    def test_viewer_returns_expected_shape(self, service_db):
        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert "sources" in result
        assert "files_by_source" in result
        assert "files_by_status" in result
        assert "projects_by_status" in result
        assert "emails_by_client" in result
        assert "chats_by_client" in result

    def test_viewer_excludes_hidden_client_emails(self, service_db):
        """Hidden-client emails should not appear in VIEWER counts."""
        # Assign email 2 to hidden client (id=2)
        service_db.execute("UPDATE emails SET client_id = 2 WHERE id = 2")
        service_db.commit()

        result = status_service.get_status(service_db, role=Role.VIEWER)
        # Source-level: emails count should exclude the hidden-client email
        assert result["sources"]["emails"]["count"] == 2  # visible + opaque, not hidden

    def test_viewer_excludes_hidden_client_chats(self, service_db):
        """Hidden-client chats should not appear in VIEWER counts."""
        service_db.execute("UPDATE chats SET client_id = 2 WHERE id = 2")
        service_db.commit()

        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert result["sources"]["chats"]["count"] == 2

    def test_viewer_excludes_hidden_client_messages(self, service_db):
        """Messages belonging to hidden-client chats should be excluded."""
        service_db.execute("UPDATE chats SET client_id = 2 WHERE id = 2")
        service_db.commit()

        result = status_service.get_status(service_db, role=Role.VIEWER)
        # Chat 2 has 1 message — should be excluded from count
        # Chat 1 has 2 messages, Chat 3 has 1 message = 3 visible
        assert result["sources"]["messages"]["count"] == 3

    def test_viewer_excludes_hidden_clients_from_count(self, service_db):
        result = status_service.get_status(service_db, role=Role.VIEWER)
        # 3 clients total, but client 2 is hidden → 2 visible
        assert result["sources"]["clients"]["count"] == 2

    def test_admin_returns_unfiltered(self, service_db):
        result = status_service.get_status(service_db, role=Role.ADMIN)
        # ADMIN uses the existing get_system_status path — different shape
        assert "has_data" in result
        assert "counts" in result
        assert "total" in result

    def test_viewer_files_by_source(self, service_db):
        result = status_service.get_status(service_db, role=Role.VIEWER)
        # 3 files total but file 2 is hidden — VIEWER sees 2
        assert "local" in result["files_by_source"]
        assert result["files_by_source"]["local"]["count"] == 2

    def test_viewer_files_by_status(self, service_db):
        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert "active" in result["files_by_status"]
        assert result["files_by_status"]["active"] == 3

    def test_viewer_projects_by_status(self, service_db):
        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert "active" in result["projects_by_status"]

    def test_missing_table_graceful(self, service_db):
        """If a table doesn't exist, the count should be 0, not a crash."""
        # Drop the visits table to simulate a missing table
        service_db.execute("DROP TABLE IF EXISTS visits")
        service_db.commit()

        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert result["sources"]["browser"]["count"] == 0
