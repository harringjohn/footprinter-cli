"""Smoke tests — assert new pipe terminology names are importable."""


def test_pipe_runner_importable():
    from footprinter.ingest.pipe_runner import PipeRunner

    assert hasattr(PipeRunner, "run_pipe")
    assert hasattr(PipeRunner, "run_pipes")


def test_protocol_types_importable():
    from footprinter.ingest.adapters.protocol import PipeAdapter, PipeResult, PipeStatus

    assert PipeStatus is not None
    assert PipeResult is not None
    assert PipeAdapter is not None


def test_pipe_adapter_has_pipe_name():
    from footprinter.ingest.adapters.protocol import PipeAdapter

    # PipeAdapter is a Protocol — check that pipe_name is declared
    annotations = {}
    for cls in PipeAdapter.__mro__:
        annotations.update(getattr(cls, "__annotations__", {}))
    # Protocol properties show up as methods, not annotations — check the class itself
    assert hasattr(PipeAdapter, "pipe_name")


def test_registry_pipes_importable():
    from footprinter.ingest.registry import CORE_PIPES, get_all_pipes  # noqa: F401


def test_connector_pipes_importable():
    from footprinter.connectors import get_connector_pipes  # noqa: F401
