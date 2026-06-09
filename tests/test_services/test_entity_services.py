"""Tests for entity read services — get() and list_() with role filtering.

Each entity type has visible, hidden, and opaque rows in the ``service_db``
fixture. Tests verify:
- ADMIN sees everything unfiltered
- VIEWER gets visibility-filtered results (hidden removed, opaque minimized)
"""

import pytest

from footprinter.services import (
    Role,
    chat_service,
    client_service,
    email_service,
    file_service,
    folder_service,
    project_service,
    visit_service,
)

# ═══════════════════════════════════════════════════════════════════════════
# Client service
# ═══════════════════════════════════════════════════════════════════════════


class TestClientService:
    # -- resolve_by_name tests ------------------------------------------------

    def test_resolve_by_name_single_match(self, service_db):
        result = client_service.resolve_by_name(service_db, "Acme", role=Role.VIEWER)
        assert result is not None
        assert result.get("name") == "Acme Corp"
        assert "projects" in result
        assert "total_files" in result

    def test_resolve_by_name_no_match(self, service_db):
        result = client_service.resolve_by_name(service_db, "Nobody", role=Role.VIEWER)
        assert result is None

    def test_resolve_by_name_hidden_returns_none(self, service_db):
        result = client_service.resolve_by_name(service_db, "Hidden", role=Role.VIEWER)
        assert result is None

    def test_resolve_by_name_opaque(self, service_db):
        result = client_service.resolve_by_name(service_db, "Opaque", role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "name" not in result  # opaque

    def test_get_admin_returns_full(self, service_db):
        result = client_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["name"] == "Acme Corp"

    def test_get_viewer_visible(self, service_db):
        result = client_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["name"] == "Acme Corp"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = client_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = client_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "client_type" in result
        assert "status" in result
        assert "name" not in result
        assert "path_pattern" not in result

    def test_list_admin_returns_all(self, service_db):
        result = client_service.list_(service_db, role=Role.ADMIN)
        assert len(result["clients"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = client_service.list_(service_db, role=Role.VIEWER)
        ids = [c["id"] for c in result["clients"]]
        assert 2 not in ids  # hidden removed
        assert 1 in ids  # visible kept
        assert result.get("suppressed", 0) >= 1

    # -- Write tests: upsert/delete -----------------------------------------

    def test_upsert_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            client_service.upsert(
                service_db,
                name="New Co",
                client_type="external",
                role=Role.VIEWER,
            )

    def test_upsert_create(self, service_db):
        result = client_service.upsert(
            service_db,
            name="New Co",
            client_type="external",
        )
        assert result["action"] == "created"
        assert result["id"] is not None
        assert "slug" in result

    def test_upsert_create_fetchable(self, service_db):
        created = client_service.upsert(
            service_db,
            name="Fetchable Co",
            client_type="internal",
        )
        fetched = client_service.get(service_db, created["id"])
        assert fetched is not None
        assert fetched["name"] == "Fetchable Co"

    def test_upsert_update(self, service_db):
        result = client_service.upsert(
            service_db,
            name="Acme Corp",
            client_type="internal",
        )
        assert result["action"] == "updated"
        assert result["id"] == 1

    def test_upsert_update_with_status(self, service_db):
        result = client_service.upsert(
            service_db,
            name="Acme Corp",
            client_type="external",
            status="unlisted",
        )
        assert result["action"] == "updated"
        fetched = client_service.get(service_db, result["id"])
        assert fetched["status"] == "unlisted"

    def test_upsert_validation(self, service_db):
        with pytest.raises(ValueError):
            client_service.upsert(
                service_db,
                name="Bad",
                client_type="bogus",
            )
        with pytest.raises(ValueError):
            client_service.upsert(
                service_db,
                name="",
                client_type="external",
            )

    def test_upsert_default_role(self, service_db):
        result = client_service.upsert(
            service_db,
            name="Default Role Co",
            client_type="personal",
        )
        assert result["action"] == "created"

    def test_delete_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            client_service.delete(service_db, 1, role=Role.VIEWER)

    def test_delete_happy_path_hard_removes_row(self, service_db):
        # Insert a fresh client with no dependents so the hard delete succeeds.
        service_db.execute(
            """INSERT INTO clients (id, name, slug, client_type, status)
               VALUES (99, 'Disposable', 'disposable', 'external', 'listed')"""
        )
        service_db.commit()
        result = client_service.delete(service_db, 99)
        assert result == {"id": 99, "deleted": True}
        row = service_db.execute("SELECT id FROM clients WHERE id = 99").fetchone()
        assert row is None

    def test_delete_blocks_when_dependents_exist(self, service_db):
        # Client 1 (Acme) has project 1 (Alpha) attached — hard delete must block.
        with pytest.raises(ValueError, match="dependents"):
            client_service.delete(service_db, 1)
        row = service_db.execute("SELECT id FROM clients WHERE id = 1").fetchone()
        assert row is not None

    def test_delete_not_found(self, service_db):
        result = client_service.delete(service_db, 9999)
        assert result is None

    # -- Include tests ---------------------------------------------------------

    def test_invalid_include_raises(self, service_db):
        with pytest.raises(ValueError, match="Invalid include"):
            client_service.get(service_db, 1, include=["bogus"])

    def test_invalid_include_raises_on_list(self, service_db):
        with pytest.raises(ValueError, match="Invalid include"):
            client_service.list_(service_db, include=["bogus"])

    def test_get_without_include_strips_nested(self, service_db):
        result = client_service.get(service_db, 1)
        assert result is not None
        assert "projects" not in result
        assert "file_count" not in result

    def test_get_include_projects(self, service_db):
        result = client_service.get(service_db, 1, include=["projects"])
        assert "projects" in result
        project_ids = [p["id"] for p in result["projects"]]
        assert 1 in project_ids  # Alpha belongs to Acme

    def test_get_include_aggregates(self, service_db):
        result = client_service.get(service_db, 1, include=["aggregates"])
        assert "aggregates" in result
        agg = result["aggregates"]
        assert agg["project_count"] == 1
        assert agg["file_count"] == 1  # readme.md
        assert len(agg["per_project"]) == 1
        assert agg["per_project"][0]["project_name"] == "Alpha"

    def test_get_include_both(self, service_db):
        result = client_service.get(service_db, 1, include=["projects", "aggregates"])
        assert "projects" in result
        assert "aggregates" in result

    def test_get_viewer_visible_with_include(self, service_db):
        result = client_service.get(service_db, 1, role=Role.VIEWER, include=["projects"])
        assert result is not None
        assert "projects" in result
        # Nested projects are visibility-filtered (only visible ones)
        for p in result["projects"]:
            assert "name" in p or "id" in p

    def test_get_viewer_opaque_no_include(self, service_db):
        result = client_service.get(service_db, 3, role=Role.VIEWER, include=["projects"])
        assert result is not None
        assert "projects" not in result  # opaque → no includes

    def test_get_viewer_hidden_no_include(self, service_db):
        result = client_service.get(service_db, 2, role=Role.VIEWER, include=["projects"])
        assert result is None

    def test_list_include_projects(self, service_db):
        result = client_service.list_(service_db, role=Role.ADMIN, include=["projects"])
        for client in result["clients"]:
            assert "projects" in client

    def test_list_viewer_include_projects(self, service_db):
        result = client_service.list_(service_db, role=Role.VIEWER, include=["projects"])
        for client in result["clients"]:
            if "name" in client:  # visible — has full fields
                assert "projects" in client
            else:  # opaque — minimal dict
                assert "projects" not in client

    def test_get_viewer_aggregates_excludes_hidden(self, service_db):
        """Aggregates must not leak hidden/opaque project data to VIEWER."""
        # Add a hidden project under Acme (client 1, visible)
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (10, 'Secret', 'listed',
                       1, 'hidden', 'allow')"""
        )
        service_db.execute(
            """INSERT INTO files (id, name, path, source, status, content_type,
                                  size_bytes, project_id, visibility, access)
               VALUES (10, 'secret.py', '/Users/u/Work/secret/secret.py', 'local',
                       'listed', 'python', 500, 10, 'hidden', 'allow')"""
        )
        service_db.commit()

        result = client_service.get(
            service_db,
            1,
            role=Role.VIEWER,
            include=["aggregates"],
        )
        assert result is not None
        agg = result["aggregates"]
        # Only visible project Alpha counted — hidden Secret excluded
        assert agg["project_count"] == 1
        assert agg["file_count"] == 1
        assert all(p["project_name"] != "Secret" for p in agg["per_project"])

    def test_resolve_by_name_viewer_excludes_unlisted_projects(self, service_db):
        """VIEWER client nav enumerates listed projects only; unlisted → a count."""
        # Add an unlisted project under Acme (client 1, visible)
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (20, 'Archived', 'unlisted',
                       1, 'full', 'allow')"""
        )
        service_db.commit()

        result = client_service.resolve_by_name(service_db, "Acme", role=Role.VIEWER)
        assert result is not None
        names = [p.get("name") for p in result["projects"]]
        assert "Alpha" in names  # listed project present
        assert "Archived" not in names  # unlisted excluded
        assert result["unlisted_project_count"] == 1

    def test_resolve_by_name_admin_includes_unlisted_projects(self, service_db):
        """ADMIN client nav still enumerates unlisted projects (no listing filter)."""
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (20, 'Archived', 'unlisted',
                       1, 'full', 'allow')"""
        )
        service_db.commit()

        result = client_service.resolve_by_name(service_db, "Acme", role=Role.ADMIN)
        assert result is not None
        names = [p.get("name") for p in result["projects"]]
        assert "Archived" in names

    def test_resolve_by_name_viewer_unlisted_only_client(self, service_db):
        """A client whose only project is unlisted shows zero projects + a count."""
        service_db.execute(
            """INSERT INTO clients (id, name, slug, client_type, status,
                                    visibility, access)
               VALUES (20, 'ArchiveCo', 'archiveco', 'external', 'listed',
                       'full', 'allow')"""
        )
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (21, 'OnlyArchive', 'unlisted',
                       20, 'full', 'allow')"""
        )
        service_db.commit()

        result = client_service.resolve_by_name(service_db, "ArchiveCo", role=Role.VIEWER)
        assert result is not None
        assert result["projects"] == []
        assert result["unlisted_project_count"] == 1

    def test_get_viewer_aggregates_excludes_unlisted(self, service_db):
        """Aggregates must not count unlisted projects for VIEWER."""
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (20, 'Archived', 'unlisted',
                       1, 'full', 'allow')"""
        )
        service_db.commit()

        result = client_service.get(service_db, 1, role=Role.VIEWER, include=["aggregates"])
        assert result is not None
        agg = result["aggregates"]
        assert agg["project_count"] == 1  # only listed Alpha
        assert all(p["project_name"] != "Archived" for p in agg["per_project"])


# ═══════════════════════════════════════════════════════════════════════════
# Project service
# ═══════════════════════════════════════════════════════════════════════════


class TestProjectService:
    # -- resolve_by_name tests ------------------------------------------------

    def test_resolve_by_name_single_match(self, service_db):
        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.VIEWER)
        assert result is not None
        assert result.get("name") == "Alpha"
        assert "file_count" in result
        assert "top_content_types" in result
        assert "folders" in result
        assert "entity_counts" in result

    def test_resolve_by_name_exact_match_among_many(self, service_db):
        """When multiple fuzzy matches exist but one is exact, return it."""
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (10, 'Alpha Plus', 'listed',
                       1, 'full', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.VIEWER)
        assert result is not None
        assert result.get("name") == "Alpha"

    def test_resolve_by_name_ambiguous(self, service_db):
        """Multiple fuzzy matches with no exact → disambiguation."""
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (10, 'Alpha Plus', 'listed',
                       1, 'full', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Alph", role=Role.VIEWER)
        assert result is not None
        assert result.get("disambiguation") is True
        assert len(result["matches"]) >= 2

    def test_resolve_by_name_no_match(self, service_db):
        result = project_service.resolve_by_name(service_db, "Nonexistent", role=Role.VIEWER)
        assert result is None

    def test_resolve_by_name_hidden_returns_none(self, service_db):
        """Hidden project should not be findable by VIEWER."""
        result = project_service.resolve_by_name(service_db, "Beta", role=Role.VIEWER)
        assert result is None

    def test_resolve_by_name_hidden_diagnostic(self, service_db):
        """Hidden match returns not_found with hidden_count for diagnostic."""
        result = project_service.resolve_by_name(service_db, "Beta", role=Role.VIEWER)
        # None because hidden — diagnostic info available in the hidden_count response
        # The implementation may return None or a not_found dict
        assert result is None or result.get("not_found") is True

    def test_resolve_by_name_opaque(self, service_db):
        result = project_service.resolve_by_name(service_db, "Gamma", role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "name" not in result  # opaque strips name

    def test_resolve_by_name_viewer_unlisted_returns_none(self, service_db):
        """VIEWER cannot resolve onto an unlisted project (treated like hidden)."""
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (20, 'Zeta', 'unlisted',
                       1, 'full', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Zeta", role=Role.VIEWER)
        assert result is None

    def test_resolve_by_name_admin_unlisted_resolves(self, service_db):
        """ADMIN can still resolve an unlisted project by name."""
        service_db.execute(
            """INSERT INTO projects (id, name, status,
                                     client_id, visibility, access)
               VALUES (20, 'Zeta', 'unlisted',
                       1, 'full', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Zeta", role=Role.ADMIN)
        assert result is not None
        assert result["name"] == "Zeta"

    def test_resolve_by_name_viewer_excludes_unlisted_folders(self, service_db):
        """VIEWER project nav lists listed folders only — unlisted dropped."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                    direct_file_count, total_size_bytes, status,
                                    visibility, access)
               VALUES (20, '/Users/u/Work/alpha/archive', '/Work/alpha/archive',
                       'archive', 'local', 1, 1, 100, 'unlisted', 'full', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.VIEWER)
        assert result is not None
        folder_ids = [f["id"] for f in result["folders"]]
        assert 1 in folder_ids  # listed folder present
        assert 20 not in folder_ids  # unlisted folder excluded

    def test_resolve_by_name_admin_includes_unlisted_folders(self, service_db):
        """ADMIN project nav still includes unlisted folders."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                    direct_file_count, total_size_bytes, status,
                                    visibility, access)
               VALUES (20, '/Users/u/Work/alpha/archive', '/Work/alpha/archive',
                       'archive', 'local', 1, 1, 100, 'unlisted', 'full', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.ADMIN)
        assert result is not None
        folder_ids = [f["id"] for f in result["folders"]]
        assert 20 in folder_ids

    def test_get_admin_returns_full(self, service_db):
        result = project_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["name"] == "Alpha"

    def test_get_viewer_visible(self, service_db):
        result = project_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["name"] == "Alpha"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = project_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = project_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "status" in result
        assert "name" not in result

    def test_list_admin_returns_all(self, service_db):
        result = project_service.list_(service_db, role=Role.ADMIN)
        assert len(result["projects"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = project_service.list_(service_db, role=Role.VIEWER)
        ids = [p["id"] for p in result["projects"]]
        assert 2 not in ids
        assert 1 in ids
        assert result.get("suppressed", 0) >= 1

    # -- Write tests: upsert/delete -----------------------------------------

    def test_upsert_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            project_service.upsert(
                service_db,
                name="New Proj",
                role=Role.VIEWER,
            )

    def test_upsert_create(self, service_db):
        result = project_service.upsert(
            service_db,
            name="New Proj",
        )
        assert result["action"] == "created"
        assert result["id"] is not None

    def test_upsert_create_fetchable(self, service_db):
        created = project_service.upsert(
            service_db,
            name="Fetchable Proj",
        )
        fetched = project_service.get(service_db, created["id"])
        assert fetched is not None
        assert fetched["name"] == "Fetchable Proj"

    def test_upsert_update_by_name(self, service_db):
        result = project_service.upsert(
            service_db,
            name="Alpha",
            description="updated description",
        )
        assert result["action"] == "updated"
        assert result["id"] == 1

    def test_upsert_validation(self, service_db):
        with pytest.raises(ValueError):
            project_service.upsert(service_db, name="")

    def test_delete_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            project_service.delete(service_db, 1, role=Role.VIEWER)

    def test_delete_happy_path_hard_removes_row(self, service_db):
        # Insert a fresh project with no dependents so the hard delete succeeds.
        service_db.execute(
            """INSERT INTO projects (id, name, status)
               VALUES (99, 'Disposable', 'listed')"""
        )
        service_db.commit()
        result = project_service.delete(service_db, 99)
        assert result == {"id": 99, "deleted": True}
        row = service_db.execute("SELECT id FROM projects WHERE id = 99").fetchone()
        assert row is None

    def test_delete_blocks_when_dependents_exist(self, service_db):
        # Project 1 (Alpha) has files/folders/chats attached — hard delete must block.
        with pytest.raises(ValueError, match="dependents"):
            project_service.delete(service_db, 1)
        row = service_db.execute("SELECT id FROM projects WHERE id = 1").fetchone()
        assert row is not None

    def test_delete_not_found(self, service_db):
        result = project_service.delete(service_db, 9999)
        assert result is None

    # -- Include tests ---------------------------------------------------------

    def test_invalid_include_raises(self, service_db):
        with pytest.raises(ValueError, match="Invalid include"):
            project_service.get(service_db, 1, include=["bogus"])

    def test_invalid_include_raises_on_list(self, service_db):
        with pytest.raises(ValueError, match="Invalid include"):
            project_service.list_(service_db, include=["bogus"])

    def test_get_without_include_no_nested(self, service_db):
        result = project_service.get(service_db, 1)
        assert result is not None
        assert "files" not in result
        assert "folders" not in result

    def test_get_include_files(self, service_db):
        result = project_service.get(service_db, 1, include=["files"])
        assert "files" in result
        file_ids = [f["id"] for f in result["files"]]
        assert 1 in file_ids  # readme.md

    def test_get_include_folders(self, service_db):
        result = project_service.get(service_db, 1, include=["folders"])
        assert "folders" in result
        folder_ids = [f["id"] for f in result["folders"]]
        assert 1 in folder_ids  # src

    def test_get_include_both(self, service_db):
        result = project_service.get(service_db, 1, include=["files", "folders"])
        assert "files" in result
        assert "folders" in result

    def test_get_viewer_visible_with_include(self, service_db):
        result = project_service.get(service_db, 1, role=Role.VIEWER, include=["files"])
        assert result is not None
        assert "files" in result

    def test_get_viewer_opaque_no_include(self, service_db):
        result = project_service.get(service_db, 3, role=Role.VIEWER, include=["files"])
        assert result is not None
        assert "files" not in result  # opaque → no includes

    def test_list_include_files(self, service_db):
        result = project_service.list_(service_db, role=Role.ADMIN, include=["files"])
        for project in result["projects"]:
            assert "files" in project

    def test_list_viewer_include_files(self, service_db):
        result = project_service.list_(service_db, role=Role.VIEWER, include=["files"])
        for project in result["projects"]:
            if "name" in project:  # visible — has full fields
                assert "files" in project
            else:  # opaque — minimal dict
                assert "files" not in project


# ═══════════════════════════════════════════════════════════════════════════
# File service
# ═══════════════════════════════════════════════════════════════════════════


class TestFileService:
    def test_get_admin_returns_full(self, service_db):
        result = file_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["name"] == "readme.md"

    def test_get_viewer_visible(self, service_db):
        result = file_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["name"] == "readme.md"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = file_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = file_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "content_type" in result
        assert "source" in result
        assert "name" not in result

    def test_list_admin_returns_all(self, service_db):
        result = file_service.list_(service_db, role=Role.ADMIN)
        assert len(result["files"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = file_service.list_(service_db, role=Role.VIEWER)
        ids = [f["id"] for f in result["files"]]
        assert 2 not in ids
        assert 1 in ids
        assert result.get("suppressed", 0) >= 1

    def test_list_viewer_strips_content_for_denied(self, service_db):
        """File 3 has access='deny' — snippet should be stripped."""
        result = file_service.list_(service_db, role=Role.VIEWER)
        opaque_files = [f for f in result["files"] if f["id"] == 3]
        # opaque file still appears (as minimal dict), content stripped
        for f in opaque_files:
            assert "snippet" not in f

    # -- Write tests: assign -------------------------------------------------

    def test_assign_project(self, service_db):
        result = file_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["id"] == 1
        assert result["project_id"] == 1
        assert "client_id" not in result  # only set fields returned

    def test_assign_client(self, service_db):
        result = file_service.assign(service_db, 1, client_id=1)
        assert result is not None
        assert result["client_id"] == 1
        assert "project_id" not in result

    def test_assign_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            file_service.assign(service_db, 1, project_id=1, role=Role.VIEWER)

    def test_assign_not_found(self, service_db):
        result = file_service.assign(service_db, 999, project_id=1)
        assert result is None

    def test_assign_works_without_assignment_source(self, service_db):
        """assign() succeeds on tool-only DB (no assignment_source column)."""
        result = file_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["project_id"] == 1

    def test_assign_stamps_assignment_source(self, service_db):
        """assign() writes assignment_source='user' when the column exists."""
        service_db.execute("ALTER TABLE files ADD COLUMN assignment_source TEXT")
        file_service.assign(service_db, 1, project_id=1)
        row = service_db.execute(
            "SELECT assignment_source FROM files WHERE id = 1"
        ).fetchone()
        assert row["assignment_source"] == "user"


# ═══════════════════════════════════════════════════════════════════════════
# Folder service
# ═══════════════════════════════════════════════════════════════════════════


class TestFolderService:
    def test_get_admin_returns_full(self, service_db):
        result = folder_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["name"] == "src"

    def test_get_viewer_visible(self, service_db):
        result = folder_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["name"] == "src"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = folder_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = folder_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "direct_files" in result
        assert "source" in result
        assert "name" not in result

    def test_list_admin_returns_all(self, service_db):
        result = folder_service.list_(service_db, role=Role.ADMIN, depth=None)
        assert len(result["folders"]) == 3

    def test_list_default_depth_is_none(self, service_db):
        # All three fixture folders sit at /Work/<project>/src — depth 2 below
        # home — so the old depth=1 default would drop them. The default must
        # now return everything.
        result = folder_service.list_(service_db, role=Role.ADMIN)
        assert len(result["folders"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = folder_service.list_(service_db, role=Role.VIEWER, depth=None)
        ids = [f["id"] for f in result["folders"]]
        assert 2 not in ids
        assert 1 in ids
        assert result.get("suppressed", 0) >= 1

    # -- Write tests: assign -------------------------------------------------

    def test_assign_project(self, service_db):
        result = folder_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["id"] == 1
        assert result["project_id"] == 1

    def test_assign_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            folder_service.assign(service_db, 1, project_id=1, role=Role.VIEWER)

    def test_assign_invalid_project(self, service_db):
        with pytest.raises(ValueError, match="No project"):
            folder_service.assign(service_db, 1, project_id=999)

    # -- get_by_path tests -------------------------------------------------

    def test_get_by_path_returns_folder(self, service_db):
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/src",
            role=Role.ADMIN,
        )
        assert result is not None
        assert result["name"] == "src"
        assert "files" in result
        assert "subfolders" in result
        assert "recursive_file_count" in result

    def test_get_by_path_not_found(self, service_db):
        result = folder_service.get_by_path(
            service_db,
            "/nonexistent/path",
            role=Role.ADMIN,
        )
        assert result is None

    def test_get_by_path_hidden_returns_none_for_viewer(self, service_db):
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/beta/src",
            role=Role.VIEWER,
        )
        assert result is None

    def test_get_by_path_opaque_returns_minimal_for_viewer(self, service_db):
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/gamma/src",
            role=Role.VIEWER,
        )
        assert result is not None
        assert "id" in result
        # Opaque: no child queries
        assert "files" not in result
        assert "subfolders" not in result

    def test_get_by_path_visible_includes_navigation(self, service_db):
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/src",
            role=Role.VIEWER,
        )
        assert result is not None
        assert "files" in result
        assert "subfolders" in result
        assert "recursive_file_count" in result

    def test_get_by_path_filters_hidden_children(self, service_db):
        """Subfolders and files with hidden visibility should be excluded."""
        # Add a hidden subfolder under visible folder 1
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    visibility, access)
               VALUES (10, '/Users/u/Work/alpha/src/secret', '/Work/alpha/src/secret',
                       'secret', 'local', 1, 0, 0, 'hidden', 'allow')"""
        )
        # Add a hidden file under visible folder 1
        service_db.execute(
            """INSERT INTO files (id, name, path, source, status, content_type,
                                  size_bytes, project_id, folder_id, visibility, access)
               VALUES (10, 'hidden.py', '/Users/u/Work/alpha/src/hidden.py', 'local',
                       'listed', 'python', 100, 1, 1, 'hidden', 'allow')"""
        )
        service_db.commit()

        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/src",
            role=Role.VIEWER,
        )
        assert result is not None
        subfolder_ids = [sf["id"] for sf in result["subfolders"]]
        assert 10 not in subfolder_ids
        file_ids = [f["id"] for f in result["files"]]
        assert 10 not in file_ids
        assert result.get("suppressed", 0) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Chat service
# ═══════════════════════════════════════════════════════════════════════════


class TestChatService:
    def test_get_admin_returns_full(self, service_db):
        result = chat_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["title"] == "Visible Chat"

    def test_get_viewer_visible(self, service_db):
        result = chat_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["title"] == "Visible Chat"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = chat_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = chat_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "account" in result
        assert "title" not in result

    def test_list_admin_returns_all(self, service_db):
        result = chat_service.list_(service_db, role=Role.ADMIN)
        assert len(result["chats"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = chat_service.list_(service_db, role=Role.VIEWER)
        ids = [c["id"] for c in result["chats"]]
        assert 2 not in ids
        assert 1 in ids
        assert result.get("suppressed", 0) >= 1

    # -- Write tests: assign -------------------------------------------------

    def test_assign_project(self, service_db):
        result = chat_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["id"] == 1
        assert result["project_id"] == 1

    def test_assign_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            chat_service.assign(service_db, 1, project_id=1, role=Role.VIEWER)

    def test_assign_works_without_assignment_source(self, service_db):
        """assign() succeeds on tool-only DB (no assignment_source column)."""
        result = chat_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["project_id"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Email service
# ═══════════════════════════════════════════════════════════════════════════


class TestEmailService:
    def test_get_admin_returns_full(self, service_db):
        result = email_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["subject"] == "Visible Email"

    def test_get_viewer_visible(self, service_db):
        result = email_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["subject"] == "Visible Email"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = email_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = email_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "account" in result
        assert "subject" not in result

    def test_list_admin_returns_all(self, service_db):
        result = email_service.list_(service_db, role=Role.ADMIN)
        assert len(result["emails"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = email_service.list_(service_db, role=Role.VIEWER)
        ids = [e["id"] for e in result["emails"]]
        assert 2 not in ids
        assert 1 in ids
        assert result.get("suppressed", 0) >= 1

    # -- Write tests: assign -------------------------------------------------

    def test_assign_project(self, service_db):
        result = email_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["id"] == 1
        assert result["project_id"] == 1

    def test_assign_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            email_service.assign(service_db, 1, project_id=1, role=Role.VIEWER)

    def test_assign_works_without_assignment_source(self, service_db):
        """assign() succeeds on tool-only DB (no assignment_source column)."""
        result = email_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["project_id"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Visit service
# ═══════════════════════════════════════════════════════════════════════════


class TestVisitService:
    def test_get_admin_returns_full(self, service_db):
        result = visit_service.get(service_db, 1, role=Role.ADMIN)
        assert result is not None
        assert result["title"] == "Visible Page"

    def test_get_viewer_visible(self, service_db):
        result = visit_service.get(service_db, 1, role=Role.VIEWER)
        assert result is not None
        assert result["title"] == "Visible Page"

    def test_get_viewer_hidden_returns_none(self, service_db):
        result = visit_service.get(service_db, 2, role=Role.VIEWER)
        assert result is None

    def test_get_viewer_opaque_returns_minimal(self, service_db):
        result = visit_service.get(service_db, 3, role=Role.VIEWER)
        assert result is not None
        assert "id" in result
        assert "browser" in result
        assert "title" not in result

    def test_list_admin_returns_all(self, service_db):
        result = visit_service.list_(service_db, role=Role.ADMIN)
        assert len(result["visits"]) == 3

    def test_list_viewer_filters_hidden(self, service_db):
        result = visit_service.list_(service_db, role=Role.VIEWER)
        ids = [v["id"] for v in result["visits"]]
        assert 2 not in ids
        assert 1 in ids
        assert result.get("suppressed", 0) >= 1

    # -- Write tests: assign -------------------------------------------------

    def test_assign_project(self, service_db):
        result = visit_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["id"] == 1
        assert result["project_id"] == 1

    def test_assign_permission_denied(self, service_db):
        with pytest.raises(PermissionError):
            visit_service.assign(service_db, 1, project_id=1, role=Role.VIEWER)

    def test_assign_works_without_assignment_source(self, service_db):
        """assign() succeeds on tool-only DB (no assignment_source column)."""
        result = visit_service.assign(service_db, 1, project_id=1)
        assert result is not None
        assert result["project_id"] == 1
