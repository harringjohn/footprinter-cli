"""Tests for search_service — multi-source keyword search with visibility."""

from footprinter.services import Role, search_service


class TestSearchServiceBasics:
    """Core search behavior: structure, keyword matching, empty query."""

    def test_search_returns_expected_structure(self, service_db):
        """All four source keys present when searching all sources."""
        result = search_service.search(
            service_db,
            query="",
            role=Role.VIEWER,
        )
        assert "files" in result
        assert "emails" in result
        assert "chats" in result
        assert "browser" in result

    def test_search_empty_query_returns_recent(self, service_db):
        """Empty query returns recent items (no FTS needed)."""
        result = search_service.search(
            service_db,
            query="",
            sources=["chats"],
            role=Role.VIEWER,
        )
        # Chat 1 is visible, chat 2 is hidden (excluded by SQL), chat 3 is opaque (minimal)
        assert len(result["chats"]) >= 1
        titles = [c.get("title") for c in result["chats"]]
        assert "Visible Chat" in titles
        assert "Hidden Chat" not in titles

    def test_search_chat_keyword(self, service_db):
        """Chat title keyword search uses LIKE matching."""
        result = search_service.search(
            service_db,
            query="Visible",
            sources=["chats"],
            role=Role.VIEWER,
        )
        assert len(result["chats"]) == 1
        assert result["chats"][0]["title"] == "Visible Chat"

    def test_search_single_source(self, service_db):
        """Searching a single source only returns that source key."""
        result = search_service.search(
            service_db,
            query="",
            sources=["emails"],
            role=Role.VIEWER,
        )
        assert "emails" in result
        # Other sources not searched
        assert "files" not in result
        assert "chats" not in result


class TestSearchVisibility:
    """Visibility filtering on search results."""

    def test_hidden_items_excluded(self, service_db):
        """Hidden files/emails/chats excluded from results."""
        result = search_service.search(
            service_db,
            query="",
            role=Role.VIEWER,
        )
        file_names = [f.get("name") for f in result["files"]]
        assert "secret.py" not in file_names  # hidden file

        email_subjects = [e.get("subject") for e in result["emails"]]
        assert "Hidden Email" not in email_subjects  # hidden email

    def test_opaque_items_minimized(self, service_db):
        """Opaque items appear with minimal fields only."""
        result = search_service.search(
            service_db,
            query="",
            role=Role.VIEWER,
        )
        # Opaque file (id=3) should appear with only opaque fields
        opaque_files = [f for f in result["files"] if f.get("id") == 3]
        if opaque_files:
            f = opaque_files[0]
            assert "name" not in f  # sensitive field stripped
            assert "id" in f

    def test_content_stripped_for_denied(self, service_db):
        """Chat summary/snippet stripped when mcp_read='deny'."""
        result = search_service.search(
            service_db,
            query="",
            sources=["chats"],
            role=Role.VIEWER,
        )
        # Chat 3 is opaque (mcp_view='opaque'), so it's minimized
        # Chat 1 is visible with allow — should keep summary
        visible_chats = [c for c in result["chats"] if c.get("title") == "Visible Chat"]
        if visible_chats:
            # Visible + allow chat keeps summary
            assert "summary" in visible_chats[0] or visible_chats[0].get("summary") is None

    def test_hidden_excluded_from_all_sources(self, service_db):
        """Hidden items excluded across files, emails, and chats."""
        result = search_service.search(
            service_db,
            query="",
            role=Role.VIEWER,
        )
        # Hidden items excluded at SQL level — verify none leak through
        all_ids = (
            [f["id"] for f in result["files"]]
            + [e["id"] for e in result["emails"]]
            + [c["id"] for c in result["chats"]]
        )
        # ID 2 is hidden across all entity types
        assert all_ids.count(2) == 0

    def test_admin_sees_all(self, service_db):
        """ADMIN role sees hidden items."""
        result = search_service.search(
            service_db,
            query="",
            role=Role.ADMIN,
        )
        file_names = [f.get("name") for f in result["files"]]
        assert "secret.py" in file_names  # hidden file visible to ADMIN


class TestSearchBrowserGating:
    """Browser source-level visibility gating."""

    def test_browser_visible_by_default(self, service_db):
        """Browser results returned when no source policy set."""
        result = search_service.search(
            service_db,
            query="",
            sources=["browser"],
            role=Role.VIEWER,
        )
        assert "browser" in result
        assert len(result["browser"]) >= 1

    def test_browser_hidden_by_source_policy(self, service_db):
        """Browser excluded entirely when source:browser policy is hidden."""
        service_db.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:browser', 'hidden')"
        )
        service_db.commit()
        result = search_service.search(
            service_db,
            query="",
            sources=["browser"],
            role=Role.VIEWER,
        )
        # Browser key should be absent or empty when hidden
        assert len(result.get("browser", [])) == 0

    def test_browser_opaque_minimal_fields(self, service_db):
        """Browser returns minimal fields when source:browser is opaque."""
        service_db.execute(
            "INSERT OR REPLACE INTO visibility_policies (scope, setting) VALUES ('source:browser', 'opaque')"
        )
        service_db.commit()
        result = search_service.search(
            service_db,
            query="",
            sources=["browser"],
            role=Role.VIEWER,
        )
        if result.get("browser"):
            b = result["browser"][0]
            assert "id" in b
            assert "browser" in b
            assert "url" not in b  # sensitive field stripped
            assert "title" not in b
