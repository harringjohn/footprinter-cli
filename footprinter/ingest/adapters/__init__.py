"""Pipeline adapter types and concrete adapters.

Re-exports the core types and built-in source adapters:

    from footprinter.ingest.adapters import PipeAdapter, PipeResult
    from footprinter.ingest.adapters import LocalFoldersAdapter, BrowserAdapter

Connector adapters (Drive, Gmail) live in connectors/google/adapters/.
"""

from footprinter.ingest.adapters.browser import BrowserAdapter
from footprinter.ingest.adapters.chat import ChatAdapter
from footprinter.ingest.adapters.local_files import LocalFilesAdapter
from footprinter.ingest.adapters.local_folders import LocalFoldersAdapter
from footprinter.ingest.adapters.protocol import (
    ErrorType,
    PipeAdapter,
    PipeContext,
    PipeResult,
    PipeStatus,
)

__all__ = [
    "BrowserAdapter",
    "ChatAdapter",
    "PipeAdapter",
    "PipeContext",
    "ErrorType",
    "LocalFilesAdapter",
    "LocalFoldersAdapter",
    "PipeResult",
    "PipeStatus",
]
