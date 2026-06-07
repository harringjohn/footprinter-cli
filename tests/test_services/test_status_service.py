"""Tests for status_service — visibility-aware system status aggregates."""

from unittest.mock import patch

from footprinter.services import Role, status_service
from footprinter.services.status_service import (
    get_data_counts,
    get_source_health,
    visible_totals,
)


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
        assert "listed" in result["files_by_status"]
        assert result["files_by_status"]["listed"] == 3

    def test_viewer_projects_by_status(self, service_db):
        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert "listed" in result["projects_by_status"]

    def test_missing_table_graceful(self, service_db):
        """If a table doesn't exist, the count should be 0, not a crash."""
        # Drop the visits table to simulate a missing table
        service_db.execute("DROP TABLE IF EXISTS visits")
        service_db.commit()

        result = status_service.get_status(service_db, role=Role.VIEWER)
        assert result["sources"]["browser"]["count"] == 0


class TestGetDataCounts:
    """get_data_counts should be importable from the service layer and return
    the expected dict shape when given a sqlite3.Connection."""

    EXPECTED_KEYS = {
        "files", "files_total", "folders", "visits", "emails", "chats",
        "messages", "top_chats", "recent_files", "recent_uploads", "last_run",
        "entity_breakdown", "access_resolution", "chat_date_range",
        "remote_source_accounts",
    }

    def test_returns_expected_shape(self, service_db):
        counts = get_data_counts(service_db)
        assert isinstance(counts, dict)
        assert self.EXPECTED_KEYS.issubset(counts.keys())

    def test_files_total_matches_db(self, service_db):
        counts = get_data_counts(service_db)
        assert counts["files_total"] == 3

    def test_visits_count(self, service_db):
        counts = get_data_counts(service_db)
        assert counts["visits"] == 3

    def test_messages_count(self, service_db):
        counts = get_data_counts(service_db)
        assert counts["messages"] == 4

    def test_entity_breakdown_present(self, service_db):
        counts = get_data_counts(service_db)
        assert "files" in counts["entity_breakdown"]
        assert "total" in counts["entity_breakdown"]["files"]


class TestGetSourceHealth:
    """get_source_health should be importable from the service layer."""

    def test_empty_connectors(self):
        with patch("footprinter.services.status_service.discover_connectors", return_value={}):
            health = get_source_health({})
        assert health["connector_rows"] == []
        assert health["remote_enabled"] is False

    def test_semantic_disabled(self):
        with patch("footprinter.services.status_service.discover_connectors", return_value={}):
            health = get_source_health({})
        assert "semantic" in health
        assert health["semantic"]["enabled"] is False


class TestVisibleTotals:
    """visible_totals should be importable from the service layer."""

    def test_local_only(self):
        counts = {
            "files": {"local": {"count": 10, "size_mb": 5.0}},
            "folders": {"local": 3},
            "remote_source_accounts": {},
        }
        health = {"remote_enabled": False}
        result = visible_totals(counts, health)
        assert result == {"files": 10, "folders": 3, "size_mb": 5.0}

    def test_remote_excluded_when_disabled(self):
        counts = {
            "files": {
                "local": {"count": 10, "size_mb": 5.0},
                "gdrive": {"count": 20, "size_mb": 15.0},
            },
            "folders": {"local": 3, "gdrive": 7},
            "remote_source_accounts": {"gdrive": "user@example.com"},
        }
        health = {"remote_enabled": False}
        result = visible_totals(counts, health)
        assert result == {"files": 10, "folders": 3, "size_mb": 5.0}

    def test_remote_included_when_enabled(self):
        counts = {
            "files": {
                "local": {"count": 10, "size_mb": 5.0},
                "gdrive": {"count": 20, "size_mb": 15.0},
            },
            "folders": {"local": 3, "gdrive": 7},
            "remote_source_accounts": {"gdrive": "user@example.com"},
        }
        health = {"remote_enabled": True}
        result = visible_totals(counts, health)
        assert result == {"files": 30, "folders": 10, "size_mb": 20.0}
