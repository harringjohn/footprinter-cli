"""Pipe runner — pipe dispatch, iteration, timing, error aggregation.

Runs pipes in order, delegates data-source pipes to adapters and
processing pipes to ProcessingPipeline.  Handles timing, error
classification, and fatal-error halting.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from footprinter.ingest.adapters.protocol import PipeContext

if TYPE_CHECKING:
    from footprinter.ingest.processing import ProcessingPipeline

logger = logging.getLogger(__name__)


class PipeRunner:
    """Runs pipes in order, handles timing, manages error aggregation.

    Receives its adapter registry, pipeline definitions, and valid pipe
    list from the orchestrator (composition root). Does not import pipe
    definitions directly.
    """

    def __init__(
        self,
        processing: ProcessingPipeline,
        get_db: Callable,
        config: Dict,
        config_path: str,
        adapter_registry: Dict[str, type],
        pipelines: Dict[str, List[str]],
        all_pipes: List[str],
        user_pipes: Optional[List[str]] = None,
        connector_pipe_map: Optional[Dict[str, str]] = None,
    ):
        self.processing = processing
        self._get_db = get_db
        self.config = config
        self.config_path = config_path
        self.adapter_registry = adapter_registry
        self.pipelines = pipelines
        self.all_pipes = all_pipes
        # User-selectable subset for error messages. Falls back to all_pipes
        # when omitted (legacy call sites) — error messages then show every
        # pipe including post-processing, as before.
        self.user_pipes = user_pipes if user_pipes is not None else all_pipes
        self._connector_pipe_map = connector_pipe_map or {}
        self.full_mode = False

    def run_pipe(
        self,
        pipe: str,
        on_progress: Optional[Callable] = None,
        last_run: Optional[datetime] = None,
    ) -> Dict:
        """Run a single pipe.

        Dispatches to the adapter registry for data-source pipes,
        or to ProcessingPipeline for processing pipes.  If the pipe
        belongs to an uninstalled connector, returns a skip result with
        install instructions.

        Returns:
            Dict with pipe results including elapsed_seconds.
        """
        logger.info(f"Running pipe: {pipe}")
        start_time = datetime.now()

        result = {"stage": pipe, "status": "unknown"}

        try:
            adapter_cls = self.adapter_registry.get(pipe)
            if adapter_cls is not None:
                adapter = adapter_cls()
                db = self._get_db()
                ctx = PipeContext(
                    source_config=self.config,
                    config_path=self.config_path,
                    full_mode=self.full_mode,
                    last_run=last_run,
                    on_progress=on_progress,
                )
                pipe_result = adapter.run(db, ctx)
                elapsed = (datetime.now() - start_time).total_seconds()
                pipe_result.elapsed_seconds = round(elapsed, 1)
                result = pipe_result.to_dict()
            elif self.processing.is_processing_pipe(pipe):
                db = self._get_db()
                pipe_result = self.processing.run_phase(pipe, db)
                elapsed = (datetime.now() - start_time).total_seconds()
                pipe_result.elapsed_seconds = round(elapsed, 1)
                result = pipe_result.to_dict()
            else:
                # Check if this pipe belongs to an uninstalled connector
                connector_name = self._find_connector_for_pipe(pipe)
                if connector_name:
                    result = {
                        "stage": pipe,
                        "status": "skipped",
                        "reason": "not installed",
                        "hint": f"run: fp connect install {connector_name}",
                    }
                else:
                    logger.error(f"Unknown pipe: {pipe}")
                    result = {
                        "stage": pipe,
                        "status": "error",
                        "error": f"Unknown pipe: {pipe}",
                    }

            result["stage"] = pipe
            result["status"] = result.get("status", "completed")

        except ImportError as e:
            logger.warning(f"Pipe {pipe} skipped — missing dependency: {e}")
            result = {
                "stage": pipe,
                "status": "skipped",
                "reason": f"Not installed: {e}",
                "error_type": "missing_dependency",
            }
        except sqlite3.OperationalError as e:
            logger.error(f"Database error in pipe {pipe}: {e}")
            result = {
                "stage": pipe,
                "status": "error",
                "error": str(e),
                "error_type": "database",
            }
        except FileNotFoundError as e:
            logger.error(f"Config/file error in pipe {pipe}: {e}")
            result = {
                "stage": pipe,
                "status": "error",
                "error": str(e),
                "error_type": "config",
            }
        # Intentional broad catch: last-resort after specific
        # ImportError, OperationalError, FileNotFoundError handlers
        except Exception as e:
            logger.error(f"Error in pipe {pipe}: {e}")
            result = {
                "stage": pipe,
                "status": "error",
                "error": str(e),
                "error_type": "runtime",
            }

        elapsed = (datetime.now() - start_time).total_seconds()
        result["elapsed_seconds"] = round(elapsed, 1)
        logger.info(f"Pipe {pipe} completed in {elapsed:.1f}s")

        return result

    def validate_pipes(self, pipes: List[str]) -> None:
        """Raise ValueError for unknown pipe names. Pure check, no side effects.

        Exposed so callers that need to fail before starting UI output
        (progress bars, headers) can pre-flight without duplicating the
        unknown-pipe rule.
        """
        unknown = [s for s in pipes if s not in self.all_pipes]
        if unknown:
            raise ValueError(
                f"Unknown pipe(s): {', '.join(unknown)}. "
                f"Valid pipes: {', '.join(self.user_pipes)}"
            )

    def run_pipes(
        self,
        pipes: List[str],
        on_pipe_start: Optional[Callable] = None,
        on_pipe_end: Optional[Callable] = None,
        on_progress: Optional[Callable] = None,
        pipe_hook: Optional[Callable] = None,
        last_run: Optional[datetime] = None,
    ) -> List[Dict]:
        """Run multiple pipes in order.

        Raises ValueError for unknown pipe names. Stops on fatal errors
        (database/config error_type), continues on runtime errors.
        """
        self.validate_pipes(pipes)

        results = []

        for pipe in pipes:
            if on_pipe_start:
                on_pipe_start(pipe)

            if pipe_hook:
                result = pipe_hook(pipe, on_progress=on_progress)
            else:
                result = self.run_pipe(pipe, on_progress=on_progress, last_run=last_run)
            results.append(result)

            if on_pipe_end:
                on_pipe_end(pipe, result)

            # Stop pipeline on fatal errors (database/config); runtime errors continue
            if result.get("status") == "error":
                if result.get("error_type") in ("database", "config"):
                    logger.error(f"Fatal error in {pipe}: {result.get('error', 'unknown')}")
                    break

        return results

    def _find_connector_for_pipe(self, pipe: str) -> str | None:
        """Find the connector name that owns a given pipe, if any."""
        return self._connector_pipe_map.get(pipe)
