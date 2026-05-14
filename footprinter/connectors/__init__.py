"""Connector registry — metadata and discovery for optional integrations.

Defines ConnectorSpec (the metadata dataclass) and discover_connectors()
which finds installed connector plugins via importlib.metadata entry points.
Helper functions check install status, config, and credentials.
"""

import functools
import importlib
import importlib.metadata
import importlib.util
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class AuthType(str, Enum):
    """Authentication mechanism a connector uses — routing label for CLI dispatch."""

    OAUTH2 = "oauth2"
    BEARER = "bearer"
    IMPORT = "import"
    FILESYSTEM = "filesystem"


@dataclass(frozen=True)
class ConnectorSpec:
    """Metadata for an optional integration."""

    name: str
    extra: str
    description: str
    pipes: tuple[str, ...]
    probe_module: str
    config_sections: tuple[str, ...]
    setup_hook: str  # dotted path to callable
    remove_packages: tuple[str, ...]
    adapter_entries: dict[str, str] = field(default_factory=dict)  # pipe_name → "module:ClassName"
    services: tuple[str, ...] = ()
    seed_prefix: str = ""  # prefix for source_seed names (e.g. "gdrive")
    schema_extensions: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    auth_type: AuthType = AuthType.OAUTH2
    check_auth: str = ""  # dotted path to callable(config) → auth status string
    config_apply: str = ""  # dotted path to callable(config, result) → None
    health_check: str = ""  # dotted path to callable(config) → list[dict]
    read_file: str = ""  # dotted path to callable(external_id, account, mime_type) → bytes|None
    seed_label_fn: str = ""  # dotted path to callable(display) → str
    features: tuple[tuple[str, str, str, str], ...] = ()  # (name, probe_module, config_section, hint)
    zero_result_checks: tuple[tuple[str, str], ...] = ()  # (pipe_name, count_key) for status warnings


@functools.lru_cache(maxsize=1)
def discover_connectors() -> dict[str, ConnectorSpec]:
    """Discover connector plugins via entry points.

    Each entry point in the ``footprinter.connectors`` group should be
    either a :class:`ConnectorSpec` instance or a callable that returns one.
    The entry point name becomes the connector key.
    """
    connectors: dict[str, ConnectorSpec] = {}
    eps = importlib.metadata.entry_points(group="footprinter.connectors")
    for ep in eps:
        try:
            spec = ep.load()
            if callable(spec):
                spec = spec()
            connectors[ep.name] = spec
        except Exception:
            logger.warning("Failed to load connector entry point: %s", ep.name)
    return connectors


def is_installed(spec: ConnectorSpec) -> bool:
    """Check if the connector's pip extra is installed."""
    try:
        return importlib.util.find_spec(spec.probe_module) is not None
    except (ValueError, ModuleNotFoundError):
        return False


def is_configured(spec: ConnectorSpec, config: dict) -> bool:
    """Check if any of the connector's config sections are enabled."""
    for section in spec.config_sections:
        if section in config and config[section].get("enabled"):
            return True
    return False


def has_credentials(spec: ConnectorSpec, config: dict) -> bool:
    """Check if the credentials file exists for any config section."""
    for section in spec.config_sections:
        if section in config:
            creds_path = config[section].get("credentials_path")
            if creds_path and Path(os.path.expanduser(creds_path)).exists():
                return True
    return False


def get_status(spec: ConnectorSpec, config: dict) -> str:
    """Return user-facing status: 'not available', 'available', or 'installed'."""
    if not is_installed(spec):
        return "not available"
    if is_configured(spec, config):
        return "installed"
    return "available"


def resolve_hook(dotted_path: str):
    """Resolve a dotted path to a callable.

    Returns the callable, or None if *dotted_path* is empty.
    Raises ValueError on malformed paths, ImportError/AttributeError on bad paths.
    """
    if not dotted_path:
        return None
    if "." not in dotted_path:
        raise ValueError(f"Invalid hook path {dotted_path!r}: expected dotted path like 'module.func'")
    module_path, func_name = dotted_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


def resolve_check_auth(spec: ConnectorSpec, config: dict) -> str | None:
    """Resolve and call a connector's check_auth callable.

    Returns the auth status string (e.g. "authenticated", "expired"),
    None if no check_auth is configured, or "error" on failure.
    """
    if not spec.check_auth:
        return None
    try:
        module_path, func_name = spec.check_auth.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        fn = getattr(mod, func_name)
        return str(fn(config))
    except Exception:
        logger.warning("check_auth failed for connector %s", spec.name, exc_info=True)
        return "error"


def get_schema_specs(connectors: dict[str, ConnectorSpec] | None = None) -> list[ConnectorSpec]:
    """Return installed connector specs that declare schema extensions."""
    if connectors is None:
        connectors = discover_connectors()
    return [s for s in connectors.values() if is_installed(s) and s.schema_extensions]


def get_connector_pipes(connectors: dict[str, ConnectorSpec] | None = None) -> dict[str, type]:
    """Discover adapter classes from installed connectors.

    If *connectors* is provided, uses that dict; otherwise calls
    :func:`discover_connectors`. For installed connectors, imports their
    adapter modules and collects adapter classes via the explicit
    ``adapter_entries`` mapping. Returns ``{pipe_name: adapter_class}``.

    Connectors whose pip extra is NOT installed are skipped entirely —
    no import is attempted.
    """
    if connectors is None:
        connectors = discover_connectors()
    sources: dict[str, type] = {}
    for spec in connectors.values():
        if not is_installed(spec):
            continue
        for pipe_name, entry in spec.adapter_entries.items():
            module_path, class_name = entry.rsplit(":", 1)
            module = importlib.import_module(module_path)
            sources[pipe_name] = getattr(module, class_name)
    return sources
