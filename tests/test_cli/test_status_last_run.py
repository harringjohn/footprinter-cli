"""Tests for --last-run status display."""

import json


def test_print_last_run_shows_table(capsys):
    """print_last_run() renders per-stage rows with timing."""
    from footprinter.cli.status import print_last_run

    record = {
        "started_at": "2026-03-04T12:00:00+00:00",
        "completed_at": "2026-03-04T12:05:00+00:00",
        "mode": "incremental",
        "total_elapsed_seconds": 300.0,
        "stages": [
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 120.5,
                "files_indexed": 5000,
            },
            {
                "stage": "browser",
                "status": "completed",
                "elapsed_seconds": 30.0,
                "urls_indexed": 1200,
            },
        ],
    }
    print_last_run(record)
    output = capsys.readouterr().out
    assert "local_files" in output
    assert "browser" in output


def test_print_last_run_handles_no_record(capsys):
    """Gracefully handles None input."""
    from footprinter.cli.status import print_last_run

    print_last_run(None)
    output = capsys.readouterr().out
    assert "No pipeline runs recorded" in output


def test_print_last_run_flags_zero_results(capsys, monkeypatch):
    """Connector pipe with 0 results gets a warning when checks are dynamic."""
    from footprinter.cli import status as status_mod
    from footprinter.cli.status import print_last_run

    monkeypatch.setattr(
        status_mod,
        "_build_zero_result_checks",
        lambda: {"drive_files": "files_indexed", "browser": "urls_indexed"},
    )

    record = {
        "started_at": "2026-03-04T12:00:00+00:00",
        "completed_at": "2026-03-04T12:01:00+00:00",
        "mode": "full",
        "total_elapsed_seconds": 60.0,
        "stages": [
            {
                "stage": "drive_files",
                "status": "completed",
                "elapsed_seconds": 5.0,
                "files_indexed": 0,
            },
        ],
    }
    print_last_run(record)
    output = capsys.readouterr().out
    # Should contain a warning indicator for zero results
    assert "0 results" in output.lower() or "warning" in output.lower() or "⚠" in output


def test_status_last_run_flag_routes_correctly(monkeypatch, tmp_path):
    """--last-run flag dispatches before subcommand check."""
    import argparse

    monkeypatch.setenv("FOOTPRINTER_HOME", str(tmp_path))

    # Write a test record
    record = {
        "started_at": "2026-03-04T12:00:00+00:00",
        "completed_at": "2026-03-04T12:01:00+00:00",
        "mode": "incremental",
        "total_elapsed_seconds": 60.0,
        "stages": [
            {"stage": "local_files", "status": "completed", "elapsed_seconds": 60.0, "files_indexed": 100},
        ],
    }
    last_run_path = tmp_path / "last_run.json"
    last_run_path.write_text(json.dumps(record))

    # Simulate the _handle function being called with --last-run
    from footprinter.cli import status_cmd

    # Build args object mimicking what argparse would produce
    args = argparse.Namespace(
        last_run=True,
        command=None,
        json=False,
        detail=None,
        limit=50,
    )

    # Should not raise — it dispatches to print_last_run
    status_cmd._handle(args)


# ---------------------------------------------------------------------------
# Interrupted run display
# ---------------------------------------------------------------------------


def test_print_last_run_shows_interrupted_marker(capsys):
    """Interrupted record shows '(interrupted)' in output."""
    from footprinter.cli.status import print_last_run

    record = {
        "started_at": "2026-03-04T12:00:00+00:00",
        "completed_at": "2026-03-04T12:02:00+00:00",
        "mode": "incremental",
        "interrupted": True,
        "total_elapsed_seconds": 120.0,
        "stages": [
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 120.0,
                "files_indexed": 500,
            },
        ],
    }
    print_last_run(record)
    output = capsys.readouterr().out
    assert "interrupted" in output.lower()


def test_print_last_run_flags_zero_drive_folders(capsys, monkeypatch):
    """Connector pipe with 0 folders gets a warning when checks are dynamic."""
    from footprinter.cli import status as status_mod
    from footprinter.cli.status import print_last_run

    monkeypatch.setattr(
        status_mod,
        "_build_zero_result_checks",
        lambda: {"drive_folders": "folders_indexed", "browser": "urls_indexed"},
    )

    record = {
        "started_at": "2026-03-04T12:00:00+00:00",
        "completed_at": "2026-03-04T12:01:00+00:00",
        "mode": "full",
        "total_elapsed_seconds": 60.0,
        "stages": [
            {
                "stage": "drive_folders",
                "status": "completed",
                "elapsed_seconds": 3.0,
                "folders_indexed": 0,
            },
        ],
    }
    print_last_run(record)
    output = capsys.readouterr().out
    # Should contain a warning indicator for zero results
    assert "0 results" in output.lower() or "warning" in output.lower() or "⚠" in output


def test_print_last_run_normal_run_no_interrupted_marker(capsys):
    """Normal record (no interrupted key) does NOT show 'interrupted'."""
    from footprinter.cli.status import print_last_run

    record = {
        "started_at": "2026-03-04T12:00:00+00:00",
        "completed_at": "2026-03-04T12:05:00+00:00",
        "mode": "incremental",
        "total_elapsed_seconds": 300.0,
        "stages": [
            {
                "stage": "local_files",
                "status": "completed",
                "elapsed_seconds": 300.0,
                "files_indexed": 5000,
            },
        ],
    }
    print_last_run(record)
    output = capsys.readouterr().out
    assert "interrupted" not in output.lower()


# ---------------------------------------------------------------------------
# Orchestrator main() saves run record
# ---------------------------------------------------------------------------


# test_orchestrator_main_saves_run_record removed — main() no longer exists
# in indexer.cli. Run record saving is now handled by cli/router.py.


# ---------------------------------------------------------------------------
# Dynamic zero-result checks
# ---------------------------------------------------------------------------


def test_build_zero_result_checks_core_only(monkeypatch):
    """With no connectors installed, only core checks are returned."""
    import footprinter.connectors as conn_mod
    from footprinter.cli.status import _build_zero_result_checks

    monkeypatch.setattr(conn_mod, "discover_connectors", lambda: {})

    result = _build_zero_result_checks()
    assert result == {"browser": "urls_indexed"}


def test_build_zero_result_checks_with_connector(monkeypatch):
    """Connector zero_result_checks merge into the core set."""
    import footprinter.connectors as conn_mod
    from footprinter.cli.status import _build_zero_result_checks
    from footprinter.connectors import ConnectorSpec

    fake_spec = ConnectorSpec(
        name="fake",
        extra="fake",
        description="Fake connector",
        pipes=("fake_pipe",),
        probe_module="fake_module",
        config_sections=("fake_section",),
        setup_hook="fake.setup",
        remove_packages=(),
        zero_result_checks=(("fake_pipe", "items_indexed"),),
    )
    monkeypatch.setattr(conn_mod, "discover_connectors", lambda: {"fake": fake_spec})
    monkeypatch.setattr(conn_mod, "is_installed", lambda spec: True)

    result = _build_zero_result_checks()
    assert result == {"browser": "urls_indexed", "fake_pipe": "items_indexed"}
