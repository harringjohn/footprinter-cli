"""
Tests for the source registry module.

Verifies that core source constants, dynamic pipeline/refresh resolution,
source registry, and re-exports are correctly defined in
footprinter.ingest.registry.
"""


# ── Fake connector sources for testing dynamic resolution ──────────────

GOOGLE_CONNECTOR_SOURCES = {
    "drive_folders": "DriveFoldersAdapter",
    "drive_files": "DriveFilesAdapter",
    "gmail": "GmailAdapter",
}

GOOGLE_CONNECTOR_PIPELINES = {
    "google": ["drive_folders", "drive_files", "gmail"],
}


class TestCoreConstants:
    """Tests for CORE_PIPES, CONNECTOR_PIPES, FUTURE_PIPES (static lists)."""

    def test_core_sources_has_4_entries(self):
        """CORE_PIPES lists the 4 always-available sources."""
        from footprinter.ingest.registry import CORE_PIPES

        assert len(CORE_PIPES) == 4
        assert set(CORE_PIPES) == {"local_folders", "local_files", "browser", "chat"}

    def test_future_sources_contains_deferred_stages(self):
        """FUTURE_PIPES lists stages deferred from v1.0."""
        from footprinter.ingest.registry import FUTURE_PIPES

        assert "project_links" in FUTURE_PIPES
        assert "summaries" in FUTURE_PIPES
        assert "drive_links" in FUTURE_PIPES


class TestGetPipelines:
    """Tests for get_pipelines() dynamic pipeline resolution."""

    def test_no_connectors_returns_local_and_all(self):
        """With no connector sources, only 'local' and 'all' pipelines exist."""
        from footprinter.ingest.registry import CORE_PIPES, POST_PIPES, get_pipelines

        pipelines = get_pipelines({})
        assert set(pipelines.keys()) == {"local", "all"}
        assert pipelines["local"] == list(CORE_PIPES) + POST_PIPES
        assert pipelines["all"] == list(CORE_PIPES) + POST_PIPES

    def test_google_connector_adds_google_pipeline(self):
        """With Google connector sources, a 'google' pipeline appears."""
        from footprinter.ingest.registry import POST_PIPES, get_pipelines

        pipelines = get_pipelines(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        assert "google" in pipelines
        assert set(pipelines["google"]) == {"drive_folders", "drive_files", "gmail", *POST_PIPES}

    def test_all_pipeline_includes_core_and_connectors(self):
        """'all' pipeline merges core + connector sources."""
        from footprinter.ingest.registry import CORE_PIPES, get_pipelines

        pipelines = get_pipelines(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        all_stages = pipelines["all"]
        for s in CORE_PIPES:
            assert s in all_stages
        for s in GOOGLE_CONNECTOR_SOURCES:
            assert s in all_stages

    def test_no_full_or_drive_pipeline(self):
        """Static 'full' and 'drive' pipeline names are retired."""
        from footprinter.ingest.registry import get_pipelines

        pipelines = get_pipelines(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        assert "full" not in pipelines
        assert "drive" not in pipelines

    def test_local_pipeline_excludes_connectors(self):
        """'local' pipeline only has core sources, even with connectors installed."""
        from footprinter.ingest.registry import CORE_PIPES, POST_PIPES, get_pipelines

        pipelines = get_pipelines(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        assert pipelines["local"] == list(CORE_PIPES) + POST_PIPES

    def test_excludes_processor_stages(self):
        """Pipeline stages come from adapter_entries, not ConnectorSpec.pipes
        (which may include processors like drive_links)."""
        from footprinter.ingest.registry import get_pipelines

        pipelines = get_pipelines(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        assert "drive_links" not in pipelines.get("google", [])
        for stages in pipelines.values():
            assert "drive_links" not in stages
            assert "project_links" not in stages
            assert "summaries" not in stages


class TestGetRefreshSources:
    """Tests for get_refresh_pipes() dynamic refresh resolution."""

    def test_no_connectors_has_core_keys(self):
        """Without connectors, refresh sources have core keys + 'all'."""
        from footprinter.ingest.registry import get_refresh_pipes

        refresh = get_refresh_pipes({})
        assert "local" in refresh
        assert "browser" in refresh
        assert "chat" in refresh
        assert "all" in refresh

    def test_no_connectors_excludes_drive_and_gmail(self):
        """Without connectors, no 'drive' or 'gmail' refresh keys."""
        from footprinter.ingest.registry import get_refresh_pipes

        refresh = get_refresh_pipes({})
        assert "drive" not in refresh
        assert "gmail" not in refresh

    def test_google_connector_adds_google_key(self):
        """With Google connector, 'google' refresh key is added."""
        from footprinter.ingest.registry import POST_PIPES, get_refresh_pipes

        refresh = get_refresh_pipes(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        assert "google" in refresh
        assert set(refresh["google"]) == {"drive_folders", "drive_files", "gmail", *POST_PIPES}

    def test_google_connector_adds_per_stage_keys(self):
        """Each connector stage gets its own refresh key, plus post-processing."""
        from footprinter.ingest.registry import POST_PIPES, get_refresh_pipes

        refresh = get_refresh_pipes(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        assert "gmail" in refresh
        assert refresh["gmail"] == ["gmail", *POST_PIPES]
        assert "drive" in refresh
        assert set(refresh["drive"]) == {"drive_folders", "drive_files", *POST_PIPES}

    def test_all_key_includes_everything(self):
        """'all' refresh key includes core + connector stages."""
        from footprinter.ingest.registry import CORE_PIPES, get_refresh_pipes

        refresh = get_refresh_pipes(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        all_stages = refresh["all"]
        for s in CORE_PIPES:
            assert s in all_stages
        for s in GOOGLE_CONNECTOR_SOURCES:
            assert s in all_stages

    def test_no_processor_stages_in_refresh(self):
        """Refresh sources never include processor stages."""
        from footprinter.ingest.registry import get_refresh_pipes

        refresh = get_refresh_pipes(GOOGLE_CONNECTOR_SOURCES, GOOGLE_CONNECTOR_PIPELINES)
        for name, stages in refresh.items():
            assert "project_links" not in stages, f"'{name}' has project_links"
            assert "summaries" not in stages, f"'{name}' has summaries"
            assert "drive_links" not in stages, f"'{name}' has drive_links"


class TestGetAllSources:
    """Tests for get_all_pipes() dynamic source list."""

    def test_no_connectors_is_core_plus_post(self):
        """Without connectors, all sources = core + post-processing (future stages excluded)."""
        from footprinter.ingest.registry import CORE_PIPES, POST_PIPES, get_all_pipes

        result = get_all_pipes({})
        assert set(result) == set(CORE_PIPES) | set(POST_PIPES)

    def test_with_google_includes_connector_stages(self):
        """With Google connector, connector stage names appear."""
        from footprinter.ingest.registry import get_all_pipes

        result = get_all_pipes(GOOGLE_CONNECTOR_SOURCES)
        for s in GOOGLE_CONNECTOR_SOURCES:
            assert s in result

    def test_excludes_future_sources(self):
        """Future sources excluded — not valid for --stages invocation."""
        from footprinter.ingest.registry import get_all_pipes

        result = get_all_pipes(GOOGLE_CONNECTOR_SOURCES)
        assert "project_links" not in result
        assert "summaries" not in result
        assert "drive_links" not in result


class TestGetUserPipes:
    """Tests for get_user_pipes() — user-selectable subset for error messages."""

    def test_no_connectors_is_core_only(self):
        """Without connectors, user pipes = CORE_PIPES exactly (no POST_PIPES)."""
        from footprinter.ingest.registry import CORE_PIPES, get_user_pipes

        result = get_user_pipes({})
        assert result == list(CORE_PIPES)
        assert "access_resolution" not in result

    def test_includes_connector_sources(self):
        """Connector data-source pipes are included; POST_PIPES still excluded."""
        from footprinter.ingest.registry import CORE_PIPES, get_user_pipes

        result = get_user_pipes(GOOGLE_CONNECTOR_SOURCES)
        for s in CORE_PIPES:
            assert s in result
        for s in GOOGLE_CONNECTOR_SOURCES:
            assert s in result
        assert "access_resolution" not in result

    def test_excludes_future_pipes(self):
        """Future pipes are not registered, so they don't appear."""
        from footprinter.ingest.registry import get_user_pipes

        result = get_user_pipes(GOOGLE_CONNECTOR_SOURCES)
        assert "project_links" not in result
        assert "summaries" not in result
        assert "drive_links" not in result


class TestCoreSourceRegistry:
    """Tests for the CORE_PIPE_REGISTRY in registry."""

    def test_core_source_registry_has_4_entries(self):
        """CORE_PIPE_REGISTRY has exactly 4 entries for core data-source stages.

        Connector adapters (Drive, Gmail) are discovered dynamically via
        get_connector_pipes() and merged by the pipeline runner.
        """
        from footprinter.ingest.registry import CORE_PIPE_REGISTRY

        assert len(CORE_PIPE_REGISTRY) == 4

    def test_core_source_registry_values_are_adapters(self):
        """Each registry value produces a zero-arg instance satisfying PipeAdapter."""
        from footprinter.ingest.adapters import PipeAdapter
        from footprinter.ingest.registry import CORE_PIPE_REGISTRY

        for stage, adapter_cls in CORE_PIPE_REGISTRY.items():
            instance = adapter_cls()
            assert isinstance(instance, PipeAdapter), f"{stage} adapter {adapter_cls} does not satisfy PipeAdapter"


class TestConnectorPipesRemoved:
    """CONNECTOR_PIPES must not exist — connector pipes are resolved dynamically."""

    def test_connector_pipes_not_in_all(self):
        from footprinter.ingest import registry

        assert "CONNECTOR_PIPES" not in registry.__all__

    def test_connector_pipes_attribute_gone(self):
        from footprinter.ingest import registry

        assert not hasattr(registry, "CONNECTOR_PIPES")


class TestReExports:
    """Tests for convenience re-exports from registry."""

    def test_re_exports_stage_result(self):
        """PipeResult should be importable from registry."""
        from footprinter.ingest.registry import PipeResult

        assert PipeResult is not None
        from footprinter.ingest.adapters.protocol import PipeResult as Original

        assert PipeResult is Original

    def test_re_exports_stage_status(self):
        """PipeStatus should be importable from registry."""
        from footprinter.ingest.registry import PipeStatus

        assert PipeStatus is not None
        from footprinter.ingest.adapters.protocol import PipeStatus as Original

        assert PipeStatus is Original
