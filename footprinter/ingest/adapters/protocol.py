"""Adapter protocol types for the pipeline refactor.

Defines the formal types that all pipe adapters implement:
- PipeStatus: enum of result statuses matching current orchestrator strings
- ErrorType: enum of error categories used for halt decisions
- PipeResult: typed replacement for ad-hoc result dicts
- PipeContext: typed runtime context replacing the convention-based config dict
- PipeAdapter: Protocol that all adapters implement
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from footprinter.ingest.database import Database


class PipeStatus(Enum):
    """Pipe result status.

    Values match the status strings in the current orchestrator result dicts.
    """

    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    SKIPPED = "skipped"
    ERROR = "error"
    INFO = "info"


class ErrorType(Enum):
    """Error categories for pipeline halt decisions.

    The orchestrator uses error_type to decide whether to halt the pipeline:
    database and config errors are fatal; missing_dependency and runtime are not.
    """

    MISSING_DEPENDENCY = "missing_dependency"
    DATABASE = "database"
    CONFIG = "config"
    RUNTIME = "runtime"


@dataclass
class PipeResult:
    """Typed result from a pipeline pipe.

    Replaces the ad-hoc Dict[str, Any] returned by orchestrator pipe methods.
    Factory classmethods reduce boilerplate in adapter implementations.
    """

    stage: str
    status: PipeStatus
    elapsed_seconds: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[ErrorType] = None

    # -- Factory classmethods --------------------------------------------------

    @classmethod
    def completed(cls, stage: str, **data: Any) -> PipeResult:
        """Create a result indicating the stage completed successfully."""
        return cls(stage=stage, status=PipeStatus.COMPLETED, data=data)

    @classmethod
    def completed_with_errors(cls, stage: str, error: str, **data: Any) -> PipeResult:
        """Create a result indicating the stage completed with non-fatal errors."""
        return cls(
            stage=stage,
            status=PipeStatus.COMPLETED_WITH_ERRORS,
            data=data,
            error=error,
        )

    @classmethod
    def skipped(cls, stage: str, reason: str, **data: Any) -> PipeResult:
        """Create a result indicating the stage was skipped."""
        return cls(
            stage=stage,
            status=PipeStatus.SKIPPED,
            data={"reason": reason, **data},
        )

    @classmethod
    def make_error(
        cls,
        stage: str,
        error: str,
        error_type: Optional[ErrorType] = None,
        **data: Any,
    ) -> PipeResult:
        """Create a result indicating the stage failed with an error."""
        return cls(
            stage=stage,
            status=PipeStatus.ERROR,
            data=data,
            error=error,
            error_type=error_type,
        )

    @classmethod
    def info(cls, stage: str, **data: Any) -> PipeResult:
        """Create an informational result (no processing occurred)."""
        return cls(stage=stage, status=PipeStatus.INFO, data=data)

    # -- Serialization ---------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Flatten to the dict shape expected by the orchestrator.

        Data keys are spread to the top level first, then reserved fields
        overlay them so an adapter can't accidentally clobber stage/status.
        """
        result = {**self.data}
        result["stage"] = self.stage
        result["status"] = self.status.value
        result["elapsed_seconds"] = self.elapsed_seconds
        if self.error is not None:
            result["error"] = self.error
        if self.error_type is not None:
            result["error_type"] = self.error_type.value
        return result


@dataclass
class PipeContext:
    """Typed runtime context passed to adapter.run().

    Replaces the convention-based Dict[str, Any] config parameter.
    """

    source_config: Dict[str, Any]
    config_path: str = ""
    full_mode: bool = False
    last_run: Optional[datetime] = None
    on_progress: Optional[Callable[[int], None]] = None
    # When set, overrides source_config["directories"] for adapters that scan
    # the local filesystem (local_folders, local_files). Lets
    # `fp setup folders add <path>` scope the scan to the new path only.
    # None means "use configured directories" (the fp ingest default).
    scan_roots: Optional[List[str]] = None


@runtime_checkable
class PipeAdapter(Protocol):
    """Protocol that all pipe adapters implement.

    Enables isinstance() validation in the adapter registry.
    Implementors can use either @property decorators or class attributes
    for the metadata fields.
    """

    @property
    def name(self) -> str:
        """Human-readable adapter name."""
        ...

    @property
    def pipe_name(self) -> str:
        """Pipe identifier used by the orchestrator."""
        ...

    @property
    def required_extras(self) -> List[str]:
        """Pip extras that must be installed for this adapter to run."""
        ...

    def run(self, db: Database, ctx: PipeContext) -> PipeResult:
        """Execute the adapter's pipe."""
        ...

    def status(self, db: Database) -> Dict[str, Any]:
        """Return current data counts and health for this pipe."""
        ...
