"""Pipe registry — the "phone book" for pipes.

Knows what pipes exist, which adapter classes implement the core
data-source pipes, and provides functions to compute pipeline and
refresh pipe definitions dynamically.  Does NOT run anything — that's
the orchestrator's job.

v1.0 pipe set
--------------
Core (always available): local_folders, local_files, browser, chat.
Connector pipes are resolved dynamically from installed ConnectorSpecs.
"""

from footprinter.ingest.adapters import (
    BrowserAdapter,
    ChatAdapter,
    LocalFilesAdapter,
    LocalFoldersAdapter,
)
from footprinter.ingest.adapters.protocol import ErrorType, PipeResult, PipeStatus

# ── Source catalogue ────────────────────────────────────────────────

# Core v1.0 sources (work out of the box)
CORE_PIPES = [
    "local_folders",  # Scan ~/Work, ~/Personal folder structure
    "local_files",  # Index local files
    "browser",  # Browser history
    "chat",  # Claude/ChatGPT exports (status only - manual import)
]

# Not valid CLI targets; excluded from get_all_pipes()
FUTURE_PIPES = [
    "project_links",
    "summaries",
    "drive_links",
]

# Post-processing pipes — appended to every pipeline, run after all data-source pipes
POST_PIPES = [
    "access_resolution",  # Stamp visibility + permissions on ingested entities
]

# ── Core source registry (data-source adapters only) ─────────────────

CORE_PIPE_REGISTRY = {
    "local_folders": LocalFoldersAdapter,
    "local_files": LocalFilesAdapter,
    "browser": BrowserAdapter,
    "chat": ChatAdapter,
}

# ── Dynamic resolution functions ─────────────────────────────────────
#
# These replace the former static PIPELINES, REFRESH_PIPES, and ALL_PIPES
# dicts. They accept connector_pipelines — a dict mapping connector names
# to their pipe lists (e.g., {"google": ["drive_folders", "drive_files", "gmail"]}).
# The orchestrator builds this from ConnectorSpec metadata and passes it in,
# so this module never imports from connectors/.


def get_pipelines(
    connector_pipes: dict[str, type],
    connector_pipelines: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Compute pipeline definitions from core + installed connector pipes.

    Args:
        connector_pipes: Merged adapter registry from get_connector_pipes().
        connector_pipelines: Connector name → adapter pipe names. Built by
            the orchestrator from ConnectorSpec.adapter_entries.

    Returns pipeline name → ordered pipe list.
    """
    pipelines: dict[str, list[str]] = {
        "local": list(CORE_PIPES),
    }

    # Add a pipeline per connector whose pipes are in connector_pipes
    for name, pipes in (connector_pipelines or {}).items():
        installed = [s for s in pipes if s in connector_pipes]
        if installed:
            pipelines[name] = installed

    # "all" = core + all installed connector data-source pipes
    all_pipe_names = list(CORE_PIPES)
    for name, pipes in pipelines.items():
        if name == "local":
            continue
        for s in pipes:
            if s not in all_pipe_names:
                all_pipe_names.append(s)
    pipelines["all"] = all_pipe_names

    # Append post-processing pipes to every pipeline
    for name in pipelines:
        pipelines[name] = pipelines[name] + POST_PIPES

    return pipelines


def get_refresh_pipes(
    connector_pipes: dict[str, type],
    connector_pipelines: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Compute refresh pipe mappings from core + installed connector pipes.

    Args:
        connector_pipes: Merged adapter registry from get_connector_pipes().
        connector_pipelines: Connector name → adapter pipe names.

    Returns source name → pipe list. Each core source group gets a key,
    each connector gets a key, and individual connector pipes get keys.
    """
    refresh: dict[str, list[str]] = {
        "local": ["local_folders", "local_files"],
        "browser": ["browser"],
        "chat": ["chat"],
    }

    # Per-connector and per-pipe entries
    for name, pipes in (connector_pipelines or {}).items():
        installed = [s for s in pipes if s in connector_pipes]
        if not installed:
            continue

        # Connector-level key (e.g., "google")
        refresh[name] = installed

        # Per-pipe keys and grouped keys (e.g., "gmail", "drive")
        drive_pipes = []
        for pipe in installed:
            if pipe.startswith("drive_"):
                drive_pipes.append(pipe)
            else:
                # Individual pipe key (e.g., "gmail")
                refresh[pipe] = [pipe]

        if drive_pipes:
            refresh["drive"] = drive_pipes

    # "all" = everything
    all_pipe_names = list(CORE_PIPES)
    for name, pipes in (connector_pipelines or {}).items():
        for s in pipes:
            if s in connector_pipes and s not in all_pipe_names:
                all_pipe_names.append(s)
    refresh["all"] = all_pipe_names

    # Append post-processing pipes to every refresh group
    for name in refresh:
        refresh[name] = refresh[name] + POST_PIPES

    return refresh


def get_all_pipes(connector_pipes: dict[str, type]) -> list[str]:
    """Compute complete list of valid pipe names.

    Includes core pipes and installed connector pipes.
    FUTURE_PIPES entries are excluded — they are not registered pipes.
    """
    result = list(CORE_PIPES)
    for s in connector_pipes:
        if s not in result:
            result.append(s)
    for s in POST_PIPES:
        if s not in result:
            result.append(s)
    return result


def get_user_pipes(connector_pipes: dict[str, type]) -> list[str]:
    """Compute the user-selectable subset of pipes for CLI error messages.

    Includes core + installed connector data-source pipes. POST_PIPES are
    excluded — they run implicitly after every pipeline and aren't meant
    to be invoked directly via ``fp ingest --pipe``.
    """
    result = list(CORE_PIPES)
    for s in connector_pipes:
        if s not in result:
            result.append(s)
    return result


# ── Convenience re-exports ───────────────────────────────────────────

__all__ = [
    "CORE_PIPES",
    "FUTURE_PIPES",
    "POST_PIPES",
    "CORE_PIPE_REGISTRY",
    "get_pipelines",
    "get_refresh_pipes",
    "get_all_pipes",
    "get_user_pipes",
    "PipeResult",
    "PipeStatus",
    "ErrorType",
]
