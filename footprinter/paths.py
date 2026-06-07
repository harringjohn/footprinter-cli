"""Single source-of-truth path resolution for Footprinter.

Defaults to ``~/.footprinter/`` (installable-package compatible).
Every path can be overridden via environment variable.
"""

import importlib.resources
import logging
import os
from pathlib import Path


def get_home() -> Path:
    """Return FOOTPRINTER_HOME, creating it if needed.

    Priority: ``$FOOTPRINTER_HOME`` env var > ``~/.footprinter/`` default.
    """
    override = os.environ.get("FOOTPRINTER_HOME")
    home = Path(override) if override else Path.home() / ".footprinter"
    home.mkdir(parents=True, exist_ok=True)
    return home


def get_config_path() -> Path:
    """Return the config file path. Respects FOOTPRINTER_CONFIG env var."""
    env = os.environ.get("FOOTPRINTER_CONFIG")
    return Path(env) if env else get_home() / "config.yaml"


def get_db_path() -> Path:
    """Return the database path, creating the parent dir. Respects FOOTPRINTER_DB_PATH env var."""
    env = os.environ.get("FOOTPRINTER_DB_PATH")
    path = Path(env) if env else get_home() / "footprinter.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_chroma_path() -> Path:
    """Return the ChromaDB storage path."""
    return get_home() / "chroma"


def get_log_path() -> Path:
    """Return the setup log path."""
    return get_home() / "setup.log"


def get_run_logs_dir() -> Path:
    """Return the run logs directory (~/.footprinter/logs/), creating it if needed."""
    d = get_home() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prune_run_logs(keep: int = 20) -> int:
    """Remove old run log files, keeping the *keep* most recent.

    Only targets ``run_*.log`` files. Returns the number of files removed.
    """
    log_dir = get_run_logs_dir()
    logs = sorted(log_dir.glob("run_*.log"))
    to_remove = logs[: max(0, len(logs) - keep)]
    for path in to_remove:
        path.unlink()
    if to_remove:
        logging.getLogger("footprinter").debug(
            "Pruned %d old run log(s) from %s",
            len(to_remove),
            log_dir,
        )
    return len(to_remove)


def get_last_run_path() -> Path:
    """Return the path to the last run record (~/.footprinter/last_run.json)."""
    return get_home() / "last_run.json"


def get_run_lock_path() -> Path:
    """Return the path to the run lockfile (~/.footprinter/run.lock)."""
    return get_home() / "run.lock"


def get_bundled_path(name: str) -> Path:
    """Return path to a bundled resource file shipped with the package."""
    return importlib.resources.files("footprinter.bundled") / name


