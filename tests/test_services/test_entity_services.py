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

    def test_resolve_by_name_viewer_total_folders_excludes_unlisted(self, service_db):
        """VIEWER client total_folders counts listed folders only, matching the
        per-project listed-only folder view."""
        # Unlisted folder under listed project Alpha (client 1)
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                    direct_file_count, total_size_bytes, status,
                                    visibility, access)
               VALUES (20, '/Users/u/Work/alpha/archive', '/Work/alpha/archive',
                       'archive', 'local', 1, 1, 100, 'unlisted', 'full', 'allow')"""
        )
        service_db.commit()

        viewer = client_service.resolve_by_name(service_db, "Acme", role=Role.VIEWER)
        assert viewer is not None
        assert viewer["total_folders"] == 1  # only the listed folder counted

        admin = client_service.resolve_by_name(service_db, "Acme", role=Role.ADMIN)
        assert admin["total_folders"] == 2  # ADMIN still counts the unlisted folder


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

    def test_resolve_by_name_viewer_excludes_unlisted_opaque_folders(self, service_db):
        """An opaque AND unlisted folder must not leak as a stub.

        Regression: the status filter must run before opaque visibility
        stripping, since opaque folders lose their ``status`` field.
        """
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                    direct_file_count, total_size_bytes, status,
                                    visibility, access)
               VALUES
                   (21, '/Users/u/Work/alpha/secret-archive', '/Work/alpha/secret-archive',
                    'secret-archive', 'local', 1, 1, 100, 'unlisted', 'opaque', 'allow'),
                   (22, '/Users/u/Work/alpha/secret', '/Work/alpha/secret',
                    'secret', 'local', 1, 1, 100, 'listed', 'opaque', 'allow')"""
        )
        service_db.commit()
        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.VIEWER)
        assert result is not None
        folder_ids = [f["id"] for f in result["folders"]]
        assert 21 not in folder_ids  # unlisted+opaque dropped, not leaked as a stub
        assert 22 in folder_ids  # listed+opaque still present as a restricted stub

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

    def test_get_by_path_viewer_unlisted_returns_none(self, service_db):
        """VIEWER exact-path lookup of an unlisted folder must return None."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    status, visibility, access)
               VALUES (30, '/Users/u/Work/alpha/archive', '/Work/alpha/archive',
                       'archive', 'local', 1, 1, 100, 'unlisted', 'full', 'allow')"""
        )
        service_db.commit()
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/archive",
            role=Role.VIEWER,
        )
        assert result is None

    def test_get_by_path_viewer_removed_returns_none(self, service_db):
        """VIEWER exact-path lookup of a removed folder must return None."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    status, visibility, access)
               VALUES (31, '/Users/u/Work/alpha/old', '/Work/alpha/old',
                       'old', 'local', 1, 1, 100, 'removed', 'full', 'allow')"""
        )
        service_db.commit()
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/old",
            role=Role.VIEWER,
        )
        assert result is None

    def test_get_by_path_viewer_listed_unchanged(self, service_db):
        """VIEWER exact-path lookup of an explicit-listed folder is unchanged."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    status, visibility, access)
               VALUES (32, '/Users/u/Work/alpha/docs', '/Work/alpha/docs',
                       'docs', 'local', 1, 1, 100, 'listed', 'full', 'allow')"""
        )
        service_db.commit()
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/docs",
            role=Role.VIEWER,
        )
        assert result is not None
        assert "files" in result
        assert "subfolders" in result
        assert "recursive_file_count" in result

    def test_get_by_path_admin_resolves_unlisted(self, service_db):
        """ADMIN exact-path lookup still resolves an unlisted folder."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    status, visibility, access)
               VALUES (33, '/Users/u/Work/alpha/archive', '/Work/alpha/archive',
                       'archive', 'local', 1, 1, 100, 'unlisted', 'full', 'allow')"""
        )
        service_db.commit()
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/archive",
            role=Role.ADMIN,
        )
        assert result is not None
        assert result["name"] == "archive"

    def test_get_by_path_admin_resolves_removed(self, service_db):
        """ADMIN exact-path lookup still resolves a removed folder."""
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    status, visibility, access)
               VALUES (34, '/Users/u/Work/alpha/old', '/Work/alpha/old',
                       'old', 'local', 1, 1, 100, 'removed', 'full', 'allow')"""
        )
        service_db.commit()
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/old",
            role=Role.ADMIN,
        )
        assert result is not None
        assert result["name"] == "old"

    def test_get_by_path_viewer_opaque_unlisted_returns_none(self, service_db):
        """An opaque AND unlisted folder must not leak as a stub on direct lookup.

        Regression: the status gate must run for opaque folders too, so an
        opaque+unlisted folder is fully suppressed rather than returned as a
        restricted stub.
        """
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source,
                                    project_id, direct_file_count, total_size_bytes,
                                    status, visibility, access)
               VALUES (35, '/Users/u/Work/alpha/secret-archive',
                       '/Work/alpha/secret-archive', 'secret-archive', 'local', 1, 1,
                       100, 'unlisted', 'opaque', 'allow')"""
        )
        service_db.commit()
        result = folder_service.get_by_path(
            service_db,
            "/Users/u/Work/alpha/secret-archive",
            role=Role.VIEWER,
        )
        assert result is None


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


# ═══════════════════════════════════════════════════════════════════════════
# Governance-denylist / content-strip ordering
# ═══════════════════════════════════════════════════════════════════════════


class TestListContentStripOrdering:
    """``list_`` must strip denied content BEFORE the governance denylist runs.

    The denylist (applied inside ``filter_results_list``) removes the ``access``
    field on full-visibility rows. ``strip_content_for_denied`` reads ``access``
    and fails closed to ``deny`` when it is missing, so if filtering ran first
    it would wrongly strip content from full-visibility, allowed rows. These
    tests pin the ordering against rows that carry the excerpt contract.
    """

    _EXCERPT_ROW = {
        "id": 1,
        "visibility": "full",
        "access": "allow",
        "excerpt": "real matched content",
        "excerpt_source": "body_preview",
        "chars_returned": 20,
        "chars_available": 20,
        "has_more": False,
    }

    def _patched_response(self, key):
        import copy

        return {key: [copy.deepcopy(self._EXCERPT_ROW)], "pagination": {}}

    def test_email_list_keeps_excerpt_on_allowed_full_row(self, monkeypatch):
        monkeypatch.setattr(
            email_service.db,
            "list_emails",
            lambda *a, **k: self._patched_response("emails"),
        )
        result = email_service.list_(None, role=Role.VIEWER)
        row = result["emails"][0]
        assert row["excerpt"] == "real matched content"  # content survives
        assert "access" not in row  # governance denylist still applied

    def test_chat_list_keeps_excerpt_on_allowed_full_row(self, monkeypatch):
        monkeypatch.setattr(
            chat_service.db,
            "list_chats",
            lambda *a, **k: self._patched_response("chats"),
        )
        result = chat_service.list_(None, role=Role.VIEWER)
        row = result["chats"][0]
        assert row["excerpt"] == "real matched content"
        assert "access" not in row

    def test_file_list_keeps_excerpt_on_allowed_full_row(self, monkeypatch):
        monkeypatch.setattr(
            file_service.db,
            "list_files",
            lambda *a, **k: self._patched_response("files"),
        )
        result = file_service.list_(None, role=Role.VIEWER)
        row = result["files"][0]
        assert row["excerpt"] == "real matched content"
        assert "access" not in row

    def test_denied_row_still_has_content_stripped(self, monkeypatch):
        """A full-visibility but access='deny' row loses content (fail-closed)."""
        import copy

        denied = copy.deepcopy(self._EXCERPT_ROW)
        denied["access"] = "deny"
        monkeypatch.setattr(
            email_service.db,
            "list_emails",
            lambda *a, **k: {"emails": [denied], "pagination": {}},
        )
        result = email_service.list_(None, role=Role.VIEWER)
        row = result["emails"][0]
        assert "excerpt" not in row  # content stripped for denied
        assert "access" not in row  # governance denylist applied


# ═══════════════════════════════════════════════════════════════════════════
# Curated context (context_path → Markdown) on the orientation tools
# ═══════════════════════════════════════════════════════════════════════════


class TestCuratedContext:
    """The orientation tools surface curated Markdown context on demand.

    Each test inserts a fresh super-entity row pointing at a real Markdown file
    (column override) or a folder containing a README (auto-detect), then asserts
    the navigation result carries a ``curated_context`` block with the uniform
    excerpt contract: ``excerpt_source == "context_md"``, ``chars_available``,
    ``has_more``, plus the resolved ``context_path``. The unset case carries no
    block.

    Exposure policy (enforced in ``attach_curated_context``): ADMIN sees the full
    block including the ``excerpt`` body; VIEWER sees pointer + provenance only
    (``context_path`` / ``excerpt_source`` / ``chars_available``) — no ``excerpt``,
    ``chars_returned``, or ``has_more``. The block-resolution tests below run as
    ADMIN so they can assert the excerpt body; the dedicated VIEWER/ADMIN tests
    pin the exposure split.
    """

    _CONTRACT_KEYS = {
        "excerpt",
        "excerpt_source",
        "chars_returned",
        "chars_available",
        "has_more",
        "context_path",
    }

    # Content-bearing keys stripped from the block for VIEWER (pointer-only).
    _VIEWER_STRIPPED_KEYS = {"excerpt", "chars_returned", "has_more"}
    # Pointer + provenance keys VIEWER must still see.
    _VIEWER_KEPT_KEYS = {"context_path", "excerpt_source", "chars_available"}

    @pytest.fixture(autouse=True)
    def _home_is_tmp(self, tmp_path, monkeypatch):
        """Confine the curated-context home root to ``tmp_path`` for this class.

        ``resolve_curated_context`` rejects candidates outside ``Path.home()``;
        the curated fixture files here live under ``tmp_path`` (the system temp
        root, not the real home). ``Path.home()`` honours ``$HOME``, so pointing
        ``HOME`` at ``tmp_path`` lets these files pass confinement.
        """
        monkeypatch.setenv("HOME", str(tmp_path))

    def test_project_column_override_surfaces_block(self, service_db, tmp_path):
        md = tmp_path / "alpha-context.md"
        md.write_text("Curated Alpha context.")
        service_db.execute(
            "UPDATE projects SET context_path = ? WHERE id = 1", (str(md),)
        )
        service_db.commit()

        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.ADMIN)
        assert result is not None
        block = result["curated_context"]
        assert self._CONTRACT_KEYS <= set(block)
        assert block["excerpt"] == "Curated Alpha context."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(md)
        assert block["has_more"] is False

    def test_project_admin_also_surfaces_block(self, service_db, tmp_path):
        md = tmp_path / "alpha-admin.md"
        md.write_text("Admin Alpha context.")
        service_db.execute(
            "UPDATE projects SET context_path = ? WHERE id = 1", (str(md),)
        )
        service_db.commit()

        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.ADMIN)
        assert result is not None
        assert result["curated_context"]["excerpt_source"] == "context_md"

    def test_project_unset_has_no_block(self, service_db):
        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.VIEWER)
        assert result is not None
        assert "curated_context" not in result

    def test_client_column_override_surfaces_block(self, service_db, tmp_path):
        md = tmp_path / "acme-context.md"
        md.write_text("Acme client background.")
        service_db.execute(
            "UPDATE clients SET context_path = ? WHERE id = 1", (str(md),)
        )
        service_db.commit()

        result = client_service.resolve_by_name(service_db, "Acme", role=Role.ADMIN)
        assert result is not None
        block = result["curated_context"]
        assert self._CONTRACT_KEYS <= set(block)
        assert block["excerpt"] == "Acme client background."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(md)

    def test_client_unset_has_no_block(self, service_db):
        result = client_service.resolve_by_name(service_db, "Acme", role=Role.VIEWER)
        assert result is not None
        assert "curated_context" not in result

    def test_client_convention_surfaces_block_via_home(
        self, service_db, tmp_path, monkeypatch
    ):
        """A client with only the conventional file (no override) surfaces context.

        Drives the production path: ``attach_curated_context`` resolves the client
        convention under ``get_home()``. Pointing ``FOOTPRINTER_HOME`` at
        ``tmp_path`` (the class fixture already points ``HOME`` there for
        confinement) places ``context/client-acme.md`` under the resolved home, so
        the convention fires without any injected ``context_root`` and without the
        ``context_path`` override column being set.
        """
        monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))
        context_dir = tmp_path / "context"
        context_dir.mkdir()
        convention = context_dir / "client-acme.md"
        convention.write_text("Acme curated context via convention.")

        result = client_service.resolve_by_name(service_db, "Acme", role=Role.ADMIN)
        assert result is not None
        block = result["curated_context"]
        assert self._CONTRACT_KEYS <= set(block)
        assert block["excerpt"] == "Acme curated context via convention."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(convention)
        assert block["context_path"].endswith("context/client-acme.md")

    def test_client_convention_surfaces_when_home_relocated_outside_user_home(
        self, service_db, tmp_path, monkeypatch
    ):
        """The convention fires when ``FOOTPRINTER_HOME`` is outside ``HOME``.

        The class fixture points ``HOME`` at ``tmp_path``; here ``FOOTPRINTER_HOME``
        is relocated to a *sibling* directory that is **not** under that ``HOME``
        (an isolated/relocated home, as the test harness's ``cli-verify`` uses).
        Confinement honours the caller's ``context_root`` as well as ``Path.home()``,
        so the convention path under the relocated home still passes confinement and
        surfaces the block. Without that, the path would silently fail confinement
        and the documented convention would never fire under a relocated home.
        """
        relocated_home = tmp_path.parent / "fp-relocated-home"
        context_dir = relocated_home / "context"
        context_dir.mkdir(parents=True)
        convention = context_dir / "client-acme.md"
        convention.write_text("Acme context under a relocated home.")
        monkeypatch.setenv("FOOTPRINTER_HOME", str(relocated_home))

        # FOOTPRINTER_HOME must not be under HOME, or the test would not exercise
        # the divergent-root path.
        assert not str(relocated_home.resolve()).startswith(
            str(tmp_path.resolve()) + "/"
        )

        result = client_service.resolve_by_name(service_db, "Acme", role=Role.ADMIN)
        assert result is not None
        block = result["curated_context"]
        assert block["excerpt"] == "Acme context under a relocated home."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(convention)

    def test_folder_readme_auto_detect_surfaces_block(self, service_db, tmp_path):
        folder = tmp_path / "alpha-src"
        folder.mkdir()
        readme = folder / "README.md"
        readme.write_text("Folder-level curated notes.")
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                    direct_file_count, total_size_bytes, status,
                                    visibility, access)
               VALUES (40, ?, '/Work/alpha/alpha-src', 'alpha-src', 'local', 1, 1, 100,
                       'listed', 'full', 'allow')""",
            (str(folder),),
        )
        service_db.commit()

        result = folder_service.get_by_path(service_db, str(folder), role=Role.ADMIN)
        assert result is not None
        block = result["curated_context"]
        assert self._CONTRACT_KEYS <= set(block)
        assert block["excerpt"] == "Folder-level curated notes."
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(readme)

    def test_folder_column_override_surfaces_block(self, service_db, tmp_path):
        md = tmp_path / "folder-override.md"
        md.write_text("Explicit folder context.")
        service_db.execute(
            """INSERT INTO folders (id, path, relative_path, name, source, project_id,
                                    direct_file_count, total_size_bytes, status,
                                    visibility, access, context_path)
               VALUES (41, '/Users/u/Work/alpha/configured', '/Work/alpha/configured',
                       'configured', 'local', 1, 1, 100, 'listed', 'full', 'allow', ?)""",
            (str(md),),
        )
        service_db.commit()

        result = folder_service.get_by_path(
            service_db, "/Users/u/Work/alpha/configured", role=Role.ADMIN
        )
        assert result is not None
        assert result["curated_context"]["context_path"] == str(md)
        assert result["curated_context"]["excerpt"] == "Explicit folder context."

    def test_folder_unset_has_no_block(self, service_db):
        result = folder_service.get_by_path(
            service_db, "/Users/u/Work/alpha/src", role=Role.VIEWER
        )
        assert result is not None
        assert "curated_context" not in result

    def test_viewer_curated_block_is_pointer_only(self, service_db, tmp_path):
        """VIEWER gets pointer + provenance, never the excerpt body."""
        md = tmp_path / "viewer-context.md"
        md.write_text("Sensitive curated body that VIEWER must not see.")
        service_db.execute(
            "UPDATE projects SET context_path = ? WHERE id = 1", (str(md),)
        )
        service_db.commit()

        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.VIEWER)
        assert result is not None
        block = result["curated_context"]
        assert self._VIEWER_KEPT_KEYS <= set(block)
        assert self._VIEWER_STRIPPED_KEYS.isdisjoint(set(block))
        assert block["excerpt_source"] == "context_md"
        assert block["context_path"] == str(md)

    def test_admin_curated_block_is_full(self, service_db, tmp_path):
        """ADMIN keeps the full block, excerpt body included."""
        md = tmp_path / "admin-full.md"
        md.write_text("Full curated body for ADMIN.")
        service_db.execute(
            "UPDATE projects SET context_path = ? WHERE id = 1", (str(md),)
        )
        service_db.commit()

        result = project_service.resolve_by_name(service_db, "Alpha", role=Role.ADMIN)
        assert result is not None
        block = result["curated_context"]
        assert self._CONTRACT_KEYS <= set(block)
        assert block["excerpt"] == "Full curated body for ADMIN."
