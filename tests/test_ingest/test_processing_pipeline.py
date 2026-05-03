"""
Tests for the ProcessingPipeline class.

Validates phase registration, skip guards, and runner dispatch.
"""

from unittest.mock import MagicMock


import footprinter.ingest.processing as processing_module
from footprinter.ingest.adapters.protocol import PipeResult, PipeStatus
from footprinter.ingest.processing import (
    PIPE_TO_PHASE,
    PhaseSpec,
    ProcessingPipeline,
)


class TestPhaseSpec:
    """PhaseSpec dataclass construction and defaults."""

    def test_defaults(self):
        spec = PhaseSpec(name="test_phase")
        assert spec.name == "test_phase"
        assert spec.skip_guard is None
        assert spec.runner is None

    def test_with_all_fields(self):
        runner = MagicMock()
        guard = MagicMock()
        spec = PhaseSpec(
            name="rules_analysis",
            skip_guard=guard,
            runner=runner,
        )
        assert spec.skip_guard is guard
        assert spec.runner is runner


class TestProcessingPipeline:
    """Registration, stage detection, and dependency ordering."""

    def test_register_and_list_phases(self):
        pipeline = ProcessingPipeline()
        runner = MagicMock(return_value=PipeResult.completed("my_phase"))
        pipeline.register("my_phase", runner=runner)
        assert "my_phase" in pipeline.phase_names

    def test_is_processing_stage(self):
        pipeline = ProcessingPipeline()
        pipeline.register("drive_links", runner=MagicMock())
        assert pipeline.is_processing_pipe("drive_links") is True
        assert pipeline.is_processing_pipe("browser") is False

    def test_phase_names_returns_registration_order(self):
        pipeline = ProcessingPipeline()
        noop = MagicMock(return_value=PipeResult.completed("noop"))
        pipeline.register("summaries", runner=noop)
        pipeline.register("project_links", runner=noop)
        pipeline.register("drive_links", runner=noop)

        assert pipeline.phase_names == ["summaries", "project_links", "drive_links"]


class TestRunPhase:
    """Phase execution and result wrapping."""

    def test_run_phase_calls_runner(self):
        pipeline = ProcessingPipeline()
        runner = MagicMock(return_value=PipeResult.completed("rules_analysis", items=42))
        pipeline.register("rules_analysis", runner=runner)

        db = MagicMock()
        result = pipeline.run_phase("rules_analysis", db)

        runner.assert_called_once_with(db)
        assert isinstance(result, PipeResult)
        assert result.status == PipeStatus.COMPLETED

    def test_run_phase_unknown_returns_error(self):
        pipeline = ProcessingPipeline()
        db = MagicMock()
        result = pipeline.run_phase("nonexistent", db)
        assert result.status == PipeStatus.ERROR

    def test_run_phase_no_runner_returns_error(self):
        pipeline = ProcessingPipeline()
        pipeline.register("empty_phase")
        db = MagicMock()
        result = pipeline.run_phase("empty_phase", db)
        assert result.status == PipeStatus.ERROR


class TestSkipGuards:
    """Skip guards control whether a phase runs."""

    def test_guard_true_returns_skipped(self):
        pipeline = ProcessingPipeline()
        runner = MagicMock(return_value=PipeResult.completed("drive_links"))
        guard = MagicMock(return_value=True)
        pipeline.register("drive_links", runner=runner, skip_guard=guard)

        db = MagicMock()
        result = pipeline.run_phase("drive_links", db)

        runner.assert_not_called()
        assert result.status == PipeStatus.SKIPPED

    def test_guard_false_runs_normally(self):
        pipeline = ProcessingPipeline()
        runner = MagicMock(return_value=PipeResult.completed("drive_links"))
        guard = MagicMock(return_value=False)
        pipeline.register("drive_links", runner=runner, skip_guard=guard)

        db = MagicMock()
        result = pipeline.run_phase("drive_links", db)

        runner.assert_called_once_with(db)
        assert result.status == PipeStatus.COMPLETED

    def test_guard_exception_proceeds(self):
        """Guard errors are logged; the phase still runs."""
        pipeline = ProcessingPipeline()
        runner = MagicMock(return_value=PipeResult.completed("drive_links"))
        guard = MagicMock(side_effect=RuntimeError("boom"))
        pipeline.register("drive_links", runner=runner, skip_guard=guard)

        db = MagicMock()
        result = pipeline.run_phase("drive_links", db)

        runner.assert_called_once_with(db)
        assert result.status == PipeStatus.COMPLETED


class TestDictToPipeResultRemoved:
    """dict_to_pipe_result has been removed — runners return PipeResult directly."""

    def test_dict_to_pipe_result_removed(self):
        assert not hasattr(processing_module, "dict_to_pipe_result")


class TestNoRetentionImports:
    """Pipeline modules must not import retention_classifier or scoring at load time."""

    def test_pipeline_modules_do_not_import_retention(self):
        """Importing pipeline modules should not pull in retention_classifier or scoring."""
        import importlib
        import sys

        # Clear any cached imports of the target modules
        for mod_name in list(sys.modules):
            if "retention_classifier" in mod_name or mod_name == "footprinter.ingest.scoring":
                del sys.modules[mod_name]

        # Re-import pipeline modules
        importlib.reload(importlib.import_module("footprinter.ingest.registry"))
        importlib.reload(importlib.import_module("footprinter.ingest.processing"))
        importlib.reload(importlib.import_module("footprinter.ingest.orchestrator"))

        assert "footprinter.analysis.retention_classifier" not in sys.modules, (
            "retention_classifier was imported at module load time"
        )
        assert "footprinter.ingest.scoring" not in sys.modules, "scoring was imported at module load time"


class TestProcessorsIndependent:
    """Processors must run independently — no depends_on chain."""

    def test_register_without_depends_on(self):
        """All three processors can be registered with no dependencies."""
        pipeline = ProcessingPipeline()
        noop = MagicMock(return_value=PipeResult.completed("noop"))
        pipeline.register("drive_links", runner=noop)
        pipeline.register("project_links", runner=noop)
        pipeline.register("summaries", runner=noop)

        names = pipeline.phase_names
        assert set(names) == {"drive_links", "project_links", "summaries"}

    def test_each_processor_runs_alone(self):
        """Each processor can run individually without the others registered."""
        db = MagicMock()
        for name in ("drive_links", "project_links", "summaries"):
            pipeline = ProcessingPipeline()
            runner = MagicMock(return_value=PipeResult.completed(name, items=1))
            pipeline.register(name, runner=runner)
            result = pipeline.run_phase(name, db)
            assert result.status == PipeStatus.COMPLETED
            runner.assert_called_once_with(db)

    def test_phasespec_has_no_depends_on(self):
        """PhaseSpec no longer has a depends_on field."""
        assert not hasattr(PhaseSpec(name="test"), "depends_on")

    def test_register_has_no_depends_on_param(self):
        """ProcessingPipeline.register() no longer accepts depends_on."""
        import inspect

        sig = inspect.signature(ProcessingPipeline.register)
        assert "depends_on" not in sig.parameters


class TestStageToPhaseExcludesRulesAnalysis:
    """PIPE_TO_PHASE should not contain rules_analysis (excluded from v1.0)."""

    def test_stage_to_phase_excludes_rules_analysis(self):
        assert "rules_analysis" not in PIPE_TO_PHASE

    def test_processing_pipeline_no_run_rules_analysis(self):
        """ProcessingPipeline should not have run_rules_analysis method."""
        pipeline = ProcessingPipeline()
        assert not hasattr(pipeline, "run_rules_analysis")
