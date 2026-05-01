"""Tests for the run logging file handler helper and log pruning."""

import logging

from footprinter.paths import prune_run_logs
from footprinter.utils.logging_config import add_file_handler


def test_add_file_handler_creates_handler(tmp_path):
    """Returns a FileHandler writing to the given path."""
    log_path = tmp_path / "test.log"
    handler = add_file_handler(log_path)

    try:
        assert isinstance(handler, logging.FileHandler)
        assert handler in logging.root.handlers

        # Write a message and verify it appears in the file
        logger = logging.getLogger("test.run_logging")
        logger.setLevel(logging.DEBUG)
        logger.info("hello from test")

        handler.flush()
        content = log_path.read_text()
        assert "hello from test" in content
    finally:
        logging.root.removeHandler(handler)
        handler.close()


def test_add_file_handler_suppresses_schema_noise(tmp_path):
    """Schema noise filtered on the handler, not by mutating logger level."""
    schema_logger = logging.getLogger("footprinter.ingest.db.schema")
    original_level = schema_logger.level

    log_path = tmp_path / "test.log"
    handler = add_file_handler(log_path)

    try:
        # Logger level must NOT be mutated
        assert schema_logger.level == original_level

        # Handler must have a filter that rejects schema INFO
        info_record = schema_logger.makeRecord(schema_logger.name, logging.INFO, "", 0, "info msg", (), None)
        warn_record = schema_logger.makeRecord(schema_logger.name, logging.WARNING, "", 0, "warn msg", (), None)
        assert handler.filter(info_record) is False or handler.filter(info_record) == 0
        assert handler.filter(warn_record)
    finally:
        logging.root.removeHandler(handler)
        handler.close()


def test_schema_logger_level_restored_after_cleanup(tmp_path):
    """Schema logger level is unchanged after file handler is removed."""
    schema_logger = logging.getLogger("footprinter.ingest.db.schema")
    original_level = schema_logger.level

    log_path = tmp_path / "test.log"
    handler = add_file_handler(log_path)
    logging.root.removeHandler(handler)
    handler.close()

    assert schema_logger.level == original_level


def test_add_file_handler_cleanup(tmp_path):
    """Handler can be removed from root logger."""
    log_path = tmp_path / "test.log"
    handler = add_file_handler(log_path)

    assert handler in logging.root.handlers
    logging.root.removeHandler(handler)
    handler.close()
    assert handler not in logging.root.handlers


# ---------------------------------------------------------------------------
# prune_run_logs tests
# ---------------------------------------------------------------------------


def test_prune_run_logs_keeps_recent(tmp_path, monkeypatch):
    """Creates 25 logs, prunes to 20, keeps the 20 most recent by name."""
    for i in range(25):
        (tmp_path / f"run_20260101_{i:06d}.log").touch()

    monkeypatch.setattr("footprinter.paths.get_run_logs_dir", lambda: tmp_path)
    removed = prune_run_logs(keep=20)

    remaining = sorted(tmp_path.glob("run_*.log"))
    assert removed == 5
    assert len(remaining) == 20
    # The oldest 5 (000000–000004) should be gone
    assert remaining[0].name == "run_20260101_000005.log"


def test_prune_run_logs_noop_when_under_limit(tmp_path, monkeypatch):
    """No files removed when count is under the limit."""
    for i in range(5):
        (tmp_path / f"run_20260101_{i:06d}.log").touch()

    monkeypatch.setattr("footprinter.paths.get_run_logs_dir", lambda: tmp_path)
    removed = prune_run_logs(keep=20)

    assert removed == 0
    assert len(list(tmp_path.glob("run_*.log"))) == 5


def test_prune_run_logs_ignores_non_log_files(tmp_path, monkeypatch):
    """Non-matching files are never touched."""
    for i in range(25):
        (tmp_path / f"run_20260101_{i:06d}.log").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / "debug.log").touch()

    monkeypatch.setattr("footprinter.paths.get_run_logs_dir", lambda: tmp_path)
    removed = prune_run_logs(keep=20)

    assert removed == 5
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "debug.log").exists()
    assert len(list(tmp_path.glob("run_*.log"))) == 20


def test_prune_run_logs_empty_dir(tmp_path, monkeypatch):
    """Empty directory raises no error."""
    monkeypatch.setattr("footprinter.paths.get_run_logs_dir", lambda: tmp_path)
    removed = prune_run_logs(keep=20)

    assert removed == 0
